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
            env.pop("SCCACHE_DIR", None)
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

    def slice(self, now, status=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable(root / "bin/date", f"echo {now}")
            executable(root / "bin/docker", "exit 0")
            executable(root / ".github/ci/run-in-build-container.sh",
                       f"touch invoked\nexit {status}")
            env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
                   "BUILD_JOB_STARTED_AT": "1000", "BUILD_JOB_BUDGET_SECONDS": "21600",
                   "BUILD_CHECKPOINT_MINUTES": "20", "BUILD_FINISH_MINIMUM_SECONDS": "3600",
                   "BUILD_FINALIZE_RESERVE_SECONDS": "1800", "TARGET_ARCH": "arm64",
                   "GITHUB_OUTPUT": str(root / "output"), "BUILD_CONTAINER_NAME": "fixture"}
            result = subprocess.run(["bash", ROOT / ".github/ci/warm-compiler-cache.sh"],
                                    cwd=root, env=env, capture_output=True, text=True)
            return (result, (root / "invoked").exists(),
                    (root / ".build/cache-warm-slices-exhausted-arm64").exists(),
                    (root / ".build/cache-warm-budget-exhausted-arm64").exists())

    def test_slices_stop_before_consuming_the_uninterrupted_finish_window(self):
        result, invoked, slices_stopped, budget_exhausted = self.slice(17000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(invoked)
        self.assertTrue(slices_stopped)
        self.assertFalse(budget_exhausted)

    def test_slice_runs_when_time_remains_and_preserves_real_errors(self):
        for status in (0, 1, 137):
            with self.subTest(status=status):
                result, invoked, slices_stopped, budget_exhausted = self.slice(2000, status)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertTrue(invoked)
                self.assertFalse(slices_stopped)
                self.assertFalse(budget_exhausted)


if __name__ == "__main__":
    unittest.main()
