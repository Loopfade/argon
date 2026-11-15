"""Exercise branch dispatch, exact-source provenance and main cache execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_ci_cache import ci_module
from test_full_build import pipeline

ROOT = Path(__file__).resolve().parents[1]
branch = ci_module("branch-test")


class BranchTestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sha = "a" * 40
        self.main_sha = "b" * 40
        self.env = {
            "GITHUB_REPOSITORY": "Loopfade/argon", "GITHUB_REPOSITORY_OWNER": "Loopfade",
            "GITHUB_REF": "refs/heads/topic/ua", "GITHUB_REF_TYPE": "branch",
            "GITHUB_RUN_ID": "123", "GITHUB_SHA": self.sha,
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            "TEST_RUNNER": "ubuntu-24.04", "TEST_AUTO_CONTINUE": "true",
            "TEST_SOURCE_RUN": "123", "BUILD_SOURCE_SHA": self.sha,
            "ARGON_STANDALONE_TEST": "true", "SIGNING_MODE": "test", "TARGET_ARCH": "arm64",
            "JAVA_HOME": "/fixture/jdk", "ANDROID_HOME": "/fixture/sdk",
        }
        self.run = {
            "id": 123, "path": branch.ENTRY, "event": "workflow_dispatch", "run_attempt": 1,
            "repository": {"full_name": "Loopfade/argon"},
            "head_repository": {"full_name": "Loopfade/argon"},
            "head_branch": "topic/ua", "head_sha": self.sha,
        }
        self.writes = []

    def api(self, path, payload=None):
        if payload is not None:
            self.writes.append((path, payload))
            return None
        if path == "actions/runs/123":
            return copy.deepcopy(self.run)
        if path == "actions/runs/123/attempts/1/jobs?per_page=100":
            return {"jobs": [{"steps": [{"name": "Отклонить запуск из main или тега",
                                         "conclusion": "success"}]}]}
        if path.startswith("actions/workflows/build-test-main.yml/runs?"):
            return {"workflow_runs": [{"id": 456, "display_title": "Argon Test — исходный запуск №123",
                                       "html_url": "https://github.com/Loopfade/argon/actions/runs/456"}]}
        self.fail(path)

    def test_dispatch_runs_main_service_without_release_or_source_selection_inputs(self):
        with patch.dict(os.environ, self.env), patch.object(branch, "api", side_effect=self.api):
            branch.dispatch(max_polls=1)
        self.assertEqual(self.writes, [("actions/workflows/build-test-main.yml/dispatches", {
            "ref": "main", "inputs": {"source_run": "123", "runner": "ubuntu-24.04",
                                        "auto_continue": "true"}})])
        summary = (self.root / "summary").read_text()
        self.assertIn(self.sha, summary)
        self.assertIn("actions/runs/456", summary)

    def test_main_and_tags_cannot_dispatch(self):
        for ref, kind in (("refs/heads/main", "branch"), ("refs/tags/v1", "tag")):
            with self.subTest(ref=ref), patch.dict(os.environ, {**self.env, "GITHUB_REF": ref,
                                                             "GITHUB_REF_TYPE": kind}), \
                 patch.object(branch, "api") as api, self.assertRaises(ValueError):
                branch.dispatch(max_polls=1)
            api.assert_not_called()

    def test_source_uses_original_sha_even_after_branch_moves(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main",
                                    "GITHUB_SHA": self.main_sha}), \
             patch.object(branch, "api", side_effect=self.api):
            branch.source()
        self.assertEqual((self.root / "output").read_text(), f"sha={self.sha}\n")
        self.assertIn("refs/heads/main", (self.root / "summary").read_text())

    def test_source_requires_main_executor_and_successful_branch_guard(self):
        with patch.dict(os.environ, self.env), patch.object(branch, "api") as api, \
             self.assertRaises(ValueError):
            branch.source()
        api.assert_not_called()
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch, "api", side_effect=[self.run, {"jobs": []}]), \
             self.assertRaisesRegex(ValueError, "guard"):
            branch.source()

    def test_unrelated_or_main_source_runs_are_rejected(self):
        for changes in ({"head_branch": "main"}, {"head_sha": "bad"},
                        {"path": ".github/workflows/build-all.yml"}, {"event": "pull_request"},
                        {"head_repository": {"full_name": "other/repo"}}):
            with self.subTest(changes=changes), patch.dict(os.environ, self.env), \
                 patch.object(branch, "api", return_value={**self.run, **changes}), \
                 self.assertRaises(ValueError):
                branch.source_run("123")

    def test_plan_uses_branch_sha_and_leaves_main_cache_context(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main",
                                    "GITHUB_SHA": self.main_sha}), \
             patch.object(branch.subprocess, "check_output", return_value=self.sha + "\n"), \
             patch.object(branch, "module", return_value=pipeline), \
             patch.object(pipeline, "reusable_artifact") as reuse:
            branch.plan()
            self.assertEqual(os.environ["GITHUB_SHA"], self.main_sha)
            self.assertEqual(os.environ["GITHUB_REF"], "refs/heads/main")
        reuse.assert_not_called()
        values = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        self.assertEqual(values["certificate"], "temporary-test-key")
        builds = json.loads(values["matrix"])["include"]
        self.assertEqual([item["arch"] for item in builds], ["arm64", "arm"])
        for item in builds:
            self.assertEqual(item["fingerprint"], pipeline.artifact_fingerprint(
                self.sha, item["arch"], "test", "temporary-test-key", values["prepared_inputs"], True))

    def test_changed_checkout_and_release_source_override_fail_before_build(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch.subprocess, "check_output", return_value=self.main_sha), \
             self.assertRaisesRegex(ValueError, "Checkout changed"):
            branch.check_checkout()
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             self.assertRaisesRegex(ValueError, "test build"):
            pipeline.plan("release", source_sha=self.sha)

    def image_modules(self):
        return [types.SimpleNamespace(image_fingerprint=lambda: "expected", IMAGE_LABEL="io.argon.prepared-inputs"),
                types.SimpleNamespace(resolve=lambda *args, **kwargs: "ghcr.io/loopfade/argon-build@sha256:" + "c" * 64)]

    def test_matching_main_image_needs_no_build_or_push(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch, "module", side_effect=self.image_modules()), \
             patch.object(branch.subprocess, "check_output", side_effect=[self.sha,
                 json.dumps({"config": {"Labels": {"io.argon.prepared-inputs": "expected"}}})]), \
             patch.object(branch.subprocess, "run") as build:
            branch.prepare_image()
        build.assert_not_called()
        self.assertIn("ghcr.io/loopfade/argon-build@sha256:", (self.root / "output").read_text())

    def test_changed_overlay_builds_local_image_using_main_layers_without_registry_write(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch, "module", side_effect=self.image_modules()), \
             patch.object(branch.subprocess, "check_output", side_effect=[self.sha,
                 json.dumps({"config": {"Labels": {"io.argon.prepared-inputs": "old"}}}),
                 "sha256:" + "d" * 64]), patch.object(branch.subprocess, "run") as commands:
            branch.prepare_image()
        argv = commands.call_args_list[0].args[0]
        self.assertIn("--load", argv)
        self.assertIn("type=registry,ref=ghcr.io/loopfade/argon-build:branch-main", argv)
        self.assertIn("ARGON_REVISION=" + self.sha, argv)
        self.assertNotIn("--push", argv)
        self.assertNotIn("--cache-to", argv)
        self.assertIn("local@sha256:", (self.root / "output").read_text())

    def test_new_workflow_has_no_release_key_or_publisher_and_uses_main_cache(self):
        entry = (ROOT / branch.ENTRY).read_text()
        worker = (ROOT / branch.WORKER).read_text()
        self.assertNotIn("signing:", entry)
        self.assertNotIn("secrets:", entry + worker)
        self.assertIn("      signing: test", worker)
        self.assertIn("      standalone_test: true", worker)
        self.assertIn("      source_sha: ${{ needs.plan.outputs.sha }}", worker)
        self.assertIn("      local_image: true", worker)
        self.assertIn("  group: argon-full-${{ github.ref }}", worker)
        self.assertIn("  queue: max", worker)
        self.assertNotIn("publish-release", worker)
        self.assertNotIn("build-chromium-image", worker)
        shared = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", shared)
        self.assertIn('--replace-toolchain "$TARGET_ARCH"', shared)
        self.assertIn("export GITHUB_SHA=\"$SOURCE_SHA\"", shared)

    def test_provenance_shell_passes_branch_sha_into_the_signing_container(self):
        import sys
        import textwrap
        text = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        step = text.split("        id: provenance\n", 1)[1].split("\n      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        binary = self.root / "bin/docker"
        binary.parent.mkdir()
        binary.write_text(f"#!{sys.executable}\n" + '''import json,os,sys
from pathlib import Path
Path(os.environ["CAPTURE_DOCKER"]).write_text(json.dumps({"sha":os.environ["GITHUB_SHA"],"args":sys.argv[1:]}))
''')
        binary.chmod(0o755)
        captured = self.root / "docker.json"
        env = {**os.environ, **self.env, "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
               "SOURCE_SHA": self.sha, "GITHUB_SHA": self.main_sha, "CAPTURE_DOCKER": str(captured),
               "BUILD_CONTAINER_NAME": "argon-build", "BUILD_FINGERPRINT": "planned",
               "BUILD_CERTIFICATE": "temporary-test-key", "PREPARED_IMAGE": "local@sha256:example"}
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(captured.read_text())["sha"], self.sha)


if __name__ == "__main__":
    unittest.main()
