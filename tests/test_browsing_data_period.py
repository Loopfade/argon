"""Exercise saved deletion periods and reject unreviewed Chromium upgrades."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_browsing_data_period as patch
import browsing_data_period_flow as flow
from browsing_data_period_flow import run_flow


class BrowsingDataPeriodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "tests/fixtures/browsing_data_period.json").read_text())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.src = Path(self.directory.name)
        for name, source in self.fixture["sources"].items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)

    def sources(self):
        return {str(path): (self.src / path).read_text() for path in patch.INPUT_PATHS}

    def test_fixture_is_tied_to_the_reviewed_chromium_revision(self):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        self.assertEqual(self.fixture["chromium_commit"], lock["chromium_commit"],
                         "Review the deletion-period patch and refresh the upstream fixture")

    def test_guarded_patch_is_idempotent(self):
        patch.apply(self.src)
        applied = self.sources()
        patch.apply(self.src)
        self.assertEqual(self.sources(), applied)

    def test_changed_upstream_method_stops_before_any_file_is_written(self):
        for path, signature in ((patch.DIALOG, patch.DIALOG_SIGNATURE),
                                (patch.MEDIATOR, patch.MEDIATOR_SIGNATURE),
                                (patch.FRAGMENT, patch.FRAGMENT_SIGNATURE)):
            with self.subTest(path=path):
                for name, source in self.fixture["sources"].items():
                    (self.src / name).write_text(source)
                target = self.src / path
                target.write_text(target.read_text().replace(
                    signature, signature + "\n        // Upstream implementation changed.", 1
                ))
                before = self.sources()
                with self.assertRaisesRegex(ValueError, "Chromium method changed"):
                    patch.apply(self.src)
                self.assertEqual(self.sources(), before)

    def test_modified_applied_patch_requires_review(self):
        patch.apply(self.src)
        target = self.src / patch.DIALOG
        target.write_text(target.read_text().replace("savedTimePeriod", "otherSavedTimePeriod"))
        before = self.sources()
        with self.assertRaisesRegex(ValueError, "Chromium method changed"):
            patch.apply(self.src)
        self.assertEqual(self.sources(), before)

    def test_duplicate_source_method_is_rejected(self):
        target = self.src / patch.MEDIATOR
        target.write_text(target.read_text() + patch.method_source(
            target.read_text(), patch.MEDIATOR_SIGNATURE
        ))
        with self.assertRaisesRegex(ValueError, "Expected one Chromium method"):
            patch.apply(self.src)

    def test_changed_full_screen_preference_reader_is_rejected_atomically(self):
        target = self.src / patch.FRAGMENT
        target.write_text(target.read_text().replace(
            "browsingDataBridge.getBrowsingDataDeletionTimePeriod()", "TimePeriod.LAST_HOUR"
        ))
        before = self.sources()
        with self.assertRaisesRegex(ValueError, "period reader changed"):
            patch.apply(self.src)
        self.assertEqual(self.sources(), before)

    def test_unmodified_chromium_reproduces_the_saved_period_regression(self):
        result = run_flow(self.sources(), self.fixture["time_utils"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("quick dialog should restore saved ALL_TIME", result.stderr)

    def test_production_methods_share_and_remember_all_periods(self):
        patch.apply(self.src)
        result = run_flow(self.sources(), self.fixture["time_utils"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: Chromium deletion periods", result.stdout)

    def test_prepared_jdk_runs_the_flow_without_java_or_javac_on_path(self):
        # Match the prepared-image environment: Chromium's JDK is installed,
        # but no system Java launcher or compiler is available on PATH.
        java = shutil.which("java")
        if not java:
            java = str(ROOT / "chromium/src/third_party/jdk/current/bin/java")
        prepared_java = self.src / "chromium/src/third_party/jdk/current/bin/java"
        prepared_java.parent.mkdir(parents=True)
        prepared_java.symlink_to(Path(java).resolve())
        template = self.src / "tests/fixtures/BrowsingDataPeriodFlow.java.in"
        template.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / template.relative_to(self.src), template)
        patch.apply(self.src)
        with mock_patch.object(flow, "ROOT", self.src), \
                mock_patch.object(flow.shutil, "which", return_value=None):
            result = run_flow(self.sources(), self.fixture["time_utils"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: Chromium deletion periods", result.stdout)

    def test_missing_jdk_fails_instead_of_skipping_the_regression(self):
        with mock_patch.object(flow, "ROOT", self.src), \
                mock_patch.object(flow.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "JDK required"):
                run_flow(self.sources(), self.fixture["time_utils"])


if __name__ == "__main__":
    unittest.main()
