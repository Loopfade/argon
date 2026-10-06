"""Regression checks for ARM release policy and artifact handling."""
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


class ArmReleasePolicyTests(unittest.TestCase):
    def test_existing_arm64_release_allows_ci_only_drift(self):
        compare = {
            "status": "ahead",
            "files": [
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

    def test_legacy_arm64_release_allows_only_a_complete_missing_armv7_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            arm64 = assets / "Argon-154.0.8037.57-release-arm64-v8a.apk"
            checksum = Path(f"{arm64}.sha256")
            arm64.write_bytes(b"arm64")
            checksum.write_text("checksum\n")

            armv7 = "Argon-154.0.8037.57-release-armeabi-v7a.apk"
            for name in (armv7, f"{armv7}.sha256", *release_policy.RELEASE_LICENSES):
                (assets / name).write_bytes(name.encode())
            expected = {
                path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                for path in assets.iterdir()
            }
            release_assets = [
                {"name": name, "digest": digest}
                for name, digest in expected.items()
            ]
            release = {
                "draft": False,
                "target_commitish": "a" * 40,
                "assets": release_assets,
            }

            release_policy.verify_existing_release(
                release, "a" * 40, assets
            )

            release["assets"] = [item for item in release_assets
                                 if item["name"] not in (armv7, f"{armv7}.sha256")]
            release_policy.verify_existing_release(
                release, "a" * 40, assets, allow_missing_armv7=True
            )
            with self.assertRaisesRegex(ValueError, "missing verified assets"):
                release_policy.verify_existing_release(release, "a" * 40, assets)

            release["assets"].append(next(item for item in release_assets
                                          if item["name"] == armv7))
            with self.assertRaisesRegex(ValueError, "incomplete armv7 pair"):
                release_policy.verify_existing_release(
                    release, "a" * 40, assets, allow_missing_armv7=True
                )

    def test_each_published_asset_digest_is_verified_even_during_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            names = [f"Argon-154.0.8037.57-release-{abi}.apk{suffix}"
                     for abi in ("arm64-v8a", "armeabi-v7a")
                     for suffix in ("", ".sha256")]
            names += list(release_policy.RELEASE_LICENSES)
            for name in names:
                (assets / name).write_bytes(name.encode())
            published = [{"name": name, "digest": "sha256:" + hashlib.sha256(
                (assets / name).read_bytes()).hexdigest()} for name in names]
            for index, name in enumerate(names):
                with self.subTest(asset=name):
                    bad = [dict(item) for item in published]
                    bad[index]["digest"] = "sha256:" + "0" * 64
                    release = {"draft": False, "target_commitish": "a" * 40,
                               "assets": bad}
                    with self.assertRaisesRegex(ValueError, "digest mismatch"):
                        release_policy.verify_existing_release(
                            release, "a" * 40, assets, allow_missing_armv7=True)


if __name__ == "__main__":
    unittest.main()
