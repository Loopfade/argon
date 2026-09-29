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
        self.assertIn("verify-release", workflow)
        self.assertIn("verify-tag", workflow)
        self.assertIn("treating this run as successful", workflow)
        self.assertIn('release_url=$(gh release create', workflow)

    def test_release_identity_is_derived_from_verified_metadata(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertNotIn("      tag:\n", workflow)
        self.assertNotIn("      target_sha:\n", workflow)
        self.assertNotIn("      title:\n", workflow)
        self.assertIn("metadata=release-assets/build-info.json", workflow)
        self.assertIn(
            """release_revision=$(jq -er '.inputs.argon_revision | select(type == "number")' "$metadata")""",
            workflow,
        )
        self.assertIn('tag="v${version}-argon.${release_revision}"', workflow)
        self.assertIn('target_sha="$source_sha"', workflow)

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
