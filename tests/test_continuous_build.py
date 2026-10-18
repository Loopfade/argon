"""Verify continuous-build timeout recovery and live-cache snapshot isolation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuous", ROOT / ".github/ci/continuous-build.py")
continuous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuous)


class ContinuousBuildTests(unittest.TestCase):
    def test_snapshot_handles_updates_evictions_and_excludes_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, snapshot = Path(tmp) / "live", Path(tmp) / "snapshot"
            (live / "a").mkdir(parents=True)
            (live / "tmp").mkdir()
            (live / "a/resultR").write_bytes(b"completed compiler result")
            (live / "tmp/incomplete").write_bytes(b"partial")
            (live / "a/ignored").symlink_to(live / "a/resultR")
            continuous.snapshot(live, snapshot)
            self.assertEqual((snapshot / "a/resultR").read_bytes(), b"completed compiler result")
            self.assertFalse((snapshot / "tmp").exists())
            self.assertFalse((snapshot / "a/ignored").exists())
            (live / "a/resultR").unlink()
            (live / "a/newR").write_bytes(b"new result")
            continuous.snapshot(live, snapshot)
            self.assertFalse((snapshot / "a/resultR").exists())
            self.assertEqual((snapshot / "a/newR").read_bytes(), b"new result")
            with self.assertRaises(ValueError):
                continuous.snapshot(live, live)

    def test_worker_reserves_finalize_time_and_distinguishes_oom_from_budget_timeout(self):
        for status, elapsed, exhausted in ((0, 20, False), (1, 20, False),
                                           (137, 20, False), (124, 820, True), (137, 1000, True)):
            with self.subTest(status=status, elapsed=elapsed), tempfile.TemporaryDirectory() as tmp:
                result = Path(tmp) / "result.json"
                with patch.dict(os.environ, {"BUILD_JOB_STARTED_AT": "1000",
                                "BUILD_JOB_BUDGET_SECONDS": "1100", "BUILD_FINALIZE_RESERVE_SECONDS": "100"}), \
                     patch.object(continuous, "paths", return_value=(Path(tmp) / "log", result)), \
                     patch.object(continuous.time, "time", side_effect=[1000, 1000 + elapsed]), \
                     patch.object(continuous.subprocess, "call", return_value=status) as build:
                    continuous.worker()
                self.assertEqual(json.loads(result.read_text()), {"status": status, "budget_exhausted": exhausted})
                self.assertEqual(build.call_args.kwargs["env"]["BUILD_EXEC_TIMEOUT_SECONDS"], "820")

    def test_completed_background_build_is_not_started_again_by_progress_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / ".github/ci/run-in-build-container.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env bash\nprintf 'once\\n' >> invocations\nsleep 0.1\nexit 0\n")
            env = {**os.environ, "TARGET_ARCH": "arm64", "BUILD_JOB_STARTED_AT": str(int(time.time())),
                   "BUILD_JOB_BUDGET_SECONDS": "1000", "BUILD_FINALIZE_RESERVE_SECONDS": "100",
                   "GITHUB_OUTPUT": str(root / "output")}
            command = [os.sys.executable, str(ROOT / ".github/ci/continuous-build.py")]
            subprocess.run(command + ["start"], cwd=root, env=env, check=True, capture_output=True)
            for _ in range(2):
                subprocess.run(command + ["wait", "--minutes", "0.02"], cwd=root, env=env,
                               check=True, capture_output=True, timeout=10)
            completed = subprocess.run(command + ["finish"], cwd=root, env=env, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((root / "invocations").read_text(), "once\n")


if __name__ == "__main__":
    unittest.main()
