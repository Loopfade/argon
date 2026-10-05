"""Regression checks for immutable, provenance-bound release publishing."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preflight import validate_release_revision

POLICY_SPEC = importlib.util.spec_from_file_location(
    "release_policy", ROOT / "scripts/release_policy.py"
)
release_policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(release_policy)


class ReleasePublishingTests(unittest.TestCase):
    def test_release_revision_is_explicit_and_positive(self):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        self.assertEqual(validate_release_revision(lock), lock["argon_revision"])
        self.assertIs(type(lock["argon_revision"]), int)
        self.assertGreater(lock["argon_revision"], 0)

    def test_invalid_release_revisions_are_rejected(self):
        for value in (None, 0, -1, 1.5, "2", True):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "argon_revision"
            ):
                validate_release_revision({"argon_revision": value})

    def test_documentation_only_main_drift_keeps_verified_build_publishable(self):
        compare = {
            "status": "ahead",
            "files": [
                {"filename": "README.md"},
                {"filename": "docs/release-notes.md"},
            ],
        }
        self.assertEqual(release_policy.unsafe_main_drift(compare), [])

    def test_runtime_or_workflow_main_drift_makes_old_build_stale(self):
        for path in (
            "scripts/sign_and_verify.py",
            ".github/workflows/publish-release.yml",
            "build-lock.json",
        ):
            with self.subTest(path=path):
                compare = {"status": "ahead", "files": [{"filename": path}]}
                self.assertEqual(release_policy.unsafe_main_drift(compare), [path])

    def test_diverged_or_truncated_compare_is_never_treated_as_safe(self):
        self.assertTrue(release_policy.unsafe_main_drift({"status": "diverged", "files": []}))
        self.assertTrue(
            release_policy.unsafe_main_drift(
                {
                    "status": "ahead",
                    "files": [{"filename": "docs/x.md"}] * 300,
                }
            )
        )

    def test_nested_release_artifact_layout_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "arm64-v8a"
            payload.mkdir()
            apk = payload / "Argon-154.0.8037.57-release-arm64-v8a.apk"
            apk.write_bytes(b"apk")
            Path(f"{apk}.sha256").write_text("checksum\n")
            (payload / "build-info.json").write_text("{}\n")
            for name in release_policy.RELEASE_LICENSES:
                (payload / name).write_text("license\n")

            selected = release_policy.locate_release_artifact(root)
            self.assertEqual(selected["asset_dir"], payload)
            self.assertEqual(selected["apk"], apk)
            self.assertEqual(selected["metadata"], payload / "build-info.json")

    def test_duplicate_release_apks_are_rejected_across_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ("a", "b"):
                payload = root / directory
                payload.mkdir()
                (payload / "Argon-154.0.8037.57-release-arm64-v8a.apk").write_bytes(b"apk")

            with self.assertRaisesRegex(ValueError, "exactly one release arm64 APK"):
                release_policy.locate_release_artifact(root)

    def test_release_workflow_uses_recursive_artifact_locator(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn("--abi arm64-v8a", workflow)
        self.assertIn("--abi armeabi-v7a", workflow)
        self.assertNotIn("find release-assets -maxdepth 1", workflow)
        self.assertIn('cd "$arm64_dir"', workflow)
        self.assertIn('cd "$arm_dir"', workflow)

    def test_exact_existing_release_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            apk = assets / "Argon.apk"
            checksum = assets / "Argon.apk.sha256"
            apk.write_bytes(b"apk")
            checksum.write_text("checksum\n")
            expected = {}
            for path in (apk, checksum):
                expected[path.name] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

            release = {
                "draft": False,
                "target_commitish": "a" * 40,
                "assets": [
                    {"name": name, "digest": digest}
                    for name, digest in expected.items()
                ],
            }
            release_policy.verify_existing_release(release, "a" * 40, assets)

            bad = dict(release)
            bad["target_commitish"] = "b" * 40
            with self.assertRaisesRegex(ValueError, "different target"):
                release_policy.verify_existing_release(bad, "a" * 40, assets)

            bad_assets = dict(release)
            bad_assets["assets"] = [
                {"name": "Argon.apk", "digest": "sha256:" + "0" * 64},
                release["assets"][1],
            ]
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                release_policy.verify_existing_release(bad_assets, "a" * 40, assets)

    def test_existing_tag_is_reusable_only_for_same_verified_commit(self):
        tag = {"object": {"type": "commit", "sha": "a" * 40}}
        release_policy.verify_tag_ref(tag, "a" * 40)
        with self.assertRaisesRegex(ValueError, "does not point"):
            release_policy.verify_tag_ref(tag, "b" * 40)

    def test_release_workflow_is_immutable_and_idempotent(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn("group: publish-release", workflow)
        self.assertNotIn("gh release delete", workflow)
        self.assertNotIn("gh release view", workflow)
        self.assertIn("check-release-payload-drift", workflow)
        self.assertIn("verify-tag", workflow)
        self.assertIn("The exact armv7 assets are already attached", workflow)
        self.assertIn('release_url=$(gh release create', workflow)

    def test_release_identity_is_derived_from_verified_metadata(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertNotIn("      tag:\n", workflow)
        self.assertNotIn("      target_sha:\n", workflow)
        self.assertNotIn("      title:\n", workflow)
        self.assertIn("--abi arm64-v8a", workflow)
        self.assertIn("--abi armeabi-v7a", workflow)
        self.assertIn("arm64_metadata=$(jq -er '.metadata'", workflow)
        self.assertIn("arm_metadata=$(jq -er '.metadata'", workflow)
        self.assertIn(
            """release_revision=$(jq -er '.inputs.argon_revision | select(type == "number")' "$arm64_metadata")""",
            workflow,
        )
        self.assertIn('echo "tag=v${version}-${release_revision}"', workflow)
        self.assertNotIn("-argon.", workflow)
        self.assertIn('echo "target_sha=$source_sha"', workflow)
        self.assertIn('[[ "$source_sha" == "$arm_source_sha" ]] || {', workflow)

    def test_manual_publish_is_bound_to_successful_main_build_run(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn('repos/$GH_REPO/actions/runs/$MANUAL_RUN_ID', workflow)
        self.assertIn("""run_name=$(jq -er '.name' <<<"$run_json")""", workflow)
        self.assertIn('[[ "$run_name" == "Build Argon" ]]', workflow)
        self.assertIn('[[ "$run_conclusion" == success ]]', workflow)
        self.assertIn('[[ "$run_head_branch" == main ]]', workflow)
        self.assertIn('[[ "$run_head_sha" == "$source_sha" ]]', workflow)
        self.assertIn("push|workflow_run|workflow_dispatch", workflow)
        self.assertIn("Manual publication is only allowed from current main or across documentation-only drift.", workflow)

    def test_release_rechecks_main_drift_before_creation(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertGreaterEqual(workflow.count("git/ref/heads/main"), 2)
        self.assertGreaterEqual(workflow.count("scripts/release_policy.py check-drift"), 2)
        self.assertIn("Allowing publication across documentation-only main drift", workflow)
        self.assertIn("Skipping stale release", workflow)

    def test_prepared_image_workflow_does_not_loop_back_from_build_argon(self):
        image_workflow = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        trigger = image_workflow.split("\npermissions:", 1)[0]
        self.assertNotIn("workflow_run:", trigger)
        build_workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("workflows: [Build prepared Chromium image]", build_workflow)

    def test_publish_is_reusable_and_only_runs_after_successful_dual_arm_build(self):
        publisher = (ROOT / ".github/workflows/publish-release.yml").read_text()
        trigger = publisher.split("\npermissions:", 1)[0]
        self.assertIn("workflow_call:", trigger)
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("workflow_run:", trigger)

        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("needs.build.result == 'success'", build)
        self.assertIn("uses: ./.github/workflows/publish-release.yml", build)
        self.assertIn("run_id: ${{ github.run_id }}", build)
        self.assertIn("source_sha: ${{ github.sha }}", build)
        self.assertIn("contents: write", build)

        self.assertIn('[[ "$SOURCE_RUN_ID" == "$GITHUB_RUN_ID" ]]', publisher)
        self.assertIn('[[ "$EVENT_NAME" != workflow_dispatch ]]', publisher)
        self.assertIn("push|workflow_run) ;;", publisher)

    def test_release_policy_changes_trigger_a_fresh_dual_arm_build(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertGreaterEqual(
            workflow.count("'.github/workflows/publish-release.yml'"), 2
        )
        self.assertGreaterEqual(workflow.count("'scripts/release_policy.py'"), 2)

    def test_primary_build_runs_both_arm_architectures_in_parallel(self):
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("arch: [arm64, arm]", workflow)
        self.assertIn("matrix.arch", workflow)
        self.assertNotIn("One ABI per run", workflow)

    def test_release_contains_both_arm_apks_and_checksums(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn("pattern: Argon-*", workflow)
        self.assertIn("Expected exactly seven public release assets", workflow)
        self.assertIn("release-arm64-v8a.apk", workflow)
        self.assertIn("release-armeabi-v7a.apk", workflow)
        self.assertIn("## English", workflow)
        self.assertIn("## Русский", workflow)
        self.assertIn("Architectures:", workflow)
        self.assertIn("Архитектуры:", workflow)

    def test_dialog_validation_runs_after_vanadium_patch_application(self):
        checker = (ROOT / "tests/check_upstream_patches.py").read_text()
        self.assertIn("upstream_paths = (*BASE_PATHS, DIALOG_PATH)", checker)
        self.assertIn(
            "paths = (*BASE_PATHS, *EXTENSION_PATHS, DIALOG_PATH)", checker
        )
        validation = (
            "verify_extension_install_dialog((src / DIALOG_PATH).read_text())"
        )
        self.assertIn(validation, checker)
        self.assertLess(
            checker.index('subprocess.run(["git", "apply"'),
            checker.index(validation),
        )
        self.assertLess(checker.index(validation), checker.index("patch.apply(src"))


if __name__ == "__main__":
    unittest.main()
