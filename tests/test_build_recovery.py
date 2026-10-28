"""Run the build-control scripts with simulated compiler/clock commands."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def executable(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + contents + "\n")
    path.chmod(0o755)


class BuildRecoveryTests(unittest.TestCase):
    def checkpoint(self, status, elapsed):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ["build.sh", "common.sh"]:
                shutil.copy(ROOT / name, root / name)
            (root / "chromium/src").mkdir(parents=True)
            (root / "depot_tools").mkdir()
            # Preflight and GN are covered separately. Simulate their success
            # here to exercise the real shell's handling of the compiler exit.
            executable(root / "bin/python3", 'if [[ "$*" == *--print-cpu* ]]; then echo arm64; fi')
            executable(root / "bin/gn", "exit 0")
            executable(root / "bin/ccache", "exit 0")
            executable(root / "bin/timeout", 'exit "$SIMULATED_STATUS"')
            executable(root / "bin/date", '''if [[ -f "$CLOCK_MARKER" ]]; then
  echo "$((1000 + SIMULATED_ELAPSED))"
else
  touch "$CLOCK_MARKER"
  echo 1000
fi''')
            env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                   "BUILD_MODE": "checkpoint", "BUILD_TIME_LIMIT_MINUTES": "20",
                   "CCACHE_DIR": str(root / "cache"), "SIGNING_MODE": "test",
                   "CLOCK_MARKER": str(root / "clock"), "SIMULATED_STATUS": str(status),
                   "SIMULATED_ELAPSED": str(elapsed)}
            result = subprocess.run(["bash", root / "build.sh", "arm64"], env=env,
                                    text=True, capture_output=True)
            return result, (root / ".build/cache-warm-complete-arm64").exists()

    def test_completed_target_creates_marker_in_fresh_checkout(self):
        result, complete = self.checkpoint(0, 10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(complete)

    def test_actual_timeouts_are_recoverable_without_marking_target_complete(self):
        for status, elapsed in [(124, 1200), (137, 1380)]:
            with self.subTest(status=status):
                result, complete = self.checkpoint(status, elapsed)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(complete)

    def test_early_sigkill_and_compiler_errors_are_not_hidden_as_timeouts(self):
        for status, elapsed in [(137, 10), (1, 1200)]:
            with self.subTest(status=status):
                result, complete = self.checkpoint(status, elapsed)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertFalse(complete)



if __name__ == "__main__":
    unittest.main()
