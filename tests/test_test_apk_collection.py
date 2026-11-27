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
import textwrap
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
            "GITHUB_OUTPUT": str(self.root / "output"),
            "TEST_PR_NUMBER": "93", "TEST_SOURCE_FILE": str(self.root / "source.json"),
        }
        self.snapshot = {"schema": 1, "workflow": branch.WORKFLOW,
                         "repository": "Loopfade/argon", "run_id": 123,
                         "run_number": 17, "pr_number": 93,
                         "branch": "topic/ntp", "sha": self.sha}
        (self.root / "source.json").write_text(json.dumps(self.snapshot))
        self.items = [{"id": index, "name": f"Argon-{arch}-{self.sha}-{self.fingerprints[arch]}",
                       "expired": False, "created_at": f"2026-11-18T0{index}:00:00Z"}
                      for index, arch in enumerate(("arm64", "arm"), 1)]
        # ARM64 belongs to an older attempt/page; prefer the primary artifacts
        # over previous final copies, which need independent provenance checks.
        self.pages = [{"artifacts": [self.items[1],
                       {"id": 8, "name": "Argon-Test-arm64-v8a", "expired": False}]},
                      {"artifacts": [self.items[0]]}]
        self.payloads = {arch: self.payload(arch) for arch in self.fingerprints}
        self.downloads = []
        self.calls = []
        self.deleted = []
        self.final_payloads = {}

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
            if args[2:4] == ["--method", "DELETE"]:
                number = int(args[4].rsplit("/", 1)[1])
                self.deleted.append(number)
                for page in self.pages:
                    page["artifacts"] = [item for item in page["artifacts"] if item["id"] != number]
                return subprocess.CompletedProcess(args, 0)
            number = int(args[2].split("/")[-2])
            self.downloads.append(number)
            if number in self.final_payloads:
                files = self.final_payloads[number]
                prefix = ""
            elif number == 9:
                files = {"source.json": json.dumps(self.snapshot).encode()}
                prefix = ""
            else:
                arch = {1: "arm64", 2: "arm", 8: "arm64"}[number]
                files = copy.deepcopy(self.payloads[arch])
                prefix = pipeline.TARGET_ABIS[arch] + "/"
                if number == 8:
                    metadata = json.loads(files["build-info.json"])
                    files["build-info.json"] = json.dumps({**metadata, "source_commit": "b" * 40}).encode()
            with zipfile.ZipFile(kwargs["stdout"], "w") as archive:
                for name, data in files.items():
                    archive.writestr(prefix + name, data)
            return subprocess.CompletedProcess(args, 0)
        self.assertEqual(args[0], "/tools/zipalign")
        return subprocess.CompletedProcess(args, 0)

    def execute(self, action):
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
            action()

    def collect(self):
        self.execute(branch.collect)

    def final_pair(self):
        # Model two successful upload-artifact results with the embedded journal.
        self.pages[0]["artifacts"] = [item for item in self.pages[0]["artifacts"]
                                      if item["id"] != 8]
        for number, arch in ((101, "arm64"), (102, "arm")):
            self.pages[0]["artifacts"].append(
                {"id": number, "name": branch.FINAL_ARTIFACTS[arch], "expired": False})
            self.final_payloads[number] = {**copy.deepcopy(self.payloads[arch]),
                branch.SOURCE_FILENAME: json.dumps(self.snapshot).encode()}
            self.env[f"TEST_FINAL_{arch.upper()}_ID"] = str(number)

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
        outputs = (self.root / "output").read_text()
        self.assertIn("backup_arm64=false", outputs)
        self.assertIn("backup_arm=false", outputs)
        self.assertIn("попытки №3", (self.root / "summary").read_text())
        self.assertFalse(any("rerun" in " ".join(args) or "dispatch" in " ".join(args)
                             for args in self.calls))

    def test_missing_expired_or_duplicate_architecture_never_uploads_a_partial_pair(self):
        self.pages[0]["artifacts"] = [self.items[1]]
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

    def test_cleanup_leaves_only_verified_finals_and_preserves_unrelated_artifacts(self):
        self.final_pair()
        self.pages[1]["artifacts"] += [
            {"id": 9, "name": "argon-test-source", "expired": False},
            {"id": 10, "name": "Loopfade~argon~record.dockerbuild", "expired": False},
            {"id": 11, "name": "argon-continuation-1-arm", "expired": False},
            {"id": 12, "name": "unrelated-diagnostics", "expired": False},
        ]
        self.execute(branch.prune_artifacts)
        self.assertEqual(set(self.deleted), {1, 2, 9, 10, 11})
        self.assertEqual(self.downloads, [101, 102])
        self.assertEqual({item["id"] for page in self.pages for item in page["artifacts"]},
                         {101, 102, 12})
        # Repeating cleanup remains safe after intermediates have disappeared.
        self.execute(branch.prune_artifacts)
        self.assertEqual(len(self.deleted), 5)

    def test_cleanup_rejects_missing_expired_duplicate_or_replaced_final_before_deletion(self):
        self.final_pair()
        pages = copy.deepcopy(self.pages)
        for variant in ("missing", "expired", "duplicate", "replaced"):
            with self.subTest(variant=variant):
                self.pages = copy.deepcopy(pages)
                final = next(item for item in self.pages[0]["artifacts"] if item["id"] == 102)
                if variant == "missing": self.pages[0]["artifacts"].remove(final)
                if variant == "expired": final["expired"] = True
                if variant == "duplicate": self.pages[0]["artifacts"].append(dict(final))
                if variant == "replaced": final["id"] = 103
                with self.assertRaisesRegex(ValueError, "Both current final APK uploads"):
                    self.execute(branch.prune_artifacts)
                self.assertEqual(self.deleted, [])
                self.assertEqual(self.downloads, [])

    def test_invalid_second_final_or_source_journal_keeps_every_original(self):
        self.final_pair()
        original = copy.deepcopy(self.final_payloads)
        for variant in ("source-sha", "wrong-pr", "missing-journal", "checksum"):
            with self.subTest(variant=variant):
                self.final_payloads = copy.deepcopy(original)
                payload = self.final_payloads[102]
                if variant == "source-sha":
                    metadata = json.loads(payload["build-info.json"])
                    payload["build-info.json"] = json.dumps({**metadata, "source_commit": "b" * 40}).encode()
                elif variant == "wrong-pr":
                    payload[branch.SOURCE_FILENAME] = json.dumps({**self.snapshot, "pr_number": 94}).encode()
                elif variant == "missing-journal":
                    del payload[branch.SOURCE_FILENAME]
                else:
                    name = next(name for name in payload if name.endswith(".apk"))
                    payload[name] += b"corrupt"
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.execute(branch.prune_artifacts)
                self.assertEqual(self.deleted, [])

    def test_collect_after_cleanup_uses_final_pair_and_backs_it_up_before_overwrite(self):
        self.final_pair()
        for page in self.pages:
            page["artifacts"] = [item for item in page["artifacts"] if item["id"] in (101, 102)]
        (self.root / "source.json").unlink()
        self.collect()
        self.assertEqual(self.downloads, [101, 102])
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        self.assertEqual(outputs["backup_arm64"], "true")
        self.assertEqual(outputs["backup_arm"], "true")
        for arch, abi in pipeline.TARGET_ABIS.items():
            if arch not in self.fingerprints: continue
            journal = self.root / "artifacts" / abi / branch.SOURCE_FILENAME
            self.assertEqual(json.loads(journal.read_text()), self.snapshot)

    def test_previous_final_requires_the_same_provenance_as_the_primary(self):
        # The only ARM64 fallback is the older, deliberately invalid final ID 8.
        self.pages[1]["artifacts"] = []
        with self.assertRaisesRegex(ValueError, "provenance"):
            self.collect()
        self.assertFalse((self.root / "artifacts").exists())
        self.assertEqual(self.deleted, [])

    def test_expired_primary_uses_verified_final_for_legacy_collection(self):
        self.final_pair()
        self.items[0]["expired"] = True
        self.collect()
        self.assertEqual(self.downloads, [101, 2])
        outputs = dict(line.split("=", 1) for line in (self.root / "output").read_text().splitlines())
        self.assertEqual(outputs["backup_arm64"], "true")
        self.assertEqual(outputs["backup_arm"], "false")

    def test_source_retry_recovers_pinned_sha_from_finals_without_resolving_the_pr(self):
        self.final_pair()
        self.pages = [{"artifacts": [item for item in self.pages[0]["artifacts"]
                                    if item["id"] in (101, 102)]}]
        (self.root / "source.json").unlink()
        with patch.object(branch, "api") as pr_api:
            self.execute(branch.source)
        pr_api.assert_not_called()
        self.assertEqual(json.loads((self.root / "source.json").read_text()), self.snapshot)
        self.assertEqual((self.root / "output").read_text(), f"sha={self.sha}\n")

    def test_source_retry_rejects_conflicting_final_journals(self):
        self.final_pair()
        self.pages = [{"artifacts": [item for item in self.pages[0]["artifacts"]
                                    if item["id"] in (101, 102)]}]
        self.final_payloads[102][branch.SOURCE_FILENAME] = json.dumps(
            {**self.snapshot, "sha": "b" * 40}).encode()
        (self.root / "source.json").unlink()
        with self.assertRaisesRegex(ValueError, "conflicting source snapshots"):
            self.execute(branch.source)
        self.assertFalse((self.root / "source.json").exists())
        self.assertEqual(self.deleted, [])

    def test_source_retry_reads_legacy_snapshot_before_any_completed_apk(self):
        self.pages[0]["artifacts"].append({"id": 9, "name": "argon-test-source", "expired": False})
        (self.root / "source.json").unlink()
        self.execute(branch.source)
        self.assertEqual(self.downloads, [9])
        self.assertEqual(json.loads((self.root / "source.json").read_text()), self.snapshot)

    def test_collect_accepts_old_workflow_environment_and_separate_schema_one_source(self):
        self.env.pop("TEST_PR_NUMBER")
        self.env.pop("TEST_SOURCE_FILE")
        event = self.root / "event.json"
        event.write_text(json.dumps({"inputs": {"pr_number": "93"}}))
        self.env["GITHUB_EVENT_PATH"] = str(event)
        self.pages[0]["artifacts"].append({"id": 9, "name": "argon-test-source", "expired": False})
        self.collect()
        self.assertEqual(self.downloads, [1, 2, 9])
        for abi in ("arm64-v8a", "armeabi-v7a"):
            self.assertEqual(json.loads((self.root / "artifacts" / abi / branch.SOURCE_FILENAME).read_text()),
                             self.snapshot)

    def test_partial_cleanup_failure_preserves_finals_and_can_be_retried(self):
        self.final_pair()
        original_run = self.run_command
        failed = False
        def fail_once(argv, **kwargs):
            nonlocal failed
            if list(argv)[2:4] == ["--method", "DELETE"] and str(argv[-1]).endswith("/1") and not failed:
                failed = True
                raise subprocess.CalledProcessError(503, argv)
            return original_run(argv, **kwargs)
        self.run_command = fail_once
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(branch.prune_artifacts)
        self.assertNotIn(101, self.deleted)
        self.assertNotIn(102, self.deleted)
        self.execute(branch.prune_artifacts)
        self.assertEqual(set(self.deleted), {1, 2})

    def test_test_image_disables_docker_record_upload(self):
        image = (ROOT / ".github/workflows/build-chromium-image.yml").read_text()
        self.assertIn("DOCKER_BUILD_RECORD_UPLOAD: ${{ inputs.source_sha == '' && 'true' || 'false' }}", image)

    def test_workflow_keeps_apks_uploaded_by_the_build_matrix(self):
        workflow = (ROOT / branch.WORKFLOW).read_text()
        build, cleanup = workflow.split("\n  build:\n", 1)[1].split("\n  cleanup:\n", 1)
        self.assertIn("matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}", build)
        self.assertIn("uses: ./.github/workflows/build-unified-arch.yml", build)
        self.assertIn("source_sha: ${{ needs.plan.outputs.sha }}", build)
        self.assertIn("test_version_code: ${{ needs.plan.outputs.version_code }}", build)
        self.assertNotIn("\n  collect:\n", workflow)
        self.assertNotIn("prune-artifacts", workflow)
        self.assertIn("needs: [plan, image, build]\n", cleanup)
        self.assertIn("name: argon-test-source", workflow)

        worker = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        upload = next(step for step in worker.split("\n      - name: ")
                      if "name: Argon-${{ inputs.arch }}-" in step)
        self.assertIn("actions/upload-artifact@", upload)
        self.assertIn("if: steps.build.outcome == 'success' && steps.provenance.outcome == 'success'", upload)
        self.assertIn("path: ${{ inputs.standalone_test && format('{0}/argon-test-apk', runner.temp) "
                      "|| 'artifacts/' }}", upload)
        self.assertIn("overwrite: true", upload)

    def test_test_apk_upload_contains_only_the_apk_licenses_and_fingerprints(self):
        worker = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        prepare = worker.split("\n      - name: Подготовить файлы для скачивания тестового APK\n", 1)[1]
        prepare = prepare.split("\n      - name: ", 1)[0]
        self.assertIn("if: inputs.standalone_test && steps.build.outcome == 'success' "
                      "&& steps.provenance.outcome == 'success'", prepare)
        command = textwrap.dedent(prepare.split("        run: |\n", 1)[1])
        for arch in ("arm64", "arm"):
            with self.subTest(arch=arch):
                workspace = self.root / arch
                source = workspace / "artifacts" / pipeline.TARGET_ABIS[arch]
                source.mkdir(parents=True)
                runner_temp = workspace / "runner-temp"
                runner_temp.mkdir()
                payload = self.payload(arch)
                apk_name = next(name for name in payload if name.endswith(".apk"))
                payload.update({apk_name + ".idsig": b"v4 signature",
                                "manifest-identity.json": b"{}",
                                "test-source.json": b"{}",
                                "apk-signature.txt": b"signature report",
                                "signing.p12": b"fixture, never upload"})
                for name, data in payload.items():
                    (source / name).write_bytes(data)
                subprocess.run(["bash", "-c", command], cwd=workspace, check=True,
                               env={**os.environ, "TARGET_ARCH": arch,
                                    "RUNNER_TEMP": str(runner_temp),
                                    "BUILD_CERTIFICATE": self.certificate})
                files = {path.name: path.read_bytes()
                         for path in (runner_temp / "argon-test-apk").iterdir()}
                expected = {name: payload[name]
                            for name in (apk_name, apk_name + ".sha256", *pipeline.RELEASE_LICENSES)}
                expected["signing-certificate.sha256"] = (self.certificate + "\n").encode()
                self.assertEqual(files, expected)


if __name__ == "__main__":
    unittest.main()
