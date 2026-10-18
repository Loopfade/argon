"""Consumers resolve the single moving main prepared image to an immutable digest."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "resolve_image", ROOT / ".github/ci/resolve-prepared-image.py"
)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


class PreparedImageResolutionTests(unittest.TestCase):
    repository = "ghcr.io/loopfade/argon-build"
    tag = repository + ":branch-main"
    digest = "sha256:" + "a" * 64

    def test_repository_or_branch_tag_resolves_to_digest(self):
        for image in [self.repository.upper(), self.tag.upper()]:
            with self.subTest(image=image), patch.object(
                resolver.subprocess, "check_output",
                return_value=json.dumps({"digest": self.digest}),
            ) as inspect:
                resolved = resolver.resolve(image)
                self.assertEqual(resolved, f"{self.repository}@{self.digest}")
                inspect.assert_called_once_with(
                    ["docker", "buildx", "imagetools", "inspect", self.tag,
                     "--format", "{{json .Manifest}}"], text=True,
                )

    def test_explicit_immutable_image_does_not_need_registry_resolution(self):
        image = f"{self.repository}@{self.digest}"
        with patch.object(resolver.subprocess, "check_output") as inspect:
            self.assertEqual(resolver.resolve(image), image)
            inspect.assert_not_called()

    def test_missing_branch_main_does_not_fall_back(self):
        with patch.object(resolver.subprocess, "check_output", side_effect=
                          subprocess.CalledProcessError(1, "docker")) as inspect:
            with self.assertRaises(subprocess.CalledProcessError):
                resolver.resolve(self.repository)
            self.assertEqual(inspect.call_count, 1)

    def test_invalid_manifest_digests_are_rejected(self):
        for manifest in [{}, {"digest": "latest"}, {"digest": "sha256:123"},
                         {"digest": None}, {"digest": self.digest + "\nextra=1"}]:
            with self.subTest(manifest=manifest), patch.object(
                resolver.subprocess, "check_output", return_value=json.dumps(manifest)
            ), self.assertRaises(ValueError):
                resolver.resolve(self.repository)

    def test_unsupported_mutable_refs_are_rejected_before_docker(self):
        for image in [
            self.repository + ":latest",
            self.repository + "@sha256:123",
            "--help",
        ]:
            with self.subTest(image=image), patch.object(
                resolver.subprocess, "check_output"
            ) as inspect, self.assertRaises(ValueError):
                resolver.resolve(image)
            inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
