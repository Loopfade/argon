"""Exercise branch dispatch, exact-source provenance and main cache execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

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

    def test_new_workflow_has_no_release_key_or_publisher_and_uses_main_cache(self):
        entry = (ROOT / branch.ENTRY).read_text()
        worker = (ROOT / branch.WORKER).read_text()
        self.assertNotIn("signing:", entry)
        self.assertNotIn("secrets:", entry + worker)
        self.assertIn("      signing: test", worker)
        self.assertIn("      standalone_test: true", worker)
        self.assertIn("      source_sha: ${{ needs.plan.outputs.sha }}", worker)
        self.assertIn("  group: argon-full-${{ github.ref }}", worker)
        self.assertIn("  queue: max", worker)
        self.assertNotIn("publish-release", worker)
        self.assertIn("uses: ./.github/workflows/build-chromium-image.yml", worker)
        shared = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", shared)
        self.assertIn('--replace-toolchain "$TARGET_ARCH"', shared)
        self.assertIn("export GITHUB_SHA=\"$SOURCE_SHA\"", shared)

    def test_worker_prepares_one_shared_image_and_pins_it_for_both_architectures(self):
        text = (ROOT / branch.WORKER).read_text()
        image = text.split("\n  image:\n", 1)[1].split("\n  build:\n", 1)[0]
        build = text.split("\n  build:\n", 1)[1].split("\n  cleanup:\n", 1)[0]
        cleanup = text.split("\n  cleanup:\n", 1)[1]
        self.assertIn("uses: ./.github/workflows/build-chromium-image.yml", image)
        self.assertIn("source_sha: ${{ needs.plan.outputs.sha }}", image)
        self.assertNotIn("matrix:", image)
        self.assertIn("needs: [plan, image]", build)
        self.assertIn("matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}", build)
        self.assertIn("image: ${{ needs.image.outputs.image }}", build)
        self.assertNotIn("local_image", text)
        self.assertIn("packages: write", text)
        self.assertIn("needs.image.result == 'success'", cleanup)
        self.assertIn("prune-prepared-images.py --ignore-current-consumer", cleanup)

    def test_shared_image_checks_out_branch_sha_and_exports_main_buildkit_cache(self):
        image = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        self.assertIn("if: github.ref == 'refs/heads/main'", image)
        self.assertIn("ref: ${{ inputs.source_sha || github.sha }}", image)
        self.assertIn("org.opencontainers.image.revision=${{ inputs.source_sha || github.sha }}", image)
        self.assertIn("ARGON_REVISION=${{ inputs.source_sha || github.sha }}", image)
        self.assertIn('echo "tag=${image}:branch-main"', image)
        self.assertIn("push: true", image)
        self.assertIn("cache-from: type=registry,ref=${{ steps.image.outputs.tag }}", image)
        self.assertIn("cache-to: type=inline", image)
        self.assertIn("if: steps.existing.outputs.exists != 'true'", image)

    def test_resolved_digest_reuses_matching_image_or_requires_successful_publication(self):
        import textwrap
        text = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        step = text.split("        id: resolved\n", 1)[1].split("\n      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        old = "ghcr.io/loopfade/argon-build@sha256:" + "c" * 64
        new_digest = "sha256:" + "d" * 64
        for existing, built, expected in ((old, "", old), ("", new_digest,
                 "ghcr.io/loopfade/argon-build@" + new_digest), ("", "", None), ("", "invalid", None)):
            with self.subTest(existing=existing, built=built):
                output = self.root / "image-output"
                output.write_text("")
                env = {**os.environ, "EXISTING_IMAGE": existing, "BUILT_DIGEST": built,
                       "PREPARED_TAG": "ghcr.io/loopfade/argon-build:branch-main",
                       "GITHUB_OUTPUT": str(output)}
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                                        env=env, capture_output=True, text=True)
                if expected:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output.read_text(), f"image={expected}\n")
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output.read_text(), "")

    def test_merge_sha_does_not_invalidate_an_image_with_identical_inputs(self):
        with patch.dict(os.environ, {"GITHUB_SHA": self.sha, "GITHUB_REF": "refs/heads/topic/ua"}):
            branch_fingerprint = pipeline.image_fingerprint()
        with patch.dict(os.environ, {"GITHUB_SHA": self.main_sha, "GITHUB_REF": "refs/heads/main"}):
            self.assertEqual(branch_fingerprint, pipeline.image_fingerprint())

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
               "BUILD_CERTIFICATE": "temporary-test-key", "PREPARED_IMAGE": "ghcr.io/loopfade/argon-build@sha256:" + "c" * 64}
        result = subprocess.run(["bash", "-euo", "pipefail", "-c", script], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(captured.read_text())["sha"], self.sha)


if __name__ == "__main__":
    unittest.main()
