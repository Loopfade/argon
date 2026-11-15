"""Regression checks for cross-workflow orchestration."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowWiringTests(unittest.TestCase):
    def test_dashboard_snapshot_dispatches_pages(self):
        updater = (ROOT / ".github/workflows/update-dashboard.yml").read_text()
        deploy = (ROOT / ".github/workflows/deploy-dashboard.yml").read_text()
        self.assertIn("actions: write", updater)
        self.assertIn("python3 .github/ci/update-dashboard.py --publish", updater)
        publisher = (ROOT / ".github/ci/update-dashboard.py").read_text()
        self.assertIn("deploy-dashboard.yml/dispatches", publisher)
        self.assertIn('{"ref": "main"}', publisher)
        self.assertIn("unsafe_release_payload_drift", publisher)
        self.assertIn("workflow_dispatch:", deploy)
        self.assertIn("'.github/workflows/deploy-dashboard.yml'", deploy)
        self.assertIn("include-hidden-files: true", deploy)

    def test_required_checks_run_for_every_pr_and_main_only_seeds_its_cache(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        trigger = validation.split("\npermissions:", 1)[0].split("\non:\n", 1)[1]
        self.assertEqual(re.findall(r"(?m)^  ([\w-]+):", trigger),
                         ["pull_request", "push", "workflow_dispatch"])
        self.assertIn("branches: [main]", trigger)
        pr_trigger = trigger.split("  push:\n", 1)[0]
        self.assertNotRegex(pr_trigger, r"(?m)^    paths(?:-ignore)?:")
        self.assertIn("synchronize", trigger)
        for job in ("validate", "domain-policy"):
            body = re.split(r"(?m)^  [\w-]+:", validation.split(f"\n  {job}:\n", 1)[1], maxsplit=1)[0]
            self.assertIn("github.event_name == 'pull_request' && github.event.action != 'closed'", body)
            if job == "validate":
                self.assertNotIn("github.event_name == 'push'", body)
            else:
                self.assertIn("github.ref == 'refs/heads/main'", body)
                self.assertIn("lookup-only: ${{ github.event_name != 'pull_request' }}", body)
                self.assertIn("steps.cache_plan.outputs.compile == 'true'", body)
                self.assertIn('prune-compiler-cache.py --validation "$CURRENT_CACHE_KEY"', body)

        # Every input that changes the shared key must trigger its producer.
        push_trigger = trigger.split("  push:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
        for path in ("build-lock.json", "args.gn", "scripts/configure_build.py",
                     ".github/ci/validation-scope.py", ".github/workflows/validate.yml"):
            self.assertIn(f"      - {path}\n", push_trigger)

    def test_cleanup_runner_is_only_for_closed_pr(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        cleanup = validation.split("\n  cleanup:\n", 1)[1].split("\n  validate:\n", 1)[0]
        self.assertIn(
            "if: github.event_name == 'pull_request' && "
            "github.event.action == 'closed' && "
            "github.event.pull_request.base.ref == 'main'",
            cleanup,
        )
        self.assertNotIn("github.event_name == 'push'", cleanup)

    def test_pages_reads_service_data_without_changing_protected_main(self):
        deploy = (ROOT / ".github/workflows/deploy-dashboard.yml").read_text()
        self.assertIn("branches: [dashboard]", deploy)
        self.assertNotIn("branches: [main]", deploy)
        self.assertIn("          ref: dashboard\n", deploy)
        self.assertIn("python3 .github/ci/update-dashboard.py --read-published", deploy)
        updater = (ROOT / ".github/workflows/update-dashboard.yml").read_text()
        self.assertIn("if: github.ref == 'refs/heads/main'", updater)
        self.assertIn("          ref: main\n", updater)

    def test_validation_caches_are_only_saved_and_pruned_by_main(self):
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        for step_id in ("snapshot", "save_cache"):
            block = next(block for block in validation.split("\n      - name: ")
                         if f"\n        id: {step_id}\n" in block)
            self.assertIn("if: steps.cache_plan.outputs.write == 'true'", block)
        prune = validation.split("\n      - name: Удалить старые версии и снимки", 1)[1]
        self.assertIn("if: steps.cache_plan.outputs.write == 'true'", prune)
        self.assertNotIn("legacy-cache-prefix", validation)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", validation)

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
