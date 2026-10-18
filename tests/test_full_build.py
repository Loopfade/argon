"""Exercise planning, cache identity and validation of reused APK payloads."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("full_build", ROOT / ".github/ci/full-build.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class FullBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sha = "a" * 40

    def test_prepared_image_ignores_docs_but_tracks_overlay_and_toolchain_recipe(self):
        checkout = self.root / "checkout"
        def ignore(directory, names):
            skipped = set(shutil.ignore_patterns(".git", "vanadium", ".build", "keys",
                          "__pycache__", "artifacts", "tmp*", "argon-key-*")(directory, names))
            if Path(directory) == ROOT:
                skipped.update(("chromium", "depot_tools"))
            return skipped
        shutil.copytree(ROOT, checkout, ignore=ignore)
        before = pipeline.image_fingerprint(checkout)
        (checkout / "README.md").write_text("documentation changed")
        self.assertEqual(before, pipeline.image_fingerprint(checkout))
        overlay = checkout / "chromium_overlay/new-source.cc"
        overlay.write_text("new source")
        self.assertNotEqual(before, pipeline.image_fingerprint(checkout))
        overlay.unlink()
        dockerfile = checkout / "docker/chromium/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "\nRUN update-toolchain\n")
        self.assertNotEqual(before, pipeline.image_fingerprint(checkout))

    def test_apk_identity_separates_commits_architectures_keys_and_modes(self):
        args = [self.sha, "arm64", "release", "certificate", "prepared"]
        before = pipeline.artifact_fingerprint(*args)
        for index, replacement in enumerate(("b" * 40, "arm", "test", "rotated-key", "new-inputs")):
            changed = list(args)
            changed[index] = replacement
            self.assertNotEqual(before, pipeline.artifact_fingerprint(*changed))

    def plan(self, results):
        output, summary = self.root / "output", self.root / "summary"
        with patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main", "GITHUB_SHA": self.sha,
                         "JAVA_HOME": "/fixture/jdk", "ANDROID_HOME": "/fixture/sdk",
                         "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}), \
             patch.object(pipeline, "reusable_artifact", side_effect=results):
            pipeline.plan("test")
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_two_verified_apks_skip_image_and_all_compilation(self):
        outputs = self.plan([41, 42])
        self.assertEqual(outputs["build"], "false")
        self.assertEqual(json.loads(outputs["matrix"]), {"include": []})
        self.assertEqual(outputs["reuse_arm64"], "true")
        self.assertEqual(outputs["reuse_arm"], "true")

    def test_only_missing_architecture_is_scheduled(self):
        outputs = self.plan([41, None])
        builds = json.loads(outputs["matrix"])["include"]
        self.assertEqual([build["arch"] for build in builds], ["arm"])
        self.assertEqual(builds[0]["fingerprint"], outputs["fingerprint_arm"])

    def test_cold_build_schedules_both_architectures_with_distinct_identities(self):
        outputs = self.plan([None, None])
        builds = json.loads(outputs["matrix"])["include"]
        self.assertEqual([build["arch"] for build in builds], ["arm64", "arm"])
        self.assertNotEqual(builds[0]["fingerprint"], builds[1]["fingerprint"])

    def fixture_artifact(self):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        self.apk = self.root / f"Argon-{lock['chromium_version']}-release-arm64-v8a.apk"
        with zipfile.ZipFile(self.apk, "w") as zipped:
            zipped.writestr("lib/arm64-v8a/libchrome.so", b"native library")
        digest = hashlib.sha256(self.apk.read_bytes()).hexdigest()
        (self.root / (self.apk.name + ".sha256")).write_text(f"{digest}  {self.apk.name}\n")
        self.certificate = hashlib.sha256(b"fixture DER").hexdigest()
        (self.root / "signing-certificate.pem").write_text(
            "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(b"fixture DER").decode()
            + "\n-----END CERTIFICATE-----\n")
        for name in ("Chromium-LICENSE.txt", "Ruthenium-LICENSE.txt", "Titanium-LICENSE.txt"):
            (self.root / name).write_text("license")
        self.metadata = {"source_commit": self.sha, "target_cpu": "arm64", "abi": "arm64-v8a",
                         "signing_mode": "release", "inputs": lock, "apk_sha256": digest,
                         "ca_der_sha256": json.loads((ROOT / "certificates/ministry-ca-lock.json").read_text())["der_sha256"],
                         "pipeline": {"fingerprint": "expected"}}
        self.metadata_path = self.root / "build-info.json"
        self.metadata_path.write_text(json.dumps(self.metadata))
        return lock

    def verify(self):
        return pipeline.verify_artifact(self.root, self.sha, "arm64", "release", "expected",
                                        self.certificate, Path("/sdk"), Path("/jdk"))

    def test_reused_apk_is_cryptographically_checked_not_just_selected_by_name(self):
        lock = self.fixture_artifact()
        with patch.object(pipeline, "signing_tools", return_value=Path("/tools")), \
             patch.object(pipeline, "command", side_effect=[
                 f"Signer #1 certificate SHA-256 digest: {self.certificate}\n",
                 f"package: name='{lock['application_id']}'\napplication-label:'Argon'\n"]), \
             patch.object(pipeline.subprocess, "run") as align:
            self.assertEqual(self.verify(), self.root)
            align.assert_called_once()

    def test_changed_sha_mode_abi_inputs_or_fingerprint_never_reuses_apk(self):
        self.fixture_artifact()
        for key, value in (("source_commit", "b" * 40), ("target_cpu", "arm"),
                           ("signing_mode", "test"), ("inputs", {}),
                           ("pipeline", {"fingerprint": "stale"})):
            with self.subTest(key=key):
                self.metadata_path.write_text(json.dumps({**self.metadata, key: value}))
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.verify()

    def test_tampered_apk_and_rotated_release_key_are_rejected(self):
        self.fixture_artifact()
        self.certificate = "b" * 64
        with self.assertRaisesRegex(ValueError, "certificate"):
            self.verify()
        self.apk.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.verify()

    def test_missing_image_is_built_but_authentication_failure_is_not_a_cache_miss(self):
        output = self.root / "output"
        for stderr, missing in (("manifest unknown", True), ("unauthorized", False)):
            with self.subTest(stderr=stderr), patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                 patch.object(pipeline.subprocess, "check_output", side_effect=subprocess.CalledProcessError(
                     1, "docker", stderr=stderr)), patch.object(pipeline, "image_fingerprint", return_value="fingerprint"):
                if missing:
                    pipeline.probe_image("ghcr.io/loopfade/argon-build:branch-main")
                    self.assertIn("exists=false", output.read_text())
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        pipeline.probe_image("ghcr.io/loopfade/argon-build:branch-main")

    def test_matching_image_returns_the_immutable_digest_without_pulling_layers(self):
        output = self.root / "output"
        digest = "sha256:" + "a" * 64
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
             patch.object(pipeline, "image_fingerprint", return_value="expected"), \
             patch.object(pipeline.subprocess, "check_output", side_effect=[
                 json.dumps({"digest": digest}),
                 json.dumps({"config": {"Labels": {pipeline.IMAGE_LABEL: "expected"}}})]) as inspect:
            pipeline.probe_image("ghcr.io/loopfade/argon-build:branch-main")
        values = output.read_text()
        self.assertIn("exists=true", values)
        self.assertIn(f"image=ghcr.io/loopfade/argon-build@{digest}", values)
        self.assertTrue(all(call.args[0][1:4] == ["buildx", "imagetools", "inspect"]
                            for call in inspect.call_args_list))


if __name__ == "__main__":
    unittest.main()
