"""Exercise release preflight with a real throwaway PKCS12 key and the JDK."""
import base64
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


def available_jdk():
    prepared = ROOT / "chromium/src/third_party/jdk/current"
    if (prepared / "bin/java").is_file() and (prepared / "bin/keytool").is_file():
        return prepared.resolve()
    java = shutil.which("java")
    if java:
        candidate = Path(java).resolve().parent.parent
        if (candidate / "bin/keytool").is_file():
            return candidate
    return None


JDK = available_jdk()


@unittest.skipUnless(JDK, "A JDK is needed for real release-key validation")
class ReleaseKeyPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="argon-key-fixture-")
        cls.addClassCleanup(cls.fixture.cleanup)
        key = Path(cls.fixture.name) / "fixture.p12"
        cls.env = {**os.environ, "TITANIUM_RU_STORE_PASSWORD": "fixture-password-123",
                   "TITANIUM_RU_KEY_PASSWORD": "fixture-password-123",
                   "TITANIUM_RU_KEY_ALIAS": "fixture-alias"}
        subprocess.run([str(JDK / "bin/keytool"), "-genkeypair", "-noprompt",
                        "-keystore", str(key), "-storetype", "PKCS12",
                        "-storepass:env", "TITANIUM_RU_STORE_PASSWORD",
                        "-alias", cls.env["TITANIUM_RU_KEY_ALIAS"],
                        "-keyalg", "RSA", "-keysize", "2048", "-validity", "2",
                        "-dname", "CN=Disposable Argon CI Fixture"],
                       env=cls.env, check=True, capture_output=True)
        cls.env["TITANIUM_RU_KEYSTORE_BASE64"] = base64.b64encode(key.read_bytes()).decode()

    def invoke(self, env):
        paths = []
        original_run = sign_and_verify.run

        def inspect_key(argv, **kwargs):
            key = Path(argv[-1])
            paths.append(key)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            # Secrets must never appear in the Java command line.
            for secret in (env["TITANIUM_RU_STORE_PASSWORD"], env["TITANIUM_RU_KEY_PASSWORD"],
                           env["TITANIUM_RU_KEYSTORE_BASE64"]):
                self.assertNotIn(secret, " ".join(map(str, argv)))
            return original_run(argv, **kwargs)

        try:
            with patch.object(sign_and_verify, "run", side_effect=inspect_key):
                sign_and_verify.check_release_key(JDK, env)
        finally:
            for key in paths:
                self.assertFalse(key.exists())
                self.assertFalse(key.parent.exists())

    def test_real_private_key_and_certificate_match(self):
        self.invoke(self.env)

    def test_wrong_passwords_alias_and_invalid_pkcs12_fail_without_leaking_values(self):
        cases = {
            "TITANIUM_RU_STORE_PASSWORD": "wrong-store-password-456",
            "TITANIUM_RU_KEY_PASSWORD": "wrong-key-password-456",
            "TITANIUM_RU_KEY_ALIAS": "missing-private-key-alias",
            "TITANIUM_RU_KEYSTORE_BASE64": base64.b64encode(b"not a PKCS12 file").decode(),
        }
        for name, value in cases.items():
            env = {**self.env, name: value}
            with self.subTest(secret=name), self.assertRaises(subprocess.CalledProcessError) as error:
                self.invoke(env)
            diagnostics = error.exception.stdout + error.exception.stderr
            self.assertIn("Release key validation failed", diagnostics)
            for secret_name in cases:
                self.assertNotIn(env[secret_name], diagnostics)


class ReleasePreflightWiringTests(unittest.TestCase):
    def test_bad_base64_and_missing_secrets_fail_before_java(self):
        env = {"TITANIUM_RU_KEYSTORE_BASE64": "invalid-base64!",
               "TITANIUM_RU_STORE_PASSWORD": "fixture-password",
               "TITANIUM_RU_KEY_PASSWORD": "fixture-password",
               "TITANIUM_RU_KEY_ALIAS": "fixture-alias"}
        with patch.object(sign_and_verify, "run") as run:
            with self.assertRaisesRegex(SystemExit, "single-line Base64"):
                sign_and_verify.check_release_key(Path("unused-jdk"), env)
            for name in env:
                with self.subTest(secret=name), self.assertRaisesRegex(SystemExit, "Missing release"):
                    sign_and_verify.check_release_key(Path("unused-jdk"), {**env, name: ""})
            run.assert_not_called()

    def test_cli_checks_release_key_only_in_release_mode(self):
        for mode in ("test", "release"):
            argv = ["sign_and_verify.py", "--check-tools", "--mode", mode,
                    "--jdk", "/fixture/jdk", "--sdk", "/fixture/sdk"]
            with self.subTest(mode=mode), patch.object(sys, "argv", argv), \
                 patch.object(sign_and_verify, "signing_tools", return_value=Path("/fixture/sdk")), \
                 patch.object(sign_and_verify, "run"), \
                 patch.object(sign_and_verify, "check_release_key") as check:
                sign_and_verify.main()
                self.assertEqual(check.call_count, int(mode == "release"))


if __name__ == "__main__":
    unittest.main()
