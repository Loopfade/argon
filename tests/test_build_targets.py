import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import configure_build
import sign_and_verify


class BuildTargetsTests(unittest.TestCase):
    def test_cpu_and_abi_names_generate_matching_args(self):
        for cpu, abi, drumbrake in [
            ("arm64", "arm64-v8a", "true"),
            ("arm", "armeabi-v7a", "false"),
            ("x64", "x86_64", "true"),
            ("x86", "x86", "false"),
        ]:
            for name in (cpu, abi):
                with self.subTest(arch=name):
                    self.assertEqual(configure_build.target_cpu(name), cpu)
                    self.assertEqual(configure_build.TARGET_ABIS[cpu], abi)
                    args = configure_build.render_gn_args(name).splitlines()
                    self.assertIn(f'target_cpu = "{cpu}"', args)
                    self.assertIn(f'v8_enable_drumbrake = {drumbrake}', args)
                    self.assertIn(f'v8_drumbrake_bounds_checks = {drumbrake}', args)
                    self.assertIn('enable_android_secondary_abi = false', args)
                    self.assertIn('is_desktop_android = true', args)
                    self.assertIn('chrome_public_manifest_package = "app.titaniumru.browser"', args)

    def test_unsupported_architectures_are_rejected(self):
        for arch in ["riscv64", "mips", "armeabi", "all", "", "../arm64"]:
            with self.subTest(arch=arch), self.assertRaises(argparse.ArgumentTypeError):
                configure_build.render_gn_args(arch)

    def test_missing_or_duplicate_template_assignments_are_rejected(self):
        template = (ROOT / "args.gn").read_text()
        for name in ["target_cpu", "v8_enable_drumbrake", "v8_drumbrake_bounds_checks"]:
            line = next(line for line in template.splitlines() if line.startswith(name + " ="))
            for malformed in [template.replace(line, ""), template + line + "\n"]:
                with self.subTest(name=name), patch.object(Path, "read_text", return_value=malformed):
                    with self.assertRaisesRegex(ValueError, name):
                        configure_build.render_gn_args("x86")

    def test_cli_writes_separate_outputs_and_preserves_template(self):
        template = (ROOT / "args.gn").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            for cpu in ["arm64", "arm", "x64", "x86"]:
                output = Path(tmp) / cpu / "args.gn"
                subprocess.run([sys.executable, ROOT / "scripts/configure_build.py",
                                "--arch", cpu, "--output", output], check=True)
                self.assertIn(f'target_cpu = "{cpu}"', output.read_text())
            self.assertEqual(len(list(Path(tmp).glob("*/args.gn"))), 4)
        self.assertEqual((ROOT / "args.gn").read_bytes(), template)

    def test_default_remains_arm64(self):
        result = subprocess.check_output(
            [sys.executable, ROOT / "scripts/configure_build.py", "--print-cpu"], text=True
        )
        self.assertEqual(result.strip(), "arm64")

    def test_unchanged_gn_args_preserve_mtime_between_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "args.gn"
            command = [sys.executable, ROOT / "scripts/configure_build.py", "--output", output]
            subprocess.run(command, check=True)
            os.utime(output, (1000, 1000))
            subprocess.run(command, check=True)
            self.assertEqual(output.stat().st_mtime, 1000)
            subprocess.run(command + ["--arch", "x86"], check=True)
            self.assertIn('target_cpu = "x86"', output.read_text())
            self.assertNotEqual(output.stat().st_mtime, 1000)

    def test_ccache_mode_uses_ninja_wrapper(self):
        args = configure_build.render_gn_args("arm64", ccache=True).splitlines()
        self.assertIn('use_siso = false', args)
        self.assertIn('cc_wrapper = "ccache"', args)
        self.assertNotIn('use_siso = true', args)

    def test_sccache_mode_uses_ninja_wrapper(self):
        args = configure_build.render_gn_args(
            "arm64", compiler_wrapper="sccache"
        ).splitlines()
        self.assertIn('use_siso = false', args)
        self.assertIn('cc_wrapper = "sccache"', args)
        self.assertNotIn('use_siso = true', args)

    def test_shell_rejects_invalid_architecture_before_preflight(self):
        result = subprocess.run(["bash", ROOT / "build.sh", "riscv64"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported architecture", result.stderr)
        self.assertNotIn("preflight.py", result.stderr)

    def test_shell_rejects_invalid_build_mode_before_preflight(self):
        result = subprocess.run(
            ["bash", ROOT / "build.sh", "arm64"],
            capture_output=True,
            text=True,
            env={**os.environ, "BUILD_MODE": "invalid"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BUILD_MODE must be apk, warm, prepare, checkpoint, or finish", result.stderr)
        self.assertNotIn("preflight.py", result.stderr)

    def test_shell_requires_cache_directory_for_warm_mode(self):
        env = {**os.environ, "BUILD_MODE": "warm"}
        env.pop("CCACHE_DIR", None)
        env.pop("SCCACHE_DIR", None)
        result = subprocess.run(
            ["bash", ROOT / "build.sh", "arm64"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("CCACHE_DIR or SCCACHE_DIR is required", result.stderr)
        self.assertNotIn("preflight.py", result.stderr)

    def test_legacy_entry_point_selects_arm64(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            shutil.copy(ROOT / "build-arm64.sh", directory)
            (directory / "build.sh").write_text('printf "%s\\n" "$@"\n')
            result = subprocess.check_output(["bash", directory / "build-arm64.sh"], text=True)
            self.assertEqual(result.strip(), "arm64")


class ApkArchitectureTests(unittest.TestCase):
    @staticmethod
    def write_tools(root, version="36.0.0"):
        build_tools = root / "sdk/build-tools" / version
        paths = [build_tools / name for name in ("apksigner", "zipalign", "aapt2")]
        paths += [root / "jdk/bin" / name for name in ("java", "keytool")]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            path.chmod(0o755)
        return build_tools

    def test_signing_tools_choose_newest_numeric_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tools(root, "9.0.0")
            expected = self.write_tools(root, "36.0.0")
            self.assertEqual(sign_and_verify.signing_tools(root / "sdk", root / "jdk"), expected)

    def test_missing_or_nonexecutable_tool_is_rejected_before_signing(self):
        for relative in ["sdk/build-tools/36.0.0/zipalign", "sdk/build-tools/36.0.0/aapt2",
                         "jdk/bin/java", "jdk/bin/keytool"]:
            for missing in (True, False):
                with self.subTest(tool=relative, missing=missing), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    self.write_tools(root)
                    tool = root / relative
                    if missing:
                        tool.unlink()
                    else:
                        tool.chmod(0o644)
                    with self.assertRaisesRegex(SystemExit, "Missing or non-executable"):
                        sign_and_verify.signing_tools(root / "sdk", root / "jdk")

    def test_tool_preflight_needs_no_apk_or_signing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tools(root)
            argv = ["sign_and_verify.py", "--check-tools", "--sdk", str(root / "sdk"),
                    "--jdk", str(root / "jdk")]
            with patch.object(sys, "argv", argv), patch.object(sign_and_verify, "run") as run:
                sign_and_verify.main()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual({Path(command[0]).name for command in commands},
                                 {"java", "keytool", "apksigner", "aapt2", "zipalign"})
                self.assertFalse(any("-genkeypair" in command or "sign" in command
                                     for command in commands))
                self.assertEqual(run.call_args_list[2].kwargs["env"]["JAVA_HOME"],
                                 str((root / "jdk").resolve()))
                self.assertEqual(run.call_args_list[2].kwargs["env"]["PATH"].split(os.pathsep)[0],
                                 str(root / "jdk/bin"))

    @staticmethod
    def write_apk(path, abis):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("AndroidManifest.xml", "fixture")
            for abi in abis:
                archive.writestr(f"lib/{abi}/libchrome.so", b"fixture")

    def test_apk_must_contain_exactly_the_selected_abi(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / "input.apk"
            for expected in ["arm64-v8a", "armeabi-v7a", "x86_64", "x86"]:
                with self.subTest(expected=expected):
                    self.write_apk(apk, [expected])
                    sign_and_verify.verify_apk_abi(apk, expected)
                other = "x86" if expected != "x86" else "arm64-v8a"
                for actual in [[], [other], [expected, other]]:
                    with self.subTest(expected=expected, actual=actual):
                        self.write_apk(apk, actual)
                        with self.assertRaisesRegex(SystemExit, "Unexpected APK native ABIs"):
                            sign_and_verify.verify_apk_abi(apk, expected)

    def test_signing_rejects_wrong_abi_before_using_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / "input.apk"
            self.write_apk(apk, ["arm64-v8a"])
            argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", tmp,
                    "--jdk", tmp, "--mode", "test", "--arch", "x86_64"]
            with patch.object(sys, "argv", argv), patch.object(sign_and_verify, "run") as run:
                with self.assertRaisesRegex(SystemExit, "expected x86_64"):
                    sign_and_verify.main()
                run.assert_not_called()

    def test_corrupt_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / "input.apk"
            self.write_apk(apk, ["arm64-v8a"])
            apk.write_bytes(apk.read_bytes().replace(b"fixture", b"corrupt", 1))
            with self.assertRaisesRegex(SystemExit, "ZIP integrity failure"):
                sign_and_verify.verify_apk_abi(apk, "arm64-v8a")

    def test_signing_keeps_metadata_checksums_and_certificates_per_abi(self):
        # SDK/JDK commands are simulated here; this does not verify a real APK signature.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tools(root)
            for relative in ["build-lock.json", "certificates/ministry-ca-lock.json",
                             "LICENSE", "licenses/Ruthenium-BSD-3-Clause.txt"]:
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / relative, destination)
            (root / "chromium/src").mkdir(parents=True)
            (root / "chromium/src/LICENSE").write_text("Chromium license fixture")

            commands = []

            def fake_run(argv, **kwargs):
                commands.append(argv)
                if Path(argv[0]).name == "zipalign" and "-f" in argv:
                    shutil.copy(argv[-2], argv[-1])
                if "sign" in argv:
                    self.assertEqual(Path(argv[-1]).name, "aligned.apk")
                    shutil.copy(argv[-1], argv[argv.index("--out") + 1])
                if "-exportcert" in argv:
                    Path(argv[argv.index("-file") + 1]).write_text("certificate fixture")
                return subprocess.CompletedProcess(argv, 0, stdout=(
                    "package: name='app.titaniumru.browser'\n"
                    "application-label:'Argon'\n"
                ))

            snapshots = {}
            for cpu, abi in configure_build.TARGET_ABIS.items():
                commands.clear()
                apk = root / "input.apk"
                self.write_apk(apk, [abi])
                argv = ["sign_and_verify.py", "--apk", str(apk), "--sdk", str(root / "sdk"),
                        "--jdk", str(root / "jdk"), "--mode", "test", "--arch", abi]
                with patch.object(sign_and_verify, "ROOT", root), patch.object(sys, "argv", argv), \
                     patch.object(sign_and_verify, "run", side_effect=fake_run), \
                     patch.object(sign_and_verify.subprocess, "check_output", return_value="source-sha\n"):
                    sign_and_verify.main()
                self.assertTrue((root / ".build").is_dir())
                self.assertEqual(Path(commands[0][0]).name, "zipalign")
                align_index = next(i for i, cmd in enumerate(commands) if "-f" in cmd)
                sign_index = next(i for i, cmd in enumerate(commands) if "sign" in cmd)
                verify_align_index = next(i for i, cmd in enumerate(commands) if "-c" in cmd)
                self.assertLess(align_index, sign_index)
                self.assertLess(sign_index, verify_align_index)
                sign = commands[sign_index]
                self.assertEqual(sign[sign.index("--ks-type") + 1], "JKS")
                export = next(cmd for cmd in commands if "-exportcert" in cmd)
                self.assertEqual(export[export.index("-storetype") + 1], "JKS")
                directory = root / "artifacts" / abi
                info = json.loads((directory / "build-info.json").read_text())
                self.assertEqual(info["abi"], abi)
                self.assertEqual(info["target_cpu"], cpu)
                self.assertEqual(info["device_smoke_test"], "not run")
                output = next(directory.glob("*.apk"))
                self.assertTrue(output.name.endswith(f"-test-{abi}.apk"))
                self.assertEqual((directory / (output.name + ".sha256")).read_text(),
                                 f'{info["apk_sha256"]}  {output.name}\n')
                for name in ["apk-signature.txt", "signing-certificate.pem", "Titanium-LICENSE.txt",
                             "Chromium-LICENSE.txt", "Ruthenium-LICENSE.txt"]:
                    self.assertTrue((directory / name).is_file(), name)
                for previous, data in snapshots.items():
                    self.assertEqual(previous.read_bytes(), data)
                snapshots.update({path: path.read_bytes() for path in directory.iterdir()})


if __name__ == "__main__":
    unittest.main()
