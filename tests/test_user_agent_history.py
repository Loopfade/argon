from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import apply_user_agent as patch
from user_agent_history_flow import FIXTURES, run_history


class HistoryViewStateTests(unittest.TestCase):
    def test_target_mode_change_preserves_nonvisual_history(self):
        original = (FIXTURES / 'navigation_request_ua.cc.in').read_text()
        with self.assertRaises(ValueError):
            run_history(original)
        result = patch.patch_history_view_state(original)
        self.assertEqual(result, patch.patch_history_view_state(result))
        self.assertIn('PASS:', run_history(result))

    def test_changed_navigation_method_is_rejected(self):
        with self.assertRaises(ValueError):
            patch.patch_history_view_state('void ChangedNavigation() {}')
