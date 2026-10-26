"""Keep the standalone test APK separate without changing release identities."""
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
import build_identity
import configure_build
import sign_and_verify
import test_build_targets


class AppIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "build-lock.json").read_text())

    def test_default_release_and_ordinary_test_keep_the_existing_identity(self):
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false"}):
            for mode in ("release", "test"):
                self.assertEqual(build_identity.application_identity(self.lock, mode), {
                    "variant": "standard", "application_id": "app.titaniumru.browser",
                    "application_label": "Argon"})

    def test_standalone_test_is_explicit_and_rejects_release_signing(self):
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}):
            self.assertEqual(build_identity.application_identity(self.lock, "test"), {
                "variant": "standalone-test", "application_id": "app.titaniumru.browser.test",
                "application_label": "Argon Test"})
            with self.assertRaisesRegex(ValueError, "test signing"):
                build_identity.application_identity(self.lock, "release")
        with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "unexpected"}):
            with self.assertRaisesRegex(ValueError, "true or false"):
                build_identity.application_identity(self.lock, "test")

    def test_both_architectures_configure_and_restore_the_original_name(self):
        resource_text = ('<resources><string name="app_name" translatable="false">Argon</string>'
                         '<string name="other">Argon</string></resources>\n')
        for arch in ("arm64", "arm"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp)
                resource = src / build_identity.CHANNEL_CONSTANTS
                resource.parent.mkdir(parents=True)
                resource.write_text(resource_text)
                output = src / "out/args.gn"
                with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}):
                    build_identity.configure(src, output, arch, True, "test")
                    self.assertIn('chrome_public_manifest_package = "app.titaniumru.browser.test"',
                                  output.read_text())
                    self.assertIn('target_cpu = "' + arch + '"', output.read_text())
                    self.assertIn('cc_wrapper = "ccache"', output.read_text())
                    self.assertEqual(resource.read_text(), resource_text.replace(
                        '>Argon</string>', '>Argon Test</string>', 1))
                    for path in (resource, output):
                        os.utime(path, (1000, 1000))
                    build_identity.configure(src, output, arch, True, "test")
                    self.assertEqual([resource.stat().st_mtime, output.stat().st_mtime], [1000, 1000])
                with patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "false"}):
                    build_identity.configure(src, output, arch, False, "release")
                self.assertEqual(resource.read_text(), resource_text)
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

    def test_only_the_artifacts_workflow_opts_in_for_test_signing(self):
        import yaml
        workflows = ROOT / ".github/workflows"
        for path in workflows.glob("*.yml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            for job in workflow.get("jobs", {}).values():
                if job.get("uses") != "./.github/workflows/build-unified-arch.yml":
                    continue
                selected = job.get("with", {}).get("standalone_test", "false")
                self.assertEqual(selected, "${{ inputs.signing == 'test' }}"
                                 if path.name == "build-all-no-publish.yml" else "false", path.name)
        artifacts = yaml.load((workflows / "build-all-no-publish.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(artifacts["env"]["ARGON_STANDALONE_TEST"], "${{ inputs.signing == 'test' }}")
        self.assertNotIn("publish", artifacts["jobs"])
        reusable = yaml.load((workflows / "build-unified-arch.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(reusable["on"]["workflow_call"]["inputs"]["standalone_test"]["default"], "false")
        self.assertEqual(reusable["env"]["ARGON_STANDALONE_TEST"], "${{ inputs.standalone_test }}")

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
        for arch, abi in configure_build.TARGET_ABIS.items():
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                test_build_targets.ApkArchitectureTests.write_tools(root)
                for name in ("build-lock.json", "certificates/ministry-ca-lock.json",
                             "LICENSE", "licenses/Ruthenium-BSD-3-Clause.txt"):
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(ROOT / name, target)
                (root / "chromium/src").mkdir(parents=True)
                (root / "chromium/src/LICENSE").write_text("Chromium fixture")
                apk = root / "input.apk"
                test_build_targets.ApkArchitectureTests.write_apk(apk, [abi])

                def fake_run(argv, **kwargs):
                    if Path(argv[0]).name == "zipalign" and "-f" in argv:
                        shutil.copy(argv[-2], argv[-1])
                    if "sign" in argv:
                        shutil.copy(argv[-1], argv[argv.index("--out") + 1])
                    if "-exportcert" in argv:
                        Path(argv[argv.index("-file") + 1]).write_text("certificate fixture")
                    return subprocess.CompletedProcess(argv, 0, stdout=(
                        "package: name='app.titaniumru.browser.test'\napplication-label:'Argon Test'\n"))

                argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", str(root / "sdk"),
                        "--jdk", str(root / "jdk"), "--mode", "test", "--arch", arch]
                with patch.object(sign_and_verify, "ROOT", root), patch.object(sys, "argv", argv), \
                     patch.dict(os.environ, {"ARGON_STANDALONE_TEST": "true"}), \
                     patch.object(sign_and_verify, "run", side_effect=fake_run), \
                     patch.object(sign_and_verify.subprocess, "check_output", return_value="source-sha\n"):
                    sign_and_verify.main()
                metadata = json.loads((root / "artifacts" / abi / "build-info.json").read_text())
                self.assertEqual(metadata["application_identity"],
                    build_identity.application_identity(self.lock, "test", standalone_test=True))


if __name__ == "__main__":
    unittest.main()
