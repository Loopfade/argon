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

    def test_manual_signing_labels_preserve_internal_signing_modes(self):
        for filename in ("build-all.yml", "build-all-no-publish.yml"):
            text = (ROOT / ".github/workflows" / filename).read_text()
            with self.subTest(workflow=filename):
                self.assertIn("options: [Релизная, Тестовая]", text)
                self.assertIn("default: Релизная", text)
                conversion = "${{ inputs.signing == 'Релизная' && 'release' || 'test' }}"
                self.assertIn("SIGNING_MODE: " + conversion, text)
                self.assertIn("      signing: " + conversion, text)


if __name__ == "__main__":
    unittest.main()
