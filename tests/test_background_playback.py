"""Run production timer logic and check integration/locale resource integrity without an APK."""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java"
POLICY = OVERLAY / "src/org/chromium/chrome/browser/media/ArgonBackgroundPlaybackPolicy.java"
spec = importlib.util.spec_from_file_location("background_patch", ROOT / "scripts/apply_background_playback.py")
background = importlib.util.module_from_spec(spec)
spec.loader.exec_module(background)


class BackgroundPlaybackTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required for Java policy test")
    def test_native_budgets_and_rule_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
            subprocess.run([*compiler, "-d", directory, str(POLICY),
                            str(ROOT / "tests/java/ArgonBackgroundPlaybackPolicyTest.java")],
                           check=True, capture_output=True, text=True)
            result = subprocess.run(["java", "-cp", directory,
                                    "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackPolicyTest"],
                                   capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: production playback budgets", result.stdout)

    def test_two_interface_languages_have_identical_keys_and_placeholders(self):
        catalogs = []
        for folder in ("values", "values-ru"):
            catalogs.append({node.attrib["name"]: node for node in ET.parse(
                OVERLAY / "res" / folder / "argon_background_playback_strings.xml").getroot()})
        self.assertEqual(catalogs[0].keys(), catalogs[1].keys())
        for name, english in catalogs[0].items():
            russian = catalogs[1][name]
            if english.tag == "plurals":
                for item in russian:
                    self.assertEqual(re.findall(r"%[\d$]*[ds]", item.text or ""), ["%d"], name)
            else:
                self.assertEqual(re.findall(r"%[\d$]*[ds]", english.text or ""),
                                 re.findall(r"%[\d$]*[ds]", russian.text or ""), name)

    def test_patch_anchor_changes_fail_instead_of_silently_breaking_the_build(self):
        for transform in (background.patch_activity, background.patch_tab_visibility, background.patch_menu,
                          background.patch_locales, background.patch_java_sources,
                          background.patch_resources, background.patch_android_packaging,
                          background.patch_paks, background.patch_apk_template,
                          background.patch_bundle_template, background.patch_native_locale_list):
            with self.subTest(transform=transform.__name__):
                with self.assertRaises(ValueError):
                    transform("unexpected upstream source")

    def test_activity_patch_upgrades_visibility_hooks_without_duplicate_attachment(self):
        original = "            super.finishNativeInitialization();\n    public void onDestroyInternal() {\n"
        updated = background.patch_activity(original)
        self.assertEqual(background.patch_activity(updated), updated)
        self.assertEqual(updated.count(background.PACKAGE + ".attach("), 1)
        legacy = original.replace("super.finishNativeInitialization();",
            "super.finishNativeInitialization();\n            " + background.PACKAGE
            + ".attach(this, assertNonNull(mTabModelSelector));")
        self.assertEqual(background.patch_activity(legacy), updated)
        self.assertIn("mRootUiCoordinator.getTabObscuringHandler()", updated)
        self.assertIn("getScrimVisibilitySupplier()", updated)

    def test_java_registration_upgrades_existing_overlay_without_duplicate_classes(self):
        anchor = '  "java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",'
        patched = background.patch_java_sources(anchor)
        bridge = '  "java/src/org/chromium/chrome/browser/tab/ArgonBackgroundPlaybackVisibility.java",'
        self.assertEqual(patched.count(bridge), 1)
        self.assertEqual(background.patch_java_sources(patched), patched)
        legacy = patched.replace("\n" + bridge, "")
        self.assertEqual(background.patch_java_sources(legacy), patched)
        sources = re.findall(r'"(java/src/[^"\n]*ArgonBackgroundPlayback[^"\n]*\.java)"', patched)
        self.assertEqual(len(sources), len(set(sources)))
        for source in sources:
            self.assertTrue((OVERLAY.parent / source).is_file(), source)

    def test_locale_patch_upgrades_prepared_sources_and_remains_idempotent(self):
        prefix = 'platform_pak_locales = [ "en-US", "ru", "de" ]\n'
        suffix = 'locales_without_pseudolocales = platform_pak_locales - pseudolocales\n'
        source = prefix + background.ANDROID_LOCALE_MAPPING + suffix
        patched = background.patch_locales(source)
        self.assertEqual(background.patch_locales(patched), patched)
        # Both published overlays overwrote the global catalogs. An already
        # prepared checkout must recover the original Android mapping as well.
        legacy = '''# ARGON_ANDROID_UI_LOCALES: package Russian and English only.
# Keep source catalogs and website/translation language support intact.
if (is_android) {
  platform_pak_locales = []
  platform_pak_locales = [ "en-US", "ru" ]
  extended_locales = []
  all_chrome_locales -= pseudolocales
  pseudolocales = []
  android_bundle_locales_as_resources = [ "ru" ]
}

'''
        for old in (legacy, legacy.replace('  platform_pak_locales = []\n', '')):
            with self.subTest(old=old):
                self.assertEqual(background.patch_locales(prefix + old + suffix), patched)
        self.assertTrue(patched.startswith(prefix + background.ANDROID_LOCALE_MAPPING))
        self.assertTrue(patched.endswith(suffix))
        self.assertNotIn('all_chrome_locales -= pseudolocales', patched)
        self.assertNotIn('extended_locales = []', patched)
        self.assertIn('argon_android_ui_locales = [ "en-US", "ru" ]', patched)
        self.assertEqual(patched.count('# ARGON_ANDROID_UI_LOCALES:'), 1)

    def test_changed_upstream_android_mapping_requires_review(self):
        source = (background.ANDROID_LOCALE_MAPPING.replace('"he",', '"new-locale",')
                  + 'locales_without_pseudolocales = platform_pak_locales - pseudolocales\n')
        with self.assertRaisesRegex(ValueError, 'mapping changed'):
            background.patch_locales(source)

    def test_native_visibility_patch_preserves_upstream_decision_and_is_idempotent(self):
        original = "class TabImpl {\n" + background.TAB_VISIBILITY_METHOD + "\n}"
        patched = background.patch_tab_visibility(original)
        self.assertEqual(background.patch_tab_visibility(patched), patched)
        self.assertEqual(patched.count("public void refreshBackgroundPlaybackVisibility()"), 1)
        for value in ("HIDDEN", "OCCLUDED", "VISIBLE"):
            self.assertEqual(patched.count(f".resolveVisibility(this, Visibility.{value})"), 1)
        self.assertIn("mIsHidden && !isOffscreenRendering", patched)
        self.assertIn("!mIsDetachedFromActivity", patched)
        self.assertIn("assumeNonNull(mWindowAndroid).getOcclusionSupplier().get()", patched)
        with self.assertRaises(ValueError):
            background.patch_tab_visibility(original.replace("!mIsDetachedFromActivity", "true"))

    def test_retired_script_is_unregistered_on_existing_prepared_catalog(self):
        anchor = '  "java/res/values-ru/argon_widget_strings.xml",'
        patched = background.patch_resources(anchor)
        legacy = patched + '\n  "java/res/raw/argon_background_playback.js",\n'
        self.assertEqual(background.patch_resources(legacy), patched + '\n')
        self.assertEqual(background.patch_resources(patched), patched)
        self.assertFalse((OVERLAY / "res/raw/argon_background_playback.js").exists())

    def test_overview_resource_upgrades_existing_prepared_catalog_once(self):
        anchor = '  "java/res/values-ru/argon_widget_strings.xml",'
        layout = '  "java/res/layout/argon_background_overview.xml",'
        expected = background.patch_resources(anchor)
        legacy = expected.replace('\n' + layout, '')
        self.assertEqual(background.patch_resources(legacy), expected)
        self.assertEqual(background.patch_resources(expected), expected)
        self.assertEqual(expected.count(layout), 1)
