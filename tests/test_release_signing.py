"""Ensure release keys use the explicit portable PKCS12 format."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sign_and_verify


class ReleaseSigningTests(unittest.TestCase):
    def test_release_signing_declares_pkcs12_for_sign_and_certificate_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdk = root / "sdk/build-tools/36.0.0"
            jdk = root / "jdk/bin"
            for path in [*(sdk / n for n in ("apksigner", "zipalign", "aapt2")),
                         *(jdk / n for n in ("java", "keytool"))]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                path.chmod(0o755)
            for relative in ["build-lock.json", "certificates/ministry-ca-lock.json",
                             "LICENSE", "licenses/Ruthenium-BSD-3-Clause.txt"]:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / relative, destination)
            (root / "chromium/src").mkdir(parents=True)
            (root / "chromium/src/LICENSE").write_text("Chromium license fixture")
            apk = root / "input.apk"
            from test_build_targets import ApkArchitectureTests
            ApkArchitectureTests.write_apk(apk, ["arm64-v8a"])
            commands = []

            def fake_run(argv, **kwargs):
                commands.append((argv, kwargs))
                if Path(argv[0]).name == "zipalign" and "-f" in argv:
                    shutil.copy(argv[-2], argv[-1])
                if "sign" in argv:
                    shutil.copy(argv[-1], argv[argv.index("--out") + 1])
                if "-exportcert" in argv:
                    Path(argv[argv.index("-file") + 1]).write_text("certificate fixture")
                return subprocess.CompletedProcess(argv, 0, stdout=(
                    "package: name='app.titaniumru.browser'\napplication-label:'Argon'\n"))

            env = {"TITANIUM_RU_KEYSTORE_BASE64": base64.b64encode(b"PKCS12 fixture").decode(),
                   "TITANIUM_RU_STORE_PASSWORD": "ascii-store-password",
                   "TITANIUM_RU_KEY_PASSWORD": "ascii-key-password",
                   "TITANIUM_RU_KEY_ALIAS": "argon-release"}
            argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", str(root / "sdk"),
                    "--jdk", str(root / "jdk"), "--mode", "release", "--arch", "arm64"]
            with patch.object(sign_and_verify, "ROOT", root), patch.object(sys, "argv", argv), \
                 patch.dict(os.environ, env, clear=False), patch.object(sign_and_verify, "run", side_effect=fake_run), \
                 patch.object(sign_and_verify.subprocess, "check_output", return_value="source-sha\n"):
                sign_and_verify.main()
            signing = next(argv for argv, _ in commands if "sign" in argv)
            exported = next(argv for argv, _ in commands if "-exportcert" in argv)
            self.assertEqual(signing[signing.index("--ks-type") + 1], "PKCS12")
            self.assertTrue(str(signing[signing.index("--ks") + 1]).endswith("signing.p12"))
            self.assertEqual(exported[exported.index("-storetype") + 1], "PKCS12")
            signer_environment = next(kwargs["env"] for argv, kwargs in commands if "sign" in argv)
            self.assertEqual(signer_environment["PATH"].split(os.pathsep)[0], str(root / "jdk/bin"))


if __name__ == "__main__":
    unittest.main()
