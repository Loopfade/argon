"""Run the production exit transaction and protect its integration and storage boundaries."""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import sys

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'chromium_overlay/titanium/chromium_src/chrome/android/java'
SOURCES = OVERLAY / 'src/org/chromium/chrome/browser/exit'
spec = importlib.util.spec_from_file_location('exit_patch', ROOT / 'scripts/apply_exit.py')
exit_patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exit_patch)
sys.path.insert(0, str(ROOT / 'scripts'))
import apply_scoped_ca as ca
import apply_background_playback as background


class ExitTests(unittest.TestCase):
    def test_build_registration_composes_with_existing_patches(self):
        cases = [
            ('  generate_jni("chrome_jni_headers") {\n    sources = [\n    ]\n  }',
             [ca.patch_chrome_android_gn, exit_patch.patch_jni]),
            ('  "java/src/org/chromium/chrome/browser/privacy/settings/PrivacySettingsExt.java",',
             [ca.patch_titanium_chrome_java_sources, background.patch_java_sources, exit_patch.patch_java_sources]),
            ('  "java/res/xml/privacy_preferences_ext.xml",',
             [ca.patch_titanium_chrome_resources, background.patch_resources, exit_patch.patch_resources]),
            ('android_cc_ext_full_path_sources = [',
             [ca.patch_titanium_android_cc_sources, exit_patch.patch_native_sources]),
            ('android_cc_ext_full_path_deps = [',
             [ca.patch_titanium_android_cc_deps, exit_patch.patch_native_deps]),
            ('<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android">\n</PreferenceScreen>',
             [ca.patch_titanium_privacy_preferences, exit_patch.patch_preferences]),
        ]
        for value, transforms in cases:
            with self.subTest(transforms=[x.__name__ for x in transforms]):
                for transform in transforms: value = transform(value)
                first = value
                for transform in transforms: value = transform(value)
                self.assertEqual(value, first)
    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_production_transaction(self):
        compiler = ['javac'] if shutil.which('javac') else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([*compiler, '-d', directory, str(SOURCES / 'ArgonExitFlow.java'),
                            str(ROOT / 'tests/java/ArgonExitFlowTest.java')],
                           check=True, capture_output=True, text=True)
            result = subprocess.run(['java', '-cp', directory,
                'org.chromium.chrome.browser.exit.ArgonExitFlowTest'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: exit ordering', result.stdout)

    def test_every_transform_rejects_an_unknown_upstream(self):
        for name, transform in exit_patch.TRANSFORMS.items():
            with self.subTest(source=name):
                with self.assertRaises(ValueError): transform('unreviewed source')

    def test_localized_resources_have_same_keys(self):
        english, russian = [ET.parse(OVERLAY / 'res' / folder / 'argon_exit_strings.xml').getroot()
                            for folder in ('values', 'values-ru')]
        self.assertEqual({x.attrib['name'] for x in english}, {x.attrib['name'] for x in russian})
        for tree in (english, russian):
            self.assertTrue(all(x.text for x in tree))

    def test_pending_journal_does_not_share_site_or_homepage_storage(self):
        store = (SOURCES / 'ArgonExitStore.java').read_text()
        self.assertNotIn('localStorage', store)
        self.assertNotIn('argon-start-v1', store)
        self.assertIn('"argon_exit", Context.MODE_PRIVATE', store)
        self.assertIn('.commit()', store)
        self.assertIn('snapshot_types', store)
        self.assertIn('snapshot_close_tabs', store)

    def test_build_wiring_covers_docker_and_prepared_fingerprint(self):
        prepare = (ROOT / 'scripts/prepare_chromium.sh').read_text()
        self.assertLess(prepare.index('scripts/apply_background_playback.py'), prepare.index('scripts/apply_exit.py'))
        self.assertIn('scripts/apply_exit.py', (ROOT / 'docker/chromium/Dockerfile').read_text())
        self.assertIn('scripts/apply_exit.py', (ROOT / '.github/ci/check-prepared-inputs.py').read_text())

    def test_exit_does_not_change_saved_background_rules(self):
        coordinator = (SOURCES / 'ArgonExitCoordinator.java').read_text()
        self.assertNotIn('putBoolean("enabled"', coordinator)
        self.assertNotIn('ArgonBackgroundPlaybackStore', coordinator)
        controller = (OVERLAY / 'src/org/chromium/chrome/browser/media/ArgonBackgroundPlaybackController.java').read_text()
        self.assertIn('stopForExit()', controller)
        self.assertIn('resumeAfterRecovery()', controller)

    def test_clear_uses_web_origin_mask_and_preserves_native_failure_result(self):
        native = (ROOT / 'chromium_overlay/chrome/browser/android/argon_exit.cc').read_text()
        self.assertIn('ORIGIN_TYPE_UNPROTECTED_WEB', native)
        self.assertNotIn('ORIGIN_TYPE_EXTENSION', native)
        self.assertNotIn('DATA_TYPE_BOOKMARKS', native)
        self.assertIn('base::Time(), base::Time::Max()', native)
        self.assertIn('failed_data_types', native)
        self.assertIn('CommitPendingWrite', native)
