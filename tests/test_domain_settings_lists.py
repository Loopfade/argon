"""Regression for stale card backgrounds/corners after the third domain."""
from pathlib import Path
import shutil
import tempfile
import unittest

from domain_settings_lists import compile_lists, run_lists


@unittest.skipUnless(shutil.which("java"), "JDK required for domain-list styling regression")
class DomainSettingsListsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="argon-domain-lists-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = compile_lists(Path(cls.temporary.name))

    def test_all_domain_lists_keep_card_styles_when_growing_and_shrinking(self):
        for kind in ("certificates", "background", "external"):
            with self.subTest(kind=kind):
                result = run_lists(self.directory, kind)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_previous_implementation_reproduces_stale_card_styles(self):
        with tempfile.TemporaryDirectory(prefix="argon-domain-lists-before-") as directory:
            compile_lists(Path(directory), legacy=True)
            for kind in ("certificates", "background", "external"):
                with self.subTest(kind=kind):
                    result = run_lists(Path(directory), kind)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("stale containment snapshot", result.stderr)
