import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("patch", ROOT / "scripts/apply_scoped_ca.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class PatchTests(unittest.TestCase):
    def profile(self):
        return '#include "net/cert/asn1_util.h"\nvoid f() {\n' + patch.PROFILE_ANCHOR + '\n}\n'

    def test_idempotent(self):
        for transform, source in [
            (patch.patch_profile, self.profile()),
            (patch.patch_verifier, '#include "net/cert/time_conversions.h"\n' + patch.VERIFIER_ANCHOR),
            (patch.patch_gn, '    "cert/cert_verify_proc_builtin.h",'),
        ]:
            with self.subTest(transform=transform.__name__):
                value = transform(source)
                self.assertEqual(value, transform(value))

    def test_missing_or_duplicate_anchor_fails(self):
        for source in ["", self.profile() + self.profile()]:
            with self.assertRaises(ValueError):
                patch.patch_profile(source)

    def test_modified_constraint_cannot_be_accepted_as_already_patched(self):
        bad = patch.patch_profile(self.profile()).replace('".ru"', '".com"')
        with self.assertRaises(ValueError):
            patch.patch_profile(bad)

    def test_modified_ip_guard_cannot_be_accepted_as_already_patched(self):
        source = '#include "net/cert/time_conversions.h"\n' + patch.VERIFIER_ANCHOR
        bad = patch.patch_verifier(source).replace(
            '    CheckTitaniumRussianRootConstraints(path->certs, &path->errors);', '')
        with self.assertRaises(ValueError):
            patch.patch_verifier(bad)

    def test_root_header_has_reviewed_bytes(self):
        self.assertEqual(
            (ROOT / "chromium_overlay/net/cert/titanium_ru_root.h").read_text(),
            patch.root_header(patch.load_root()),
        )

    def test_modified_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "certificates").mkdir()
            for name in ["ministry-ca-lock.json", "russian_trusted_root_ca.pem"]:
                data = (ROOT / "certificates" / name).read_text()
                if name.endswith(".pem"):
                    lines = data.splitlines()
                    lines[1] = ("A" if lines[1][0] != "A" else "B") + lines[1][1:]
                    data = "\n".join(lines) + "\n"
                (directory / "certificates" / name).write_text(data)
            original = patch.ROOT
            try:
                patch.ROOT = directory
                with self.assertRaises(ValueError):
                    patch.load_root()
            finally:
                patch.ROOT = original


if __name__ == "__main__":
    unittest.main()
