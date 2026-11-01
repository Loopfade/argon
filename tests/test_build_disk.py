"""Catch a small Docker filesystem before a large Chromium layer is downloaded."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_ci_cache import ci_module

disk = ci_module("check-build-disk")
ROOT = Path(__file__).resolve().parents[1]


class BuildDiskTests(unittest.TestCase):
    def run_check(self, free):
        paths = [Path("/docker-store"), Path("/runner-temp")]
        output, errors = io.StringIO(), io.StringIO()

        def usage(path):
            value = free[paths.index(path)]
            if isinstance(value, OSError):
                raise value
            return SimpleNamespace(free=value, total=150 * disk.GIB)

        argv = ["check-build-disk.py", "--path", str(paths[0]), "--path", str(paths[1])]
        with patch.object(disk.shutil, "disk_usage", side_effect=usage), \
             patch.object(disk.sys, "argv", argv), \
             redirect_stdout(output), redirect_stderr(errors):
            status = disk.main()
        return status == 0, output.getvalue(), errors.getvalue()

    def test_small_docker_or_workspace_disk_blocks_even_when_the_other_has_room(self):
        for free in ((35, 108), (108, 35)):
            with self.subTest(free=free):
                ready, output, errors = self.run_check([size * disk.GIB for size in free])
                self.assertFalse(ready)
                self.assertIn("35.0 ГиБ", errors)
                self.assertIn("100 ГиБ", errors)
                self.assertIn("runner", errors)
                self.assertIn("/docker-store", output)
                self.assertIn("/runner-temp", output)

    def test_free_bytes_boundary_and_unreadable_filesystem(self):
        ready, _, errors = self.run_check([disk.MINIMUM_FREE, 108 * disk.GIB])
        self.assertTrue(ready)
        self.assertEqual(errors, "")
        for unavailable in (disk.MINIMUM_FREE - 1, PermissionError("unavailable")):
            with self.subTest(unavailable=unavailable):
                ready, _, errors = self.run_check([unavailable, 108 * disk.GIB])
                self.assertFalse(ready)
                self.assertIn("::error::", errors)

    def test_both_manual_workflows_forward_the_runner_to_image_preparation(self):
        for name in ("build-all.yml", "build-all-no-publish.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            image = text.split("\n  image:\n", 1)[1].split("\n  build:\n", 1)[0]
            with self.subTest(workflow=name):
                self.assertIn("uses: ./.github/workflows/build-chromium-image.yml", image)
                self.assertIn("with:\n      runner: ${{ inputs.runner }}", image)
        image = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        self.assertIn("runs-on: ${{ inputs.runner }}", image)
        cleanup = image.split("- name: Освободить место на временном исполнителе\n", 1)[1]
        cleanup = cleanup.split("\n      - ", 1)[0]
        self.assertIn("runner.environment == 'github-hosted'", cleanup)
        self.assertIn('test "$RUNNER_ENVIRONMENT" = github-hosted', cleanup)

    def test_storage_checks_precede_layer_downloads_and_image_reuse_skips_the_check(self):
        for name, download in (
            ("build-chromium-image.yml", "uses: docker/build-push-action@"),
            ("build-unified-arch.yml", "python3 .github/ci/pull-prepared-image.py"),
        ):
            text = (ROOT / ".github/workflows" / name).read_text()
            check, = [step for step in re.split(r"(?m)^      - ", text)
                      if "check-build-disk.py" in step]
            with self.subTest(workflow=name):
                self.assertLess(text.index(check), text.index(download))
                self.assertIn("docker info --format '{{.DockerRootDir}}'", check)
                self.assertIn('--path "$docker_root" --path "$RUNNER_TEMP"', check)
                if name == "build-chromium-image.yml":
                    self.assertIn("if: steps.existing.outputs.exists != 'true'", check)


if __name__ == "__main__":
    unittest.main()
