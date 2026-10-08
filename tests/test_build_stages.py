"""Exercise build-stage boundaries without downloading or compiling Chromium."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_build_recovery import executable

ROOT = Path(__file__).resolve().parents[1]
ANDROID_TARGETS = [
    "obj/chrome/browser/android/android/argon_certificate_domains_settings.o",
    "chrome_java",
    "chrome_public_apk",
]


class BuildStageTests(unittest.TestCase):
    def run_build(self, mode, compiler_status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("build.sh", "common.sh", "scripts/prepare_chromium.sh"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, root / name)
            (root / ".gclient").touch()
            (root / "vanadium/patches").mkdir(parents=True)
            (root / "vanadium/patches/fixture.patch").touch()
            (root / "patch.sh").write_text(":\n")
            # Stub external work, retaining the real build.sh control flow.
            # The prepared-image branch still has to execute CTest and GN.
            executable(root / "bin/python3", '''
printf 'python:%s\n' "$*" >> "$STAGE_LOG"
if [[ "$*" == *--print-cpu* ]]; then echo arm64
elif [[ "${1:-}" == -c ]]; then echo pinned
fi''')
            executable(root / "bin/git", '''
if [[ "$*" == *rev-parse* ]]; then
  echo pinned
elif [[ "$*" == 'init depot_tools' ]]; then
  mkdir -p depot_tools/python-bin
  cp "$FIXTURE_ROOT/bin/python3" depot_tools/python-bin/python3
  cp "$FIXTURE_ROOT/bin/noop" depot_tools/ensure_bootstrap
elif [[ "$*" == init ]]; then
  mkdir -p build
  cp "$FIXTURE_ROOT/bin/noop" build/install-build-deps.sh
fi''')
            for command in ("noop", "sudo", "gclient", "cmake", "ccache", "ctest", "gn"):
                executable(root / "bin" / command,
                           'printf "%s:%s\\n" "${0##*/}" "$*" >> "$STAGE_LOG"')
            executable(root / "bin/timeout", 'shift 3\n"$@"')
            executable(root / "bin/autoninja", '''
printf '%s\n' "$@" > "$COMPILER_ARGS"
if (( COMPILER_STATUS != 0 )); then exit "$COMPILER_STATUS"; fi
mkdir -p "$2/apks"
touch "$2/apks/ChromePublic.apk"''')
            if mode in ("checkpoint", "finish"):
                (root / "chromium/src").mkdir(parents=True)
                (root / "depot_tools").mkdir()
            env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                   "BUILD_MODE": mode, "SIGNING_MODE": "test",
                   "STAGE_LOG": str(root / "stages"), "FIXTURE_ROOT": str(root),
                   "COMPILER_ARGS": str(root / "compiler"),
                   "COMPILER_STATUS": str(compiler_status)}
            env.pop("CCACHE_DIR", None)
            if mode == "checkpoint":
                env["CCACHE_DIR"] = str(root / "cache")
            result = subprocess.run(["bash", root / "build.sh", "arm64"], env=env,
                                    capture_output=True, text=True)
            compiler = root / "compiler"
            return (result, (root / "stages").read_text().splitlines(),
                    compiler.read_text().splitlines() if compiler.exists() else None)

    def test_preparation_runs_policy_tests_and_gn_without_android_compilation(self):
        result, stages, compiler = self.run_build("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = next(i for i, line in enumerate(stages) if line.startswith("ctest:"))
        gn = next(i for i, line in enumerate(stages) if line.startswith("gn:gen "))
        self.assertLess(policy, gn)
        self.assertTrue(any("-m unittest discover" in line for line in stages))
        self.assertIsNone(compiler)
        self.assertFalse(any("sign_and_verify.py" in line for line in stages))

    def test_checkpoint_explicitly_compiles_java_jni_and_apk(self):
        result, stages, compiler = self.run_build("checkpoint")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(compiler[-3:], ANDROID_TARGETS)
        self.assertFalse(any("sign_and_verify.py" in line for line in stages))

    @unittest.skipUnless(Path("/dev/fd").exists(),
                         "The build's Bash process substitution requires /dev/fd")
    def test_final_modes_compile_java_jni_and_apk_before_signing(self):
        for mode in ("apk", "finish"):
            with self.subTest(mode=mode):
                result, stages, compiler = self.run_build(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(compiler[-3:], ANDROID_TARGETS)
                self.assertTrue(any("sign_and_verify.py" in line for line in stages))

    def test_android_compiler_failure_prevents_signing_in_every_apk_mode(self):
        for mode in ("apk", "checkpoint", "finish"):
            with self.subTest(mode=mode):
                result, stages, compiler = self.run_build(mode, compiler_status=1)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(compiler[-3:], ANDROID_TARGETS)
                self.assertFalse(any("sign_and_verify.py" in line for line in stages))


if __name__ == "__main__":
    unittest.main()
