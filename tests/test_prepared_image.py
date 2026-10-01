"""Version upgrades must resolve their own prepared image before an APK build."""
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
    digest = "sha256:" + "a" * 64

    def test_upgrade_resolves_the_locked_version_and_returns_only_a_digest(self):
        for version in ["153.0.8010.52", "154.0.8037.57"]:
            with self.subTest(version=version), patch.object(
                resolver.subprocess, "check_output",
                return_value=json.dumps({"digest": self.digest}),
            ) as inspect:
                image = resolver.resolve(self.repository.upper(), version)
                self.assertEqual(image, f"{self.repository}@{self.digest}")
                inspect.assert_called_once_with(
                    ["docker", "buildx", "imagetools", "inspect",
                     f"{self.repository}:chromium-{version}",
                     "--format", "{{json .Manifest}}"], text=True,
                )

    def test_explicit_immutable_image_does_not_need_registry_resolution(self):
        image = f"{self.repository}@{self.digest}"
        with patch.object(resolver.subprocess, "check_output") as inspect:
            self.assertEqual(resolver.resolve(image, "154.0.8037.57"), image)
            inspect.assert_not_called()

    def test_missing_m154_image_never_falls_back_to_m153(self):
        with patch.object(resolver.subprocess, "check_output", side_effect=
                          subprocess.CalledProcessError(1, "docker")) as inspect:
            with self.assertRaises(subprocess.CalledProcessError):
                resolver.resolve(self.repository, "154.0.8037.57")
            self.assertEqual(inspect.call_count, 1)

    def test_invalid_manifest_digests_are_rejected(self):
        for manifest in [{}, {"digest": "latest"}, {"digest": "sha256:123"},
                         {"digest": None}, {"digest": self.digest + "\nextra=1"}]:
            with self.subTest(manifest=manifest), patch.object(
                resolver.subprocess, "check_output", return_value=json.dumps(manifest)
            ), self.assertRaises(ValueError):
                resolver.resolve(self.repository, "154.0.8037.57")

    def test_mutable_overrides_and_invalid_versions_are_rejected_before_docker(self):
        for image, version in [
            (self.repository + ":latest", "154.0.8037.57"),
            (self.repository + "@sha256:123", "154.0.8037.57"),
            (self.repository, "154.0.8037.57\nextra=1"),
            ("--help", "154.0.8037.57"),
        ]:
            with self.subTest(image=image, version=version), patch.object(
                resolver.subprocess, "check_output"
            ) as inspect, self.assertRaises(ValueError):
                resolver.resolve(image, version)
            inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
