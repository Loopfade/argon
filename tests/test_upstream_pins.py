"""Reject internally inconsistent version bumps before preparing Chromium."""
import unittest

from check_upstream_patches import verify_pins


class UpstreamPinTests(unittest.TestCase):
    def test_mismatched_chromium_boringssl_and_titanium_pins_are_rejected(self):
        lock = {"chromium_version": "154.0.8037.57", "boringssl_commit": "a" * 40,
                "vanadium_commit": "b" * 40}
        version = "MAJOR=154\nMINOR=0\nBUILD=8037\nPATCH=57\n"
        deps = "vars = {'boringssl_revision': '" + "a" * 40 + "'}"
        tree = {"tree": [{"path": "vanadium", "type": "commit", "sha": "b" * 40}]}
        verify_pins(lock, version, deps, tree)
        for key, value, error in [
            ("chromium_version", "153.0.8010.52", "Chromium"),
            ("boringssl_commit", "c" * 40, "BoringSSL"),
            ("vanadium_commit", "d" * 40, "Vanadium"),
        ]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, error):
                verify_pins({**lock, key: value}, version, deps, tree)


if __name__ == "__main__":
    unittest.main()
