"""Collect a verified APK pair when its architectures finish in different attempts."""
import base64
from contextlib import ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from apk_identity_fixture import manifest_dump
from test_ci_cache import ci_module
from test_full_build import pipeline

ROOT = Path(__file__).resolve().parents[1]
branch = ci_module("branch-test")


class TestApkCollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sha = "a" * 40
        self.lock = json.loads((ROOT / "build-lock.json").read_text())
        (self.root / "build-lock.json").write_text(json.dumps(self.lock))
        self.certificate = hashlib.sha256(b"fixture DER").hexdigest()
        self.signer = self.certificate
        self.identity = pipeline.application_identity(self.lock, "release", standalone_test=True)
        self.identity["version_code"] = 1000000017
        self.fingerprints = {arch: pipeline.artifact_fingerprint(
            self.sha, arch, "release", self.certificate, "prepared", True, 1000000017)
            for arch in ("arm64", "arm")}
        self.env = {
            "GITHUB_REPOSITORY": "Loopfade/argon", "GITHUB_REF": "refs/heads/main",
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_NUMBER": "17", "GITHUB_RUN_ATTEMPT": "3",
            "BUILD_SOURCE_SHA": self.sha, "BUILD_CERTIFICATE": self.certificate,
            "ARGON_STANDALONE_TEST": "true", "ARGON_TEST_VERSION_CODE": "1000000017",
            "TEST_BUILD_MATRIX": json.dumps({"include": [
                {"arch": arch, "fingerprint": fingerprint}
                for arch, fingerprint in self.fingerprints.items()]}),
            "JAVA_HOME": "/fixture/jdk", "ANDROID_HOME": "/fixture/sdk",
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
        }
        self.items = [{"id": index, "name": f"Argon-{arch}-{self.sha}-{self.fingerprints[arch]}",
                       "expired": False, "created_at": f"2026-11-18T0{index}:00:00Z"}
                      for index, arch in enumerate(("arm64", "arm"), 1)]
        # ARM64 belongs to an older attempt/page; unrelated completed outputs
        # must never substitute for the exact primary build artifacts.
        self.pages = [{"artifacts": [self.items[1],
                       {"id": 8, "name": "Argon-Test-arm64-v8a", "expired": False}]},
                      {"artifacts": [self.items[0]]}]
        self.payloads = {arch: self.payload(arch) for arch in self.fingerprints}
        self.downloads = []
        self.calls = []

    def payload(self, arch):
        abi = pipeline.TARGET_ABIS[arch]
        name = pipeline.apk_filename(self.lock, "release", self.identity, abi)
        apk = io.BytesIO()
        with zipfile.ZipFile(apk, "w") as archive:
            archive.writestr(f"lib/{abi}/libchrome.so", b"host-test native payload")
        digest = hashlib.sha256(apk.getvalue()).hexdigest()
        metadata = {
            "source_commit": self.sha, "target_cpu": arch, "abi": abi,
            "signing_mode": "release", "inputs": self.lock, "apk_sha256": digest,
            "application_identity": self.identity,
            "ca_der_sha256": json.loads((ROOT / "certificates/ministry-ca-lock.json").read_text())["der_sha256"],
            "pipeline": {"fingerprint": self.fingerprints[arch]},
        }
        return {name: apk.getvalue(), name + ".sha256": f"{digest}  {name}\n".encode(),
                "build-info.json": json.dumps(metadata).encode(),
                "signing-certificate.pem": b"-----BEGIN CERTIFICATE-----\n"
                    + base64.b64encode(b"fixture DER") + b"\n-----END CERTIFICATE-----\n",
                **{license: b"license" for license in pipeline.RELEASE_LICENSES}}

    def command(self, argv, **kwargs):
        args = list(map(str, argv))
        self.calls.append(args)
        if args[0] == "gh":
            self.assertEqual(args, ["gh", "api", "repos/Loopfade/argon/actions/runs/123/artifacts?per_page=100",
                                    "--paginate", "--slurp"])
            return json.dumps(self.pages)
        if args[0] == "/tools/apksigner":
            return f"Signer #1 certificate SHA-256 digest: {self.signer}\n"
        if args[:3] == ["/tools/aapt2", "dump", "badging"]:
            return f"package: name='{self.identity['application_id']}'\napplication-label:'Argon Test'\n"
        if args[:3] == ["/tools/aapt2", "dump", "xmltree"]:
            return manifest_dump(self.identity["application_id"], self.identity["version_code"])
        self.fail(args)

    def run_command(self, argv, **kwargs):
        args = list(map(str, argv))
        self.calls.append(args)
        if args[0] == "gh":
            self.assertEqual(args[:2], ["gh", "api"])
            number = int(args[2].split("/")[-2])
            arch = {1: "arm64", 2: "arm"}[number]
            self.downloads.append(number)
            with zipfile.ZipFile(kwargs["stdout"], "w") as archive:
                for name, data in self.payloads[arch].items():
                    archive.writestr(pipeline.TARGET_ABIS[arch] + "/" + name, data)
            return subprocess.CompletedProcess(args, 0)
        self.assertEqual(args[0], "/tools/zipalign")
        return subprocess.CompletedProcess(args, 0)

    def collect(self):
        with ExitStack() as stack:
            for context in (
                patch.dict(os.environ, self.env), patch.object(branch, "ROOT", self.root),
                patch.object(branch, "check_checkout", return_value=self.sha),
                patch.object(branch, "module", return_value=pipeline),
                patch.object(pipeline, "image_fingerprint", return_value="prepared"),
                patch.object(pipeline, "signing_tools", return_value=Path("/tools")),
                patch.object(pipeline, "command", side_effect=self.command),
                patch.object(pipeline.subprocess, "run", side_effect=self.run_command),
            ):
                stack.enter_context(context)
            branch.collect()

    def test_partial_retry_collects_both_pages_without_rebuilding_or_changing_apks(self):
        original = copy.deepcopy(self.payloads)
        self.collect()
        self.assertEqual(self.downloads, [1, 2])
        for arch, files in original.items():
            output = self.root / "artifacts" / pipeline.TARGET_ABIS[arch]
            for name, contents in files.items():
                self.assertEqual((output / name).read_bytes(), contents)
            self.assertTrue((output / "manifest-identity.json").exists())
            self.assertTrue((output / "apk-signature.txt").exists())
        self.assertEqual(self.payloads, original)
        self.assertIn("попытки №3", (self.root / "summary").read_text())
        self.assertFalse(any("rerun" in " ".join(args) or "dispatch" in " ".join(args)
                             for args in self.calls))

    def test_missing_expired_or_duplicate_architecture_never_uploads_a_partial_pair(self):
        for variant in ("missing", "expired", "duplicate"):
            with self.subTest(variant=variant):
                self.items[0]["expired"] = variant == "expired"
                self.pages[1]["artifacts"] = ([] if variant == "missing" else
                    [self.items[0]] * (2 if variant == "duplicate" else 1))
                with self.assertRaisesRegex(ValueError, "exactly one available arm64"):
                    self.collect()
                self.assertFalse((self.root / "artifacts").exists())
                self.assertEqual(self.downloads, [])

    def test_wrong_metadata_in_second_apk_blocks_the_complete_pair(self):
        for change in ({"source_commit": "b" * 40}, {"target_cpu": "arm64"},
                       {"pipeline": {"fingerprint": "stale"}},
                       {"application_identity": {**self.identity, "version_code": 1000000016}}):
            with self.subTest(change=change):
                metadata = json.loads(self.payload("arm")["build-info.json"])
                self.payloads["arm"]["build-info.json"] = json.dumps({**metadata, **change}).encode()
                with self.assertRaisesRegex(ValueError, "provenance"):
                    self.collect()
                self.assertFalse((self.root / "artifacts").exists())

    def test_checksum_or_signature_mismatch_is_not_accepted_by_artifact_name(self):
        self.signer = "b" * 64
        with self.assertRaisesRegex(ValueError, "signature"):
            self.collect()
        self.signer = self.certificate
        name = next(name for name in self.payloads["arm"] if name.endswith(".apk"))
        self.payloads["arm"][name] += b"corruption"
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.collect()
        self.assertFalse((self.root / "artifacts").exists())

    def test_wrong_native_abi_is_rejected_even_with_matching_checksums(self):
        name = next(name for name in self.payloads["arm"] if name.endswith(".apk"))
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("lib/arm64-v8a/libchrome.so", b"wrong ABI")
        digest = hashlib.sha256(data.getvalue()).hexdigest()
        self.payloads["arm"][name] = data.getvalue()
        self.payloads["arm"][name + ".sha256"] = f"{digest}  {name}\n".encode()
        metadata = json.loads(self.payloads["arm"]["build-info.json"])
        self.payloads["arm"]["build-info.json"] = json.dumps({**metadata, "apk_sha256": digest}).encode()
        with self.assertRaisesRegex(SystemExit, "native ABIs"):
            self.collect()
        self.assertFalse((self.root / "artifacts").exists())

    def test_changed_plan_or_test_version_cannot_select_another_artifact(self):
        for change in ({"ARGON_TEST_VERSION_CODE": "1000000016"},
                       {"TEST_BUILD_MATRIX": json.dumps({"include": [{"arch": "arm"}]})},
                       {"TEST_BUILD_MATRIX": json.dumps({"include": [
                           {"arch": arch, "fingerprint": "stale"} for arch in ("arm64", "arm")]})},
                       {"BUILD_CERTIFICATE": "temporary-test-key"}):
            with self.subTest(change=change), patch.dict(self.env, change), self.assertRaises(ValueError):
                self.collect()
            self.assertEqual(self.downloads, [])

    def test_failed_inventory_or_unsafe_archive_does_not_produce_final_outputs(self):
        with patch.object(pipeline, "api", side_effect=subprocess.CalledProcessError(1, "gh")), \
             self.assertRaises(subprocess.CalledProcessError):
            self.collect()
        self.payloads["arm64"]["../../outside"] = b"unsafe"
        with self.assertRaisesRegex(ValueError, "Unsafe artifact"):
            self.collect()
        self.assertFalse((self.root / "artifacts").exists())

    def test_workflow_uploads_both_outputs_after_verification_and_before_cleanup(self):
        workflow = (ROOT / branch.WORKFLOW).read_text()
        collect = workflow.split("\n  collect:\n", 1)[1].split("\n  cleanup:\n", 1)[0]
        self.assertIn("needs: [plan, build]", collect)
        self.assertIn("needs.build.result == 'success'", collect)
        self.assertIn("ref: ${{ needs.plan.outputs.sha }}", collect)
        self.assertIn("TEST_BUILD_MATRIX: ${{ needs.plan.outputs.matrix }}", collect)
        self.assertIn("ARGON_TEST_VERSION_CODE: ${{ needs.plan.outputs.version_code }}", collect)
        verify, uploads = collect.split("run: python3 .github/ci/branch-test.py collect", 1)
        self.assertNotIn("actions/upload-artifact", verify)
        for abi in ("arm64-v8a", "armeabi-v7a"):
            self.assertIn(f"name: Argon-Test-{abi}\n", uploads)
            self.assertIn(f"path: artifacts/{abi}/\n", uploads)
        self.assertEqual(uploads.count("overwrite: true"), 2)
        self.assertEqual(uploads.count("if-no-files-found: error"), 2)
        self.assertIn("needs: [plan, image, build, collect]", workflow)
        self.assertNotIn("secrets.", collect)


if __name__ == "__main__":
    unittest.main()
