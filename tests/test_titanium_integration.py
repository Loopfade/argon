"""Check the version gate and patch selection used by Titanium updates."""
import fnmatch
from pathlib import Path
import subprocess
import unittest

from check_upstream_patches import vanadium_patch_exclusions

ROOT = Path(__file__).resolve().parents[1]


class TitaniumIntegrationTests(unittest.TestCase):
    def test_developer_tools_version_gate_uses_the_m154_branch(self):
        for version, expected in (("154.0.8037.57", "older"),
                                  ("154.0.8037.126", "older"),
                                  ("156.0.8059.9", "older"),
                                  ("156.0.8060.0", "newer"),
                                  ("156.0.8061.1", "newer")):
            with self.subTest(version=version):
                result = subprocess.run(
                    ["bash", "-c", 'source "$1"; '
                     'if version_lt "$2" 156.0.8060.0; then '
                     'echo older; else echo newer; fi',
                     "version-test", str(ROOT / "common.sh"), version],
                    check=True, text=True, capture_output=True,
                )
                self.assertEqual(result.stdout.strip(), expected)
                self.assertEqual(result.stderr, "")

    def test_integration_checks_use_the_builds_config_patch_exclusions(self):
        patterns = vanadium_patch_exclusions((ROOT / "build.sh").read_text())
        for name, excluded in (
            ("0015-trichrome-apk-build-targets.patch", True),
            ("0297-Earlier-config-app-parsing-and-feature-list-init-on-.patch", True),
            ("0306-tmp-Reapply-Initialize-configs-from-config-app-on-li.patch", True),
            ("0296-Extend-chrome-android-base_module_java-targets-sourc.patch", False),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns),
                    excluded,
                )

    def test_missing_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "patch selection"):
            vanadium_patch_exclusions("#!/bin/bash\n")


if __name__ == "__main__":
    unittest.main()
