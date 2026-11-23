"""Regression: switching UA mode must not reload the previous mode's scale."""
from pathlib import Path
import tempfile
import unittest

from test_user_agent_renderer import patched_fixture
from user_agent_scale_flow import compile_scale, run_scale
import apply_user_agent as patch


class UserAgentScaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="argon-ua-scale-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.sources = patched_fixture()
        cls.binary = compile_scale(Path(cls.temporary.name),
            cls.sources[patch.RENDER_FRAME], cls.sources[patch.DOCUMENT_STATE])

    def check_scenarios(self, *scenarios):
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result = run_scale(self.binary, scenario)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mode_switch_resets_copied_scale_in_both_directions(self):
        self.check_scenarios("switch", "redirect", "one_shot", "history_ua_change")

    def test_normal_reload_and_history_keep_user_zoom(self):
        self.check_scenarios("normal_reload", "history", "subframe", "remote")

    def test_history_without_saved_view_state_does_not_force_a_reset(self):
        self.check_scenarios("history_without_view_state")

    def test_abandoned_commit_and_cloned_document_do_not_reset_zoom(self):
        self.check_scenarios("cancel", "clone")

    def test_previous_implementation_reproduces_stale_scale(self):
        with tempfile.TemporaryDirectory(prefix="argon-ua-scale-before-") as directory:
            binary = compile_scale(Path(directory), self.sources[patch.RENDER_FRAME],
                                   self.sources[patch.DOCUMENT_STATE], legacy=True)
            for scenario in ("switch", "redirect"):
                with self.subTest(scenario=scenario):
                    result = run_scale(binary, scenario)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("f.page.viewport.scale == initial", result.stderr)
            # The old implementation already preserved normal reload/history zoom.
            for scenario in ("normal_reload", "history"):
                self.assertEqual(run_scale(binary, scenario).returncode, 0)
