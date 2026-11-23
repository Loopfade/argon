"""Exercise the single PR worker, pinned retries and main cache execution."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
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
            "GITHUB_REF": "refs/heads/main", "GITHUB_REF_TYPE": "branch",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "17", "GITHUB_SHA": self.main_sha,
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            "TEST_PR_NUMBER": "88", "TEST_SOURCE_FILE": str(self.root / "source.json"),
            "BUILD_SOURCE_SHA": self.sha,
            "ARGON_STANDALONE_TEST": "true", "SIGNING_MODE": "test", "TARGET_ARCH": "arm64",
            "JAVA_HOME": "/fixture/jdk", "ANDROID_HOME": "/fixture/sdk",
        }
        self.pr = {
            "number": 88, "state": "open",
            "base": {"ref": "main", "repo": {"full_name": "Loopfade/argon"}},
            "head": {"ref": "topic/ua", "sha": self.sha,
                     "repo": {"full_name": "Loopfade/argon"}},
        }

    def api(self, path):
        if path == "pulls/88":
            return copy.deepcopy(self.pr)
        self.fail(path)

    def test_source_resolves_pr_and_saves_its_sha_in_this_run(self):
        with patch.dict(os.environ, self.env), patch.object(branch, "api", side_effect=self.api) as api:
            branch.source()
        api.assert_called_once_with("pulls/88")
        self.assertEqual((self.root / "output").read_text(), f"sha={self.sha}\n")
        saved = json.loads((self.root / "source.json").read_text())
        self.assertEqual(saved["sha"], self.sha)
        self.assertEqual(saved["pr_number"], 88)
        self.assertEqual(saved["run_id"], 123)
        self.assertEqual(saved["run_number"], 17)
        summary = (self.root / "summary").read_text()
        for expected in (self.sha, "№88", "refs/heads/main", "Artifacts этого запуска"):
            self.assertIn(expected, summary)

    def test_branches_and_tags_cannot_use_the_main_worker(self):
        for ref in ("refs/heads/topic/ua", "refs/tags/main"):
            with self.subTest(ref=ref), patch.dict(os.environ, {**self.env, "GITHUB_REF": ref}), \
                 patch.object(branch, "api") as api, self.assertRaises(ValueError):
                branch.source()
            api.assert_not_called()

    def test_retries_keep_the_original_sha_without_resolving_the_pr_again(self):
        with patch.dict(os.environ, self.env), patch.object(branch, "api", side_effect=self.api):
            branch.source()
        saved = (self.root / "source.json").read_bytes()
        self.pr["head"]["sha"] = "c" * 40
        self.pr["state"] = "closed"
        with patch.dict(os.environ, {**self.env, "GITHUB_RUN_ATTEMPT": "2"}), \
             patch.object(branch, "api") as api:
            branch.source()
        api.assert_not_called()
        self.assertEqual((self.root / "source.json").read_bytes(), saved)
        self.assertEqual((self.root / "output").read_text(), f"sha={self.sha}\nsha={self.sha}\n")

    def test_missing_retry_snapshot_never_falls_back_to_a_new_pr_head(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_RUN_ATTEMPT": "2"}), \
             patch.object(branch, "module", return_value=pipeline), \
             patch.object(branch, "artifact_inventory", return_value=[]), \
             patch.object(branch, "api") as api, self.assertRaises(FileNotFoundError):
            branch.source()
        api.assert_not_called()

    def test_retry_snapshot_must_belong_to_the_same_run_pr_and_repository(self):
        with patch.dict(os.environ, self.env), patch.object(branch, "api", side_effect=self.api):
            branch.source()
        path = self.root / "source.json"
        saved = json.loads(path.read_text())
        for changes in ({"run_id": 124}, {"run_number": 18}, {"pr_number": 89},
                        {"repository": "other/repo"}, {"workflow": ".github/workflows/build-all.yml"},
                        {"schema": True}, {"sha": "bad"}, {"branch": "topic\nother"}):
            with self.subTest(changes=changes):
                path.write_text(json.dumps({**saved, **changes}))
                with patch.dict(os.environ, {**self.env, "GITHUB_RUN_ATTEMPT": "2"}), \
                     patch.object(branch, "api") as api, self.assertRaises(ValueError):
                    branch.source()
                api.assert_not_called()

    def test_only_open_main_prs_from_this_repository_are_accepted(self):
        for field, value in (("state", "closed"), ("number", 89),
                             ("base", {**self.pr["base"], "ref": "other"}),
                             ("base", {**self.pr["base"], "repo": {"full_name": "other/repo"}}),
                             ("head", {**self.pr["head"], "repo": {"full_name": "fork/argon"}}),
                             ("head", {**self.pr["head"], "repo": None}),
                             ("head", {**self.pr["head"], "ref": "main"}),
                             ("head", {**self.pr["head"], "sha": "invalid"})):
            with self.subTest(field=field, value=value), patch.dict(os.environ, self.env), \
                 patch.object(branch, "api", return_value={**self.pr, field: value}), self.assertRaises(ValueError):
                branch.source()
            self.assertFalse((self.root / "source.json").exists())

    def test_invalid_pr_numbers_are_rejected_before_api_access(self):
        for value in ("0", "-1", "01", "88\n", "88/other", ""):
            with self.subTest(value=value), patch.dict(os.environ, {**self.env, "TEST_PR_NUMBER": value}), \
                 patch.object(branch, "api") as api, self.assertRaises(ValueError):
                branch.source()
            api.assert_not_called()

    def test_api_failure_does_not_create_a_source_snapshot(self):
        with patch.dict(os.environ, self.env), \
             patch.object(branch, "api", side_effect=subprocess.CalledProcessError(1, "gh")), \
             self.assertRaises(subprocess.CalledProcessError):
            branch.source()
        self.assertFalse((self.root / "source.json").exists())

    def test_plan_uses_branch_sha_and_leaves_main_cache_context(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main",
                                    "GITHUB_SHA": self.main_sha}), \
             patch.object(branch.subprocess, "check_output", return_value=self.sha + "\n"), \
             patch.object(branch, "module", return_value=pipeline), \
             patch.object(pipeline, "release_certificate", return_value="c" * 64), \
             patch.object(pipeline, "reusable_artifact") as reuse:
            branch.plan()
            self.assertEqual(os.environ["GITHUB_SHA"], self.main_sha)
            self.assertEqual(os.environ["GITHUB_REF"], "refs/heads/main")
        reuse.assert_not_called()
        values = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        self.assertEqual(values["certificate"], "c" * 64)
        self.assertEqual(values["version_code"], "1000000017")
        builds = json.loads(values["matrix"])["include"]
        self.assertEqual([item["arch"] for item in builds], ["arm64", "arm"])
        for item in builds:
            self.assertEqual(item["fingerprint"], pipeline.artifact_fingerprint(
                self.sha, item["arch"], "release", "c" * 64, values["prepared_inputs"], True, 1000000017))

    def test_changed_checkout_and_release_source_override_fail_before_build(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch.subprocess, "check_output", return_value=self.main_sha), \
             self.assertRaisesRegex(ValueError, "Checkout changed"):
            branch.check_checkout()
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main", "ARGON_STANDALONE_TEST": "false"}), \
             self.assertRaisesRegex(ValueError, "test build"):
            pipeline.plan("release", source_sha=self.sha)

    def test_test_updates_keep_version_on_retry_and_increase_on_new_run(self):
        results = []
        for number in (17, 17, 18):
            with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_NUMBER": str(number)}), \
                 patch.object(branch.subprocess, "check_output", return_value=self.sha), \
                 patch.object(branch, "module", return_value=pipeline), \
                 patch.object(pipeline, "release_certificate", return_value="c" * 64):
                (self.root / "output").write_text("")
                branch.plan()
            output = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
            results.append((output["version_code"], output["fingerprint_arm64"]))
        self.assertEqual(results[0], results[1])
        self.assertLess(int(results[1][0]), int(results[2][0]))
        self.assertNotEqual(results[1][1], results[2][1])

    def test_missing_release_key_never_falls_back_to_temporary_test_key(self):
        with patch.dict(os.environ, {**self.env, "GITHUB_REF": "refs/heads/main"}), \
             patch.object(branch.subprocess, "check_output", return_value=self.sha), \
             patch.object(branch, "module", return_value=pipeline), \
             patch.object(pipeline, "release_certificate", side_effect=ValueError("release key unavailable")), \
             self.assertRaisesRegex(ValueError, "release key unavailable"):
            branch.plan()

    def test_staged_worker_uses_pinned_old_checkout_without_importing_its_legacy_helper(self):
        checkout = self.root / "legacy-checkout"
        ci = checkout / ".github/ci"
        ci.mkdir(parents=True)
        (ci / "branch-test.py").write_text('raise AssertionError("Legacy helper must not run")\n')
        (ci / "full-build.py").write_text('''import json, os
from pathlib import Path
def plan(mode, *, source_sha, reuse):
    Path(os.environ["PLAN_CAPTURE"]).write_text(json.dumps({
        "mode": mode, "sha": source_sha, "reuse": reuse,
        "version": os.environ["ARGON_TEST_VERSION_CODE"],
        "checkout": str(Path(__file__).resolve().parents[2]),
    }))
''')
        staged = self.root / "staged/argon-branch-test.py"
        staged.parent.mkdir()
        staged.write_bytes((ROOT / ".github/ci/branch-test.py").read_bytes())
        binary = self.root / "bin/git"
        binary.parent.mkdir()
        binary.write_text(f'#!{sys.executable}\nimport os\nprint(os.environ["PINNED_TEST_SHA"])\n')
        binary.chmod(0o755)
        capture = self.root / "plan-capture.json"
        env = {**os.environ, **self.env, "PINNED_TEST_SHA": self.sha,
               "PLAN_CAPTURE": str(capture),
               "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run([sys.executable, str(staged), "plan",
                                 "--checkout-root", str(checkout)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(capture.read_text()), {
            "mode": "release", "sha": self.sha, "reuse": False,
            "version": "1000000017", "checkout": str(checkout),
        })

    def test_single_worker_takes_pr_number_and_uses_release_key_without_publishing(self):
        worker = (ROOT / branch.WORKFLOW).read_text()
        self.assertFalse((ROOT / ".github/workflows/build-test-main.yml").exists())
        self.assertIn("      pr_number:", worker)
        self.assertNotIn("source_run", worker)
        self.assertNotIn("/dispatches", worker)
        self.assertNotIn("secrets: inherit", worker)
        self.assertIn("TITANIUM_RU_KEYSTORE_BASE64: ${{ secrets.TITANIUM_RU_KEYSTORE_BASE64 }}", worker)
        self.assertIn("      signing: release", worker)
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
        text = (ROOT / branch.WORKFLOW).read_text()
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
