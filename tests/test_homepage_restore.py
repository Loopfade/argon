"""Restore the saved home after a website, keeping per-frame PageState consistent."""
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import apply_homepage as homepage
from homepage_restore_flow import CONTROLLER, FIXTURES, run_restore, run_restored_home_load


class HomepageRestoreTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler required')
    def test_real_restore_reproduces_native_frame_then_repairs_it(self):
        source = (FIXTURES / 'serialized_navigation_builder.cc.in').read_text()
        with self.assertRaisesRegex(ValueError,
                                    'renderer still receives chrome-native://newtab/'):
            run_restore(source)
        patched = homepage.patch_session_restore(source)
        self.assertIn('PASS: restored NTP frame', run_restore(patched))

    def test_restore_patch_is_idempotent_and_rejects_changed_upstream(self):
        source = (FIXTURES / 'serialized_navigation_builder.cc.in').read_text()
        patched = homepage.patch_session_restore(source)
        self.assertEqual(patched, homepage.patch_session_restore(patched))
        with self.assertRaisesRegex(ValueError, 'Chromium method changed'):
            homepage.patch_session_restore(source.replace('entry->SetURL(rewritten_url);',
                                                         'entry->SetURL(GURL());'))

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler required')
    def test_built_in_home_reopens_in_place_after_session_restore(self):
        source = CONTROLLER.read_text()
        with self.assertRaisesRegex(ValueError, 'built-in restored home still reuses saved frame'):
            run_restored_home_load(source)
        patched = homepage.patch_restored_home_load(source)
        self.assertIn('PASS: restored built-in home replaces its entry',
                      run_restored_home_load(patched))
