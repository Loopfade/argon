"""Guard the Russian display contract without depending on a YAML package."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


class WorkflowLocalizationTests(unittest.TestCase):
    def test_all_workflow_job_step_and_input_labels_are_russian(self):
        paths = list((ROOT / ".github/workflows").glob("*.yml"))
        paths += list((ROOT / ".github/actions").rglob("action.yml"))
        for path in paths:
            text = path.read_text()
            with self.subTest(path=path.name):
                labels = re.findall(
                    r"(?m)^(?:name:|run-name:|    name:|\s+- name:|\s*description:) (.+)$", text)
                self.assertTrue(labels)
                for label in labels:
                    self.assertRegex(label, CYRILLIC, f"{path.name}: {label}")
                self.assertNotRegex(text, r"(?m)^\s+- uses:")
                if "\njobs:\n" in text:
                    jobs = re.split(r"(?m)^  [\w-]+:\n", text.split("\njobs:\n", 1)[1])[1:]
                    for job in jobs:
                        self.assertRegex(job, r"(?m)^    name: ")

    def test_dashboard_subscribes_to_the_current_build_workflow_name(self):
        workflows = ROOT / ".github/workflows"
        name = (workflows / "build-all.yml").read_text().splitlines()[0].removeprefix("name: ")
        updater = (workflows / "update-dashboard.yml").read_text()
        subscriptions = re.search(r"(?m)^    workflows: \[(.+)\]$", updater)[1]
        self.assertIn("'" + name + "'", subscriptions)

    def test_release_and_branch_test_use_one_release_key_with_separate_app_variants(self):
        workflows = ROOT / ".github/workflows"
        production = (workflows / "build-all.yml").read_text()
        test = (workflows / "build-test-main.yml").read_text()
        self.assertNotIn("inputs.signing", production)
        self.assertIn("SIGNING_MODE: release", production)
        self.assertIn("      signing: release", production)
        self.assertIn("      standalone_test: false", production)
        self.assertIn("      signing: release", test)
        self.assertIn("      standalone_test: true", test)
        for name in ("TITANIUM_RU_KEYSTORE_BASE64", "TITANIUM_RU_KEY_ALIAS"):
            self.assertIn("${{ secrets." + name + " }}", production)
            self.assertIn("${{ secrets." + name + " }}", test)


if __name__ == "__main__":
    unittest.main()
