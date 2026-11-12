"""Execute the pinned renderer-loss callback and its deferred SadTab task."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/exit'
spec = importlib.util.spec_from_file_location('exit_patch', ROOT / 'scripts/apply_exit.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def verify_sad_tab_source(source):
    spec = importlib.util.spec_from_file_location('homepage', ROOT / 'scripts/apply_homepage.py')
    homepage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(homepage)
    fixture = (FIXTURES / 'sad_tab_methods.java.in').read_text()
    for signature in ('    private void showSadTab(SadTab sadTab) {',
                      '        public void primaryMainFrameRenderProcessGone(@TerminationStatus int terminationStatus) {'):
        if homepage.method_source(source, signature) != homepage.method_source(fixture, signature):
            raise ValueError('Chromium SadTab callback changed; review exit suppression')


class ExitSadTabTests(unittest.TestCase):
    def test_patch_is_idempotent_and_rejects_changed_sources(self):
        original = (FIXTURES / 'sad_tab_methods.java.in').read_text()
        changed = patch.patch_tab_observer(original)
        self.assertEqual(changed, patch.patch_tab_observer(changed))
        with self.assertRaises(ValueError):
            patch.patch_tab_observer(original.replace('if (mTab.isHidden()', 'if (true'))

    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_real_renderer_loss_and_delayed_display(self):
        original = (FIXTURES / 'sad_tab_methods.java.in').read_text()
        harness = (FIXTURES / 'ExitSadTabFlow.java.in').read_text()
        # Add only Android service adapters; the branching and queued callback
        # under test come verbatim from the production patch on pinned Chromium.
        compiler = ['javac'] if shutil.which('javac') else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
        for patched in (False, True):
            with self.subTest(patched=patched), tempfile.TemporaryDirectory() as directory:
                methods = patch.patch_tab_observer(original) if patched else original
                methods = methods.replace(patch.PACKAGE, 'Gate').replace('@TerminationStatus ', '')
                source = harness.replace('// PRODUCTION_METHODS', methods)
                # Android Activity's getString is an environment dependency.
                source = source.replace('static final class Activity {}',
                                        'static final class Activity { String getString(int id) { return "help"; } }')
                path = Path(directory) / 'ExitSadTabFlow.java'
                path.write_text(source)
                compiled = subprocess.run([*compiler, '--release', '17', '-d', directory, str(path)],
                                          capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run(['java', '-cp', directory, 'ExitSadTabFlow'],
                                        capture_output=True, text=True)
                if patched:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('PASS: exit/recovery SadTab', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('intentional exit queued SadTab', result.stderr)
