"""Run the configured new-tab action through the pinned Chromium toolbar methods."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_homepage import homepage

FIXTURES = Path(__file__).resolve().parent / 'fixtures/homepage'
TOOLBAR_DIR = Path('chrome/browser/ui/android/toolbar/java/src/org/chromium/chrome/browser/toolbar')
BASE_PROVIDER = TOOLBAR_DIR / 'optional_button/BaseButtonDataProvider.java'
BROWSING_BUTTONS = TOOLBAR_DIR / 'top/OptionalBrowsingModeButtonController.java'
TAB_CREATOR_UTIL = Path('chrome/browser/tabmodel/android/java/src/org/chromium/chrome/browser/tabmodel/TabCreatorUtil.java')
BASE_SIGNATURE = '    protected boolean shouldShowButton(@Nullable Tab tab) {'
LAUNCH_SIGNATURE = '    public static void launchNtp(TabCreator tabCreator) {'
PHONE_SIGNATURES = (
    '    private static boolean isNtpVisualState(@VisualState int visualState) {',
    '    private boolean shouldShowOptionalButtonInLocationBar() {',
    homepage.PHONE_SHORTCUT_SIGNATURE,
    '    protected void updateOptionalButton(@Nullable ButtonData buttonData) {',
)
NEW_TAB_SIGNATURES = (homepage.NEW_TAB_BUTTON_SIGNATURE, '    public void onClick(View view) {')
DISPATCH_SIGNATURES = (
    '    private void showHighestPrecedenceOptionalButton() {',
    '    private void setCurrentOptionalButton(ButtonDataProvider provider, ButtonData buttonData) {',
    '    private void hideCurrentOptionalButton() {',
    '    private void updateCurrentOptionalButton(ButtonDataProvider provider) {',
)


def verify_shortcut_sources(read):
    for path, fixture, signatures in (
        (homepage.TOOLBAR_PHONE, 'toolbar_shortcut.java.in', PHONE_SIGNATURES),
        (homepage.NEW_TAB_BUTTON, 'new_tab_button.java.in', NEW_TAB_SIGNATURES),
        (BASE_PROVIDER, 'toolbar_shortcut_dispatch.java.in', (BASE_SIGNATURE,)),
        (BROWSING_BUTTONS, 'toolbar_shortcut_dispatch.java.in', DISPATCH_SIGNATURES),
        (TAB_CREATOR_UTIL, 'toolbar_shortcut_dispatch.java.in', (LAUNCH_SIGNATURE,)),
    ):
        source = read(path)
        if path == homepage.TOOLBAR_PHONE:
            source = source.replace(homepage.PHONE_SHORTCUT_AFTER, homepage.PHONE_SHORTCUT_BEFORE, 1)
        elif path == homepage.NEW_TAB_BUTTON:
            source = source.replace(homepage.NEW_TAB_BUTTON_AFTER, homepage.NEW_TAB_BUTTON_BEFORE, 1)
        expected = (FIXTURES / fixture).read_text()
        for signature in signatures:
            if homepage.method_source(source, signature) != homepage.method_source(expected, signature):
                raise ValueError(f'Chromium toolbar shortcut changed; review {path}: {signature.strip()}')


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
class HomepageShortcutTests(unittest.TestCase):
    def test_configured_new_tab_action_stays_outside_the_hidden_omnibox(self):
        provider = (FIXTURES / 'new_tab_button.java.in').read_text()
        phone = homepage.PHONE_SHORTCUT_IMPORT_BEFORE + '\n' + (
            FIXTURES / 'toolbar_shortcut.java.in').read_text()
        dispatch = (FIXTURES / 'toolbar_shortcut_dispatch.java.in').read_text()
        variants = {
            'original': (provider, phone),
            'provider_only': (homepage.patch_new_tab_button(provider), phone),
            'placement_only': (provider, homepage.patch_phone_shortcut(phone)),
            'fixed': (homepage.patch_new_tab_button(provider), homepage.patch_phone_shortcut(phone)),
        }
        for variant, (provider_source, phone_source) in variants.items():
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = (FIXTURES / 'ToolbarShortcutFlow.java.in').read_text()
                for marker, value, signatures in (
                    ('ARGON_BASE_PROVIDER_METHOD', dispatch, (BASE_SIGNATURE,)),
                    ('ARGON_LAUNCH_METHOD', dispatch, (LAUNCH_SIGNATURE,)),
                    ('ARGON_NEW_TAB_METHODS', provider_source, NEW_TAB_SIGNATURES),
                    ('ARGON_PHONE_METHODS', phone_source, PHONE_SIGNATURES),
                    ('ARGON_DISPATCH_METHODS', dispatch, DISPATCH_SIGNATURES),
                ):
                    source = source.replace(marker, '\n\n'.join(
                        homepage.method_source(value, signature) for signature in signatures))
                target = root / 'ToolbarShortcutFlow.java'
                target.write_text(source)
                compiled = subprocess.run(['javac', '--release', '17', '-d', str(root), str(target)],
                                          capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run(['java', '-ea', '-cp', str(root), 'ToolbarShortcutFlow'],
                                        capture_output=True, text=True)
                if variant == 'fixed':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('PASS: configured +', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    expected = ('Selected + must be in the visible toolbar' if variant == 'provider_only'
                                else 'Selected new-tab action must be eligible on a phone homepage')
                    self.assertIn(expected, result.stderr)
