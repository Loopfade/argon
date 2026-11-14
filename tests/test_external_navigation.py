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
        self.assertIn("PASS: external navigation whole-site modes", run_flow(
            patch.USER_GUARD, patch.FALLBACK_GUARD, patch.MARKET_GUARD))

    def test_patch_is_idempotent_and_requires_reviewed_anchors(self):
        anchors = {
            patch.patch_delegate: "    /** Adds a window id to the intent, if necessary. */",
            patch.patch_handler: ('        targetIntent.removeExtra(EXTRA_BROWSER_FALLBACK_URL);\n'
                                 '        if (canLaunchExternalFallback) {\n'
                                 '        Uri.Builder builder = new Uri.Builder().scheme("market").authority("details");'),
            patch.patch_chrome_delegate: "    @Override\n    public boolean isApplicationInForeground() {",
            patch.patch_menu: ('    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
                               '        android:fragment="org.chromium.chrome.browser.homepage.settings.HomepageSettings"'),
            patch.patch_privacy_menu: patch.SECURITY_SECTION,
            patch.patch_java_sources: "chrome_java_ext_rel_path_sources = [",
            patch.patch_resources: "chrome_app_java_resources_ext_rel_path_sources = [",
        }
        for transform, anchor in anchors.items():
            with self.subTest(transform=transform.__name__):
                updated = transform(anchor)
                self.assertEqual(transform(updated), updated)
                with self.assertRaises(ValueError):
                    transform("changed upstream source")
                with self.assertRaises(ValueError):
                    transform(anchor + anchor)

    def test_link_opening_moves_to_security_without_a_duplicate_main_entry(self):
        main = ('<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android">\n'
                + patch.LEGACY_MENU_ENTRY
                + '    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
                  '        android:fragment="org.chromium.chrome.browser.homepage.settings.HomepageSettings" />\n'
                  '</PreferenceScreen>')
        updated = patch.patch_menu(main)
        self.assertNotIn('argon_external_navigation', updated)
        self.assertIn('HomepageSettings', updated)
        privacy = ('<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android" '
                   'xmlns:app="http://schemas.android.com/apk/res-auto">\n'
                   + patch.SECURITY_SECTION + '\n</PreferenceScreen>')
        updated = patch.patch_privacy_menu(privacy)
        android = '{http://schemas.android.com/apk/res/android}'
        rows = list(ET.fromstring(updated))
        self.assertEqual([row.get(android + 'key') for row in rows],
                         ['security_section', 'argon_external_navigation'])
        self.assertEqual(rows[1].get(android + 'fragment'),
                         'org.chromium.chrome.browser.externalnav.ArgonExternalNavigationSettings')
        self.assertIsNone(rows[1].get(android + 'order'))
        self.assertEqual(rows[1].get('{http://schemas.android.com/apk/res-auto}keywords'),
                         '@string/argon_external_search_keywords')
        self.assertEqual(patch.patch_privacy_menu(updated), updated)

    def test_moving_the_entry_validates_both_screens_before_writing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            originals = {}
            for path, transform in patch.TRANSFORMS.items():
                content = 'changed source' if transform is patch.patch_privacy_menu else {
                    patch.patch_delegate: '    /** Adds a window id to the intent, if necessary. */',
                    patch.patch_handler: ('        targetIntent.removeExtra(EXTRA_BROWSER_FALLBACK_URL);\n'
                                         '        if (canLaunchExternalFallback) {\n'
                                         '        Uri.Builder builder = new Uri.Builder().scheme("market").authority("details");'),
                    patch.patch_chrome_delegate: '    @Override\n    public boolean isApplicationInForeground() {',
                    patch.patch_menu: (patch.LEGACY_MENU_ENTRY + '    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
                                      '        android:fragment="org.chromium.chrome.browser.homepage.settings.HomepageSettings"'),
                    patch.patch_java_sources: 'chrome_java_ext_rel_path_sources = [',
                    patch.patch_resources: 'chrome_app_java_resources_ext_rel_path_sources = [',
                }[transform]
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                originals[path] = content
            with self.assertRaises(ValueError):
                patch.apply(root)
            self.assertEqual({path: (root / path).read_text() for path in originals}, originals)

    def test_resource_and_source_registration(self):
        resources = patch.patch_resources("chrome_app_java_resources_ext_rel_path_sources = [")
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

    def test_existing_overlays_and_new_registrations_remain_idempotent_together(self):
        import apply_scoped_ca as ca
        import apply_background_playback as background
        import apply_exit as explicit_exit
        java = ('chrome_java_ext_rel_path_sources = [\n'
                '  "java/src/org/chromium/chrome/browser/privacy/settings/PrivacySettingsExt.java",\n]')
        resources = ('chrome_app_java_resources_ext_rel_path_sources = [\n'
                     '  "java/res/xml/privacy_preferences_ext.xml",\n]')
        menu = ('    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
                '        android:fragment="org.chromium.chrome.browser.homepage.settings.HomepageSettings" />\n'
                '</PreferenceScreen>')
        def apply_all(values):
            java, resources, menu = values
            java = ca.patch_titanium_chrome_java_sources(java)
            java = background.patch_java_sources(java)
            java = explicit_exit.patch_java_sources(java)
            resources = ca.patch_titanium_chrome_resources(resources)
            resources = background.patch_resources(resources)
            resources = explicit_exit.patch_resources(resources)
            menu = background.patch_menu(menu)
            return (patch.patch_java_sources(java), patch.patch_resources(resources), patch.patch_menu(menu))
        once = apply_all((java, resources, menu))
        self.assertEqual(apply_all(once), once)
