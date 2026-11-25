from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

import apply_user_agent as patch
from user_agent_tab_state_flow import FIXTURES, run_creator


class TabStateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_duplicates_and_automatic_state_persistence(self):
        creator = (FIXTURES / 'tab_creator.java.in').read_text()
        tab = patch.patch_tab_user_agent_state((FIXTURES / 'tab_user_agent_state.java.in').read_text())
        with self.assertRaisesRegex(ValueError, 'manual choice lost'):
            run_creator(creator, tab)
        result = patch.patch_tab_creator(creator)
        self.assertEqual(result, patch.patch_tab_creator(result))
        self.assertIn('PASS:', run_creator(result, tab))

    def test_changed_duplicate_builder_is_rejected(self):
        with self.assertRaises(ValueError):
            patch.patch_tab_creator('class ChangedCreator {}')
