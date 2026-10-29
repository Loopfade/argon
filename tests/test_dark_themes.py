"""Strict application, atomic validation and coexistence with Argon's JNI catalogs."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dark_themes', ROOT / 'scripts/apply_dark_themes.py')
dark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dark)


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def fixture(root):
    # The pinned-source integration test validates complete real files. These
    # fragments isolate the installer's failure and transaction behavior.
    for path, hunks in dark.sections(dark.PATCH.read_text()).items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('\n// unrelated source between hunks\n'.join(before for before, _ in hunks))
    for path, text in {
        dark.JAVA_CATALOG: 'sources = [\n  "java/src/org/chromium/chrome/browser/privacy/settings/PrivacySettingsExt.java",\n]\n',
        dark.JNI_CATALOG: 'if (is_android) {\n  generate_jni("chrome_jni_headers") {\n    sources = [\n    ]\n  }\n}\n',
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class DarkThemePatchTests(unittest.TestCase):
    def test_repeated_preparation_with_other_jni_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            dark.apply(root)
            catalog = root / dark.JNI_CATALOG
            catalog.write_text(catalog.read_text().replace('    sources = [', '    sources = [\n      "other/ArgonExitBridge.java",'))
            before = snapshot(root)
            dark.apply(root)
            self.assertEqual(before, snapshot(root))
            self.assertEqual((ROOT / 'chromium_overlay' / dark.JAVA).read_text(), (root / dark.JAVA).read_text())

    def test_source_or_catalog_drift_is_rejected_before_any_write(self):
        for path in (dark.INPUT_PATHS[-1], dark.JAVA_CATALOG, dark.JNI_CATALOG):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture(root)
                (root / path).write_text('changed upstream source\n')
                before = snapshot(root)
                with self.assertRaises(ValueError):
                    dark.apply(root)
                self.assertEqual(before, snapshot(root))

    def test_malformed_or_escaping_patch_is_rejected(self):
        for data in ('diff --git a/../escape b/../escape\n',
                     'diff --git a/file b/renamed\n',
                     'diff --git a/file b/file\n'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                dark.sections(data)


if __name__ == '__main__':
    unittest.main()
