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

    def test_domain_validator_tests_are_built_and_executed_in_ci(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        runner = (ROOT / ".github/ci/test-domain-policy.sh").read_text()
        target = (ROOT / "tests/domain_policy/BUILD.gn").read_text()
        self.assertIn("bash /workspace/.github/ci/test-domain-policy.sh", validation)
        self.assertIn("--root-target=//argon_tests:argon_domain_policy_tests", runner)
        self.assertIn("--gtest_filter=TitaniumRuDomainPolicyTest.*", runner)
        self.assertIn("titanium_ru_domain_policy_unittest.cc", target)
        for dependency in ("//net/base:registry_controlled_domains", "//url", "//base:i18n"):
            self.assertIn(dependency, target)

if __name__ == "__main__":
    unittest.main()
