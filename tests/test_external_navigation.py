"""Check the production external-navigation rules and fail-fast patch integration."""
from pathlib import Path
import re
import shutil
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_external_navigation as patch
from external_navigation_flow import OVERLAY, run_flow


class ExternalNavigationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_policy_native_settings_and_interception_flow(self):
        self.assertIn("PASS: external navigation whole-site modes", run_flow(patch.USER_GUARD))

    def test_patch_is_idempotent_and_requires_reviewed_anchors(self):
        anchors = {
            patch.patch_delegate: "    /** Adds a window id to the intent, if necessary. */",
            patch.patch_handler: "        targetIntent.removeExtra(EXTRA_BROWSER_FALLBACK_URL);\n",
            patch.patch_chrome_delegate: "    @Override\n    public boolean isApplicationInForeground() {",
            patch.patch_menu: "</PreferenceScreen>",
            patch.patch_java_sources: '  "java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",',
            patch.patch_resources: '  "java/res/values-ru/argon_widget_strings.xml",',
        }
        for transform, anchor in anchors.items():
            with self.subTest(transform=transform.__name__):
                updated = transform(anchor)
                self.assertEqual(transform(updated), updated)
                with self.assertRaises(ValueError):
                    transform("changed upstream source")
                with self.assertRaises(ValueError):
                    transform(anchor + anchor)

    def test_resource_and_source_registration(self):
        resources = patch.patch_resources('  "java/res/values-ru/argon_widget_strings.xml",')
        for relative in re.findall(r'"(java/res/[^"\n]+argon_external[^"\n]+)"', resources):
            self.assertTrue((OVERLAY.parent / relative).is_file(), relative)
        catalogs = [{n.attrib["name"]: n.text for n in ET.parse(
            OVERLAY / "res" / folder / "argon_external_navigation_strings.xml").getroot()}
                    for folder in ("values", "values-ru")]
        self.assertEqual(catalogs[0].keys(), catalogs[1].keys())
        for key in catalogs[0]:
            self.assertEqual(re.findall(r"%[\d$]*[ds]", catalogs[0][key]),
                             re.findall(r"%[\d$]*[ds]", catalogs[1][key]), key)
        self.assertIn("apply_external_navigation.py", (ROOT / "scripts/prepare_chromium.sh").read_text())
        self.assertIn("apply_external_navigation.py", (ROOT / ".github/ci/check-prepared-inputs.py").read_text())
