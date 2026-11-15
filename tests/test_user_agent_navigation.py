"""Regression tests for UA entry/document divergence and deferred selections."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_user_agent as patch
from user_agent_navigation_flow import ANDROID, FIXTURES, compile_flow, run_flow


class UserAgentNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="argon-ua-navigation-")
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        before = root / "before"
        after = root / "after"
        before.mkdir()
        after.mkdir()
        cls.source = (FIXTURES / ANDROID).read_text()
        cls.original = compile_flow(before, cls.source)
        cls.patched = compile_flow(after, patch.patch_navigation_controller(cls.source))

    def test_completed_cycles_and_initial_entry_guard(self):
        for binary in (self.original, self.patched):
            for scenario in ("cycles", "guards"):
                with self.subTest(binary=binary, scenario=scenario):
                    result = run_flow(binary, scenario)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_cancelled_reload_and_mode_set_without_reload(self):
        for scenario in ("retry", "no_reload"):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(run_flow(self.original, scenario).returncode, 0)
                result = run_flow(self.patched, scenario)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_latest_selection_supersedes_deferred_switch(self):
        for scenario in ("deferred", "superseded"):
            with self.subTest(scenario=scenario):
                self.assertNotEqual(run_flow(self.original, scenario).returncode, 0)
                result = run_flow(self.patched, scenario)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_patch_is_idempotent_and_rejects_missing_anchors(self):
        result = patch.patch_navigation_controller(self.source)
        self.assertEqual(result, patch.patch_navigation_controller(result))
        with self.assertRaises(ValueError):
            patch.patch_navigation_controller("")
