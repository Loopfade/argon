"""Regression checks for immutable, provenance-bound release publishing."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preflight import validate_release_revision


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

    def test_release_workflow_never_replaces_existing_tags(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn("group: publish-release", workflow)
        self.assertNotIn("gh release delete", workflow)
        self.assertIn('git/ref/tags/$RELEASE_TAG', workflow)
        self.assertIn("refusing to replace an immutable release", workflow)
        self.assertIn("refusing to move an immutable release tag", workflow)

    def test_release_identity_is_derived_from_verified_metadata(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertNotIn("      tag:\n", workflow)
        self.assertNotIn("      target_sha:\n", workflow)
        self.assertNotIn("      title:\n", workflow)
        self.assertIn("metadata=release-assets/build-info.json", workflow)
        self.assertIn(
            'release_revision=$(jq -er '.inputs.argon_revision '
            '| select(type == "number")' "$metadata")',
            workflow,
        )
        self.assertIn('tag="v${version}-argon.${release_revision}"', workflow)
        self.assertIn('target_sha="$source_sha"', workflow)

    def test_manual_publish_is_bound_to_successful_main_build_run(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertIn('repos/$GH_REPO/actions/runs/$MANUAL_RUN_ID', workflow)
        self.assertIn('run_name=$(jq -er '.name' <<<"$run_json")', workflow)
        self.assertIn('[[ "$run_name" == "Build Argon" ]]', workflow)
        self.assertIn('[[ "$run_conclusion" == success ]]', workflow)
        self.assertIn('[[ "$run_head_branch" == main ]]', workflow)
        self.assertIn('[[ "$run_head_sha" == "$source_sha" ]]', workflow)
        self.assertIn("push|workflow_run|workflow_dispatch", workflow)

    def test_auto_publish_checks_current_main_twice(self):
        workflow = (ROOT / ".github/workflows/publish-release.yml").read_text()
        self.assertGreaterEqual(workflow.count("git/ref/heads/main"), 2)
        self.assertIn("Skipping stale Build Argon run", workflow)
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
