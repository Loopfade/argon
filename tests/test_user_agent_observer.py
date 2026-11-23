"""Tab-scoped UA must override stale history flags without changing site policy."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import apply_user_agent as patch
from user_agent_observer_flow import FIXTURES, compile_observer, run_observer, verify_upstream


class UserAgentObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='argon-ua-observer-')
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        original = (FIXTURES / 'request_desktop_observer.cc.in').read_text()
        cls.source = patch.patch_request_desktop_observer(original)
        cls.binary = compile_observer(root, cls.source)

    def check_scenario(self, scenario):
        result = run_observer(self.binary, scenario)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stale_history_flags_in_both_modes_and_from_both_initiators(self):
        self.check_scenario('history')

    def test_skipping_site_rules_alone_reproduces_stale_history(self):
        # The earlier PR guard returned here without applying the tab's mode.
        old = self.source.replace(
            '      navigation_handle->SetIsOverridingUserAgent(\n'
            '          mode == TabModel::TabUserAgent::DESKTOP);\n', '', 1)
        self.assertNotEqual(old, self.source)
        with tempfile.TemporaryDirectory(prefix='argon-ua-observer-before-') as directory:
            binary = compile_observer(Path(directory), old)
            self.assertNotEqual(run_observer(binary, 'history').returncode, 0)

    def test_default_legacy_and_unattached_tabs_retain_site_rules(self):
        self.check_scenario('site_defaults')

    def test_subframe_navigation_is_unchanged(self):
        self.check_scenario('subframes')

    def test_pinned_source_verifier(self):
        self.assertIn('PASS:', verify_upstream(self.source))
        with self.assertRaises(ValueError):
            verify_upstream(self.source.replace('mode == TabModel::TabUserAgent::DESKTOP);',
                                               'mode == TabModel::TabUserAgent::MOBILE);'))
