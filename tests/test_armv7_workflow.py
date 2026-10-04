"""Regression checks for the manual armv7 build and release path."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY_SPEC = importlib.util.spec_from_file_location(
    "release_policy", ROOT / "scripts/release_policy.py"
)
release_policy = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(release_policy)


class Armv7WorkflowTests(unittest.TestCase):
    def test_workflow_is_manual_only_and_never_dispatches_arm64(self):
        workflow = (ROOT / ".github/workflows/build-armv7.yml").read_text()
        trigger = workflow.split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch:", trigger)
        self.assertNotIn("\n  push:", trigger)
        self.assertNotIn("\n  pull_request:", trigger)
        self.assertNotIn("\n  workflow_run:", trigger)
        self.assertIn("arch: arm", workflow)
        self.assertIn("signing: release", workflow)
        self.assertIn("continuation: '10'", workflow)
        self.assertIn("group: publish-release", workflow)
        self.assertNotIn("actions/workflows/build.yml", workflow)
        self.assertIn("--abi armeabi-v7a", workflow)
        self.assertIn("Argon-arm-${{ github.sha }}", workflow)
        self.assertIn("check-release-payload-drift", workflow)

    def test_existing_arm64_release_allows_ci_only_drift(self):
        compare = {
            "status": "ahead",
            "files": [
                {"filename": ".github/workflows/build-armv7.yml"},
                {"filename": ".github/workflows/publish-release.yml"},
                {"filename": ".github/ci/resolve-prepared-image.py"},
                {"filename": "scripts/release_policy.py"},
                {"filename": "tests/test_armv7_workflow.py"},
                {"filename": "VALIDATION.md"},
                {"filename": "docker/chromium/filter-cache/README.md"},
            ],
        }
        self.assertEqual(release_policy.unsafe_release_payload_drift(compare), [])

    def test_existing_arm64_release_rejects_payload_drift(self):
        for path in (
            "build-lock.json",
            "args.gn",
            "build.sh",
            "patch.sh",
            "chromium_overlay/chrome/foo.cc",
            "certificates/ministry-ca-lock.json",
            "extensions/manifest.json",
            "res/values/strings.xml",
            "vanadium",
            "scripts/configure_build.py",
            "scripts/sign_and_verify.py",
            "docker/chromium/Dockerfile",
        ):
            with self.subTest(path=path):
                compare = {"status": "ahead", "files": [{"filename": path}]}
                self.assertEqual(
                    release_policy.unsafe_release_payload_drift(compare), [path]
                )

    def test_armv7_artifact_locator_uses_abi_specific_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "armeabi-v7a"
            payload.mkdir()
            apk = payload / "Argon-154.0.8037.57-release-armeabi-v7a.apk"
            apk.write_bytes(b"apk")
            Path(f"{apk}.sha256").write_text("checksum\n")
            (payload / "build-info.json").write_text("{}\n")
            for name in release_policy.RELEASE_LICENSES:
                (payload / name).write_text("license\n")

            selected = release_policy.locate_release_artifact(
                root, release_policy.ARMV7_RELEASE_ABI
            )
            self.assertEqual(selected["asset_dir"], payload)
            self.assertEqual(selected["apk"], apk)

    def test_arm64_release_can_be_idempotent_after_armv7_pair_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            arm64 = assets / "Argon-154.0.8037.57-release-arm64-v8a.apk"
            checksum = Path(f"{arm64}.sha256")
            arm64.write_bytes(b"arm64")
            checksum.write_text("checksum\n")

            expected = {
                path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (arm64, checksum)
            }
            armv7 = "Argon-154.0.8037.57-release-armeabi-v7a.apk"
            release_assets = [
                {"name": name, "digest": digest}
                for name, digest in expected.items()
            ] + [
                {"name": armv7, "digest": "sha256:" + "1" * 64},
                {"name": f"{armv7}.sha256", "digest": "sha256:" + "2" * 64},
            ]
            release = {
                "draft": False,
                "target_commitish": "a" * 40,
                "assets": release_assets,
            }

            release_policy.verify_existing_release(
                release, "a" * 40, assets, allow_armv7=True
            )
            with self.assertRaisesRegex(ValueError, "asset names"):
                release_policy.verify_existing_release(release, "a" * 40, assets)

            release["assets"] = release_assets[:-1]
            with self.assertRaisesRegex(ValueError, "unexpected additional"):
                release_policy.verify_existing_release(
                    release, "a" * 40, assets, allow_armv7=True
                )


if __name__ == "__main__":
    unittest.main()
