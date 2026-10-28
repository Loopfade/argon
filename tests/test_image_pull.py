"""A registry rate limit must not discard a ready prepared image or hide errors."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_pull", ROOT / ".github/ci/pull-prepared-image.py")
image_pull = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_pull)


class Process:
    def __init__(self, status, output):
        self.status = status
        self.stdout = io.StringIO(output)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stdout.close()

    def wait(self):
        return self.status


class ImagePullTests(unittest.TestCase):
    image = "ghcr.io/loopfade/argon-build@sha256:" + "a" * 64
    rate_limit = 'toomanyrequests: {"errors":[{"code":"TOOMANYREQUESTS","message":"retry-after: 3.712296ms"}]}\n'

    def invoke(self, results):
        return patch.object(image_pull.subprocess, "Popen",
                            side_effect=[Process(status, output) for status, output in results])

    def test_success_streams_output_without_retry(self):
        output = io.StringIO()
        with self.invoke([(0, "layer: Pull complete\nDigest: sha256:pinned\n")]) as docker, \
                patch.object(image_pull.time, "sleep") as sleep, redirect_stdout(output):
            image_pull.pull(self.image)
        self.assertEqual(docker.call_args.args[0], ["docker", "pull", self.image])
        self.assertEqual(docker.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("layer: Pull complete", output.getvalue())

    def test_observed_rate_limit_retries_only_the_selected_digest(self):
        output = io.StringIO()
        with self.invoke([(1, self.rate_limit), (1, "Too Many Requests\n"),
                          (1, "unexpected HTTP status: 429\n"), (0, "ready\n")]) as docker, \
                patch.object(image_pull.time, "sleep") as sleep, redirect_stdout(output):
            image_pull.pull(self.image)
        self.assertEqual([call.args[0] for call in docker.call_args_list],
                         [["docker", "pull", self.image]] * 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [10, 20, 40])
        self.assertIn("попытка 2/4", output.getvalue())
        self.assertIn("ready", output.getvalue())

    def test_exhausted_rate_limit_remains_a_failure(self):
        with self.invoke([(1, self.rate_limit)] * 4) as docker, \
                patch.object(image_pull.time, "sleep") as sleep, redirect_stdout(io.StringIO()), \
                self.assertRaises(subprocess.CalledProcessError):
            image_pull.pull(self.image)
        self.assertEqual(docker.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [10, 20, 40])

    def test_permanent_errors_do_not_retry(self):
        for message in ("unauthorized: authentication required", "manifest unknown",
                        "no space left on device", "pull access denied"):
            with self.subTest(message=message), self.invoke([(1, message)]) as docker, \
                    patch.object(image_pull.time, "sleep") as sleep, redirect_stdout(io.StringIO()), \
                    self.assertRaises(subprocess.CalledProcessError):
                image_pull.pull(self.image)
            self.assertEqual(docker.call_count, 1)
            sleep.assert_not_called()

    def test_permanent_error_after_a_rate_limit_stops_retrying(self):
        with self.invoke([(1, self.rate_limit), (1, "unauthorized")]) as docker, \
                patch.object(image_pull.time, "sleep") as sleep, redirect_stdout(io.StringIO()), \
                self.assertRaises(subprocess.CalledProcessError):
            image_pull.pull(self.image)
        self.assertEqual(docker.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_retry_after_is_respected_with_a_bounded_pause(self):
        for message, expected in (("retry-after: 3.712296ms", 10),
                                  ("retry-after: 35s", 35), ("retry-after: 500s", 60),
                                  ("retry-after: 25", 25), ("retry-after: invalid", 10)):
            with self.subTest(message=message):
                self.assertEqual(image_pull.retry_delay(message, 1), expected)

    def test_mutable_or_foreign_images_are_rejected_before_docker(self):
        for image in ("ghcr.io/loopfade/argon-build:branch-main", "ubuntu:24.04",
                      self.image + "\nextra", "--help", "ghcr.io/other/image@sha256:" + "a" * 64):
            with self.subTest(image=image), patch.object(image_pull.subprocess, "Popen") as docker, \
                    self.assertRaises(ValueError):
                image_pull.pull(image)
            docker.assert_not_called()

    def test_both_workflow_consumers_pull_before_starting_the_container(self):
        arch = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        validation = (ROOT / ".github/workflows/validate.yml").read_text()
        helper = "python3 .github/ci/pull-prepared-image.py"
        self.assertLess(arch.index(helper), arch.index("docker create"))
        self.assertLess(validation.index(helper), validation.index("docker run --rm"))


if __name__ == "__main__":
    unittest.main()
