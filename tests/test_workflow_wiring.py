"""Regression checks for cross-workflow orchestration."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowWiringTests(unittest.TestCase):
    def test_dashboard_snapshot_dispatches_pages(self):
        updater = (ROOT / ".github/workflows/update-dashboard.yml").read_text()
        deploy = (ROOT / ".github/workflows/deploy-dashboard.yml").read_text()
        self.assertIn("actions: write", updater)
        self.assertIn("deploy-dashboard.yml/dispatches", updater)
        self.assertIn("-f ref=main", updater)
        self.assertIn("check-release-payload-drift", updater)
        self.assertIn("workflow_dispatch:", deploy)
        self.assertIn("'.github/workflows/deploy-dashboard.yml'", deploy)
        self.assertIn("include-hidden-files: true", deploy)

    def test_dashboard_only_changes_are_ignored_by_validation(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        for path in (
            ".github/workflows/update-dashboard.yml",
            ".github/workflows/deploy-dashboard.yml",
            "index.html",
            "dashboard-data.json",
            ".nojekyll",
        ):
            self.assertGreaterEqual(validation.count(path), 2)

    def test_cleanup_runner_is_only_for_closed_pr(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        self.assertIn(
            "if: github.event_name == 'pull_request' && "
            "github.event.action == 'closed' && "
            "github.event.pull_request.base.ref == 'main'",
            validation,
        )
        self.assertNotIn("github.event_name == 'push' ||", validation)

    def test_prepared_image_compare_fails_closed_at_file_limit(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("file_count >= 300", build)
        self.assertIn("requiring a fresh image", build)

    def test_no_legacy_workflow_run_condition_in_image_job(self):
        image = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        self.assertNotIn("github.event_name != 'workflow_run'", image)


if __name__ == "__main__":
    unittest.main()
