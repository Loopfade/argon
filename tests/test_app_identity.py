"""Keep the standalone test APK separate without changing release identities."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_identity
import configure_build
import sign_and_verify
import test_build_targets
from apk_identity_fixture import manifest_dump
import base64


class AppIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "build-lock.json").read_text())

    def test_default_release_and_ordinary_test_keep_the_existing_identity(self):
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false"}):
            for mode in ("release", "test"):
                self.assertEqual(build_identity.application_identity(self.lock, mode), {
                    "variant": "standard", "application_id": "app.titaniumru.browser",
                    "application_label": "Argon"})

    def test_standalone_test_is_independent_of_signing_key(self):
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}):
            self.assertEqual(build_identity.application_identity(self.lock, "test"), {
                "variant": "standalone-test", "application_id": "app.titaniumru.browser.test",
                "application_label": "Argon Test"})
            self.assertEqual(build_identity.application_identity(self.lock, "release"),
                             build_identity.application_identity(self.lock, "test"))
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "unexpected"}):
            with self.assertRaisesRegex(ValueError, "true or false"):
                build_identity.application_identity(self.lock, "test")

    def test_test_version_code_is_validated_and_cannot_affect_production(self):
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true", "ARGON_TEST_VERSION_CODE": "1000000017"}):
            self.assertEqual(build_identity.application_identity(self.lock, "release")["version_code"], 1000000017)
        for value in ("-1", "0", "1.2", "2100000001", "\n17"):
            with self.subTest(value=value), patch.dict(os.environ, {
                    "ARGON_STANDALONE_TEST": "true", "ARGON_TEST_VERSION_CODE": value}), self.assertRaises(ValueError):
                build_identity.application_identity(self.lock, "release")
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false", "ARGON_TEST_VERSION_CODE": "1000000017"}):
            self.assertNotIn("version_code", build_identity.application_identity(self.lock, "release"))

    def test_wrong_test_manifest_is_rejected_before_opening_release_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_build_targets.ApkArchitectureTests.write_tools(root)
            shutil.copy(ROOT / "build-lock.json", root / "build-lock.json")
            apk = root / "wrong.apk"
            test_build_targets.ApkArchitectureTests.write_apk(apk, ["arm64-v8a"])
            argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", str(root / "sdk"),
                    "--jdk", str(root / "jdk"), "--mode", "release", "--arch", "arm64"]
            with patch.object(sign_and_verify, "ROOT", root), patch.object(sys, "argv", argv), \
                 patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}), \
                 patch.object(sign_and_verify, "run", return_value=subprocess.CompletedProcess(
                     [], 0, stdout=manifest_dump("app.titaniumru.browser"))), \
                 patch.object(sign_and_verify, "write_release_key") as key, \
                 self.assertRaisesRegex(ValueError, "application ID"):
                sign_and_verify.main()
            key.assert_not_called()

    def test_both_architectures_configure_and_restore_the_original_name(self):
        resource_text = ('<resources><string name="app_name" translatable="false">Argon</string>'
                         '<string name="other">Argon</string></resources>\n')
        for arch in ("arm64", "arm"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp)
                resource = src / build_identity.CHANNEL_CONSTANTS
                resource.parent.mkdir(parents=True)
                resource.write_text(resource_text)
                manifest = src / build_identity.MANIFEST
                manifest.write_text('<manifest>' + build_identity.ORIGINAL_PACKAGE
                                    + build_identity.GOOGLE_CHROME_SCHEME + '</manifest>')
                output = src / "out/args.gn"
                with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}):
                    build_identity.configure(src, output, arch, True, "test")
                    self.assertIn('chrome_public_manifest_package = "app.titaniumru.browser.test"',
                                  output.read_text())
                    self.assertIn('target_cpu = "' + arch + '"', output.read_text())
                    self.assertIn('cc_wrapper = "ccache"', output.read_text())
                    self.assertNotIn('android:scheme="googlechrome"', manifest.read_text())
                    self.assertNotIn('<original-package', manifest.read_text())
                    self.assertEqual(resource.read_text(), resource_text.replace(
                        '>Argon</string>', '>Argon Test</string>', 1))
                    for path in (resource, output, manifest):
                        os.utime(path, (1000, 1000))
                    build_identity.configure(src, output, arch, True, "test")
                    self.assertEqual([resource.stat().st_mtime, output.stat().st_mtime, manifest.stat().st_mtime], [1000, 1000, 1000])
                with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false"}):
                    build_identity.configure(src, output, arch, False, "release")
                self.assertEqual(resource.read_text(), resource_text)
                self.assertIn(build_identity.GOOGLE_CHROME_SCHEME, manifest.read_text())
                self.assertIn(build_identity.ORIGINAL_PACKAGE, manifest.read_text())
                self.assertEqual(output.read_text(), configure_build.render_gn_args(arch))

    def test_unknown_branding_fails_without_changing_generated_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            resource = src / build_identity.CHANNEL_CONSTANTS
            resource.parent.mkdir(parents=True)
            resource.write_text('<resources><string name="app_name">Unexpected</string></resources>')
            output = src / "args.gn"
            output.write_text("previous args")
            with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}), \
                 self.assertRaisesRegex(ValueError, "reviewed Argon"):
                build_identity.configure(src, output, "arm64", False, "test")
            self.assertEqual(output.read_text(), "previous args")
            self.assertIn("Unexpected", resource.read_text())

    def test_only_explicit_test_workflows_select_the_standalone_identity(self):
        # Keep build.sh's container tests stdlib-only, like the other workflow
        # wiring checks. Job boundaries and these fields use fixed YAML indents.
        workflows = ROOT / ".github/workflows"
        callers = []
        for path in workflows.glob("*.yml"):
            jobs = path.read_text().split("\njobs:\n", 1)[1]
            for job in re.split(r"(?m)^  [\w-]+:\n", jobs)[1:]:
                if not re.search(r"(?m)^    uses: \./\.github/workflows/build-unified-arch\.yml$", job):
                    continue
                callers.append(path.name)
                selected = re.findall(r"(?m)^      standalone_test: (.+)$", job)
                if path.name == "build-test-main.yml":
                    self.assertEqual(selected, ["true"], path.name)
                    self.assertEqual(re.findall(r"(?m)^      signing: (.+)$", job), ["release"])
                else:
                    self.assertIn(selected, ([], ["false"]), path.name)
        self.assertCountEqual(callers, ["build-all.yml", "build-test-main.yml"])
        reusable = (workflows / "build-unified-arch.yml").read_text()
        self.assertIn("      standalone_test:\n        type: boolean\n        default: false\n", reusable)
        self.assertIn("  ARGON_STANDALONE_TEST: ${{ inputs.standalone_test }}\n", reusable)

    def test_build_container_receives_the_explicit_variant(self):
        from test_build_recovery import executable
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable(root / "bin/docker", 'printf "%s\\n" "$@" > "$CAPTURE_DOCKER"')
            for selected in ("true", "false"):
                env = {**os.environ, "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
                       "TARGET_ARCH": "arm64", "ARGON_STANDALONE_TEST": selected,
                       "GITHUB_WORKSPACE": str(root), "CAPTURE_DOCKER": str(root / "docker-args")}
                subprocess.run(["bash", ROOT / ".github/ci/run-in-build-container.sh"],
                               cwd=root, env=env, check=True, capture_output=True)
                args = (root / "docker-args").read_text().splitlines()
                self.assertIn("ARGON_STANDALONE_TEST=" + selected, args)

    def test_signer_checks_the_standalone_package_and_name_for_both_abis(self):
        for arch, abi, mode in ((arch, abi, mode) for arch, abi in configure_build.TARGET_ABIS.items()
                               for mode in ("test", "release")):
            with self.subTest(arch=arch, mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                test_build_targets.ApkArchitectureTests.write_tools(root)
                for name in ("build-lock.json", "certificates/ministry-ca-lock.json",
                             "LICENSE", "licenses/Ruthenium-BSD-3-Clause.txt",
                             "licenses/Bare-BSD-3-Clause.txt"):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(ROOT / name, target)
                (root / "chromium/src").mkdir(parents=True)
                (root / "chromium/src/LICENSE").write_text("Chromium fixture")
                apk = root / "input.apk"
                test_build_targets.ApkArchitectureTests.write_apk(apk, [abi])

                def fake_run(argv, **kwargs):
                    if "xmltree" in argv:
                        return subprocess.CompletedProcess(argv, 0, stdout=manifest_dump())
                    if Path(argv[0]).name == "zipalign" and "-f" in argv:
                        shutil.copy(argv[-2], argv[-1])
                    if "sign" in argv:
                        shutil.copy(argv[-1], argv[argv.index("--out") + 1])
                    if "-exportcert" in argv:
                        Path(argv[argv.index("-file") + 1]).write_text("certificate fixture")
                    return subprocess.CompletedProcess(argv, 0, stdout=(
                        "package: name='app.titaniumru.browser.test'\napplication-label:'Argon Test'\n"))

                argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", str(root / "sdk"),
                        "--jdk", str(root / "jdk"), "--mode", mode, "--arch", arch]
                with patch.object(sign_and_verify, "ROOT", root), patch.object(sys, "argv", argv), \
                     patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true",
                         "TITANIUM_RU_KEYSTORE_BASE64": base64.b64encode(b"fixture PKCS12").decode(),
                         "TITANIUM_RU_STORE_PASSWORD": "fixture", "TITANIUM_RU_KEY_PASSWORD": "fixture",
                         "TITANIUM_RU_KEY_ALIAS": "fixture"}), \
                     patch.object(sign_and_verify, "run", side_effect=fake_run), \
                     patch.object(sign_and_verify.subprocess, "check_output", return_value="source-sha\n"):
                    sign_and_verify.main()
                metadata = json.loads((root / "artifacts" / abi / "build-info.json").read_text())
                self.assertEqual(metadata["application_identity"],
                    build_identity.application_identity(self.lock, mode, standalone_test=True))
                report = json.loads((root / "artifacts" / abi / "manifest-identity.json").read_text())
                self.assertEqual(report["application_id"], "app.titaniumru.browser.test")
                self.assertEqual(len(list((root / "artifacts" / abi).glob("Argon-Test-*.apk"))), 1)


if __name__ == "__main__":
    unittest.main()
