"""Exercise planning, cache identity and validation of reused APK payloads."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("full_build", ROOT / ".github/ci/full-build.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def source_stage_inputs(root):
    """Collect Docker's local COPY inputs through the source/toolchain RUN."""
    recipe = (root / "docker/chromium/Dockerfile").read_text()
    boundary = "ARGON_FETCH_VANADIUM=1 bash scripts/prepare_chromium.sh sources"
    if recipe.count(boundary) != 1:
        raise ValueError("Cannot identify the cached source/toolchain stage")
    recipe = recipe.split(boundary, 1)[0] + boundary + "\n"
    files = {"Dockerfile (source stage)": recipe.encode(),
             "generated source-lock.json": subprocess.check_output(
                 [sys.executable, str(root / "scripts/image_source_inputs.py"),
                  "--lock", str(root / "build-lock.json")])}
    prepared = recipe.replace("\\\n", " ").split("FROM tooling AS prepared\n", 1)[1]
    for line in prepared.splitlines():
        if not line.startswith("COPY "):
            continue
        words = shlex.split(line)[1:]
        if any(word.startswith("--from=") for word in words):
            continue  # The source-pins stage exports the canonical JSON above.
        sources = [word for word in words[:-1] if not word.startswith("--")]
        for name in sources:
            source = root / name
            for path in source.rglob("*") if source.is_dir() else [source]:
                if path.is_file():
                    files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


class FullBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sha = "a" * 40

    def checkout(self):
        checkout = self.root / "checkout"
        def ignore(directory, names):
            skipped = set(shutil.ignore_patterns(".git", "vanadium", ".build", "keys",
                          "__pycache__", "artifacts", "tmp*", "argon-key-*")(directory, names))
            if Path(directory) == ROOT:
                skipped.update(("chromium", "depot_tools"))
            return skipped
        shutil.copytree(ROOT, checkout, ignore=ignore)
        return checkout

    def test_prepared_image_ignores_docs_but_tracks_overlay_and_toolchain_recipe(self):
        checkout = self.checkout()
        before = pipeline.image_fingerprint(checkout)
        (checkout / "README.md").write_text("documentation changed")
        self.assertEqual(before, pipeline.image_fingerprint(checkout))
        overlay = checkout / "chromium_overlay/new-source.cc"
        overlay.write_text("new source")
        self.assertNotEqual(before, pipeline.image_fingerprint(checkout))
        overlay.unlink()
        for name in ("apply_modulemap.py", "apply_browsing_data_period.py"):
            source_patch = checkout / "scripts" / name
            original = source_patch.read_text()
            source_patch.write_text(original + "\n# changed source patch\n")
            self.assertNotEqual(before, pipeline.image_fingerprint(checkout))
            source_patch.write_text(original)
            self.assertEqual(before, pipeline.image_fingerprint(checkout))
        dockerfile = checkout / "docker/chromium/Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "\nRUN update-toolchain\n")
        self.assertNotEqual(before, pipeline.image_fingerprint(checkout))

    def test_deletion_overlay_preserves_the_cached_source_toolchain_stage(self):
        checkout = self.checkout()
        before = source_stage_inputs(checkout)
        image_before = pipeline.image_fingerprint(checkout)
        self.assertTrue(all(b"apply_browsing_data_period.py" not in data
                            for data in before.values()),
                        "The deletion overlay hook leaked into the heavy source layer")
        self.assertIn(
            'python3 "$SCRIPT_DIR/scripts/apply_browsing_data_period.py" --chromium-src "$PWD"',
            (checkout / "patch.sh").read_text(),
        )
        for name in ("scripts/apply_browsing_data_period.py", "patch.sh"):
            source = checkout / name
            original = source.read_text()
            source.write_text(original + "\n# Overlay cache regression\n")
            self.assertEqual(before, source_stage_inputs(checkout))
            self.assertNotEqual(image_before, pipeline.image_fingerprint(checkout))
            source.write_text(original)
        source = checkout / "scripts/prepare_chromium.sh"
        source.write_text(source.read_text() + "\n# Source preparation changed\n")
        self.assertNotEqual(before, source_stage_inputs(checkout))

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

    def fixture_artifact(self, arch="arm64"):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        abi = pipeline.TARGET_ABIS[arch]
        self.apk = self.root / f"Argon-{lock['chromium_version']}-release-{abi}.apk"
        with zipfile.ZipFile(self.apk, "w") as zipped:
            zipped.writestr(f"lib/{abi}/libchrome.so", b"native library")
        digest = hashlib.sha256(self.apk.read_bytes()).hexdigest()
        (self.root / (self.apk.name + ".sha256")).write_text(f"{digest}  {self.apk.name}\n")
        self.certificate = hashlib.sha256(b"fixture DER").hexdigest()
        (self.root / "signing-certificate.pem").write_text(
            "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(b"fixture DER").decode()
            + "\n-----END CERTIFICATE-----\n")
        for name in ("Chromium-LICENSE.txt", "Ruthenium-LICENSE.txt", "Titanium-LICENSE.txt"):
            (self.root / name).write_text("license")
        self.metadata = {"source_commit": self.sha, "target_cpu": arch, "abi": abi,
                         "signing_mode": "release", "inputs": lock, "apk_sha256": digest,
                         "ca_der_sha256": json.loads((ROOT / "certificates/ministry-ca-lock.json").read_text())["der_sha256"],
                         "pipeline": {"fingerprint": "expected"}}
        self.metadata_path = self.root / "build-info.json"
        self.metadata_path.write_text(json.dumps(self.metadata))
        return lock

    def record_fixture(self, arch):
        self.fixture_artifact(arch)
        directory = self.root / "artifacts" / pipeline.TARGET_ABIS[arch]
        directory.mkdir(parents=True)
        for path in self.root.iterdir():
            if path.is_file():
                path.rename(directory / path.name)
        self.metadata_path = directory / "build-info.json"
        self.apk = directory / self.apk.name
        return directory

    def test_record_binds_both_architectures_without_changing_signed_payloads(self):
        for arch in ("arm64", "arm"):
            with self.subTest(arch=arch):
                directory = self.record_fixture(arch)
                payloads = {path.name: path.read_bytes() for path in directory.iterdir()
                            if path.name != "build-info.json"}
                fingerprint = pipeline.artifact_fingerprint(
                    self.sha, arch, "release", self.certificate, "prepared")
                image = "ghcr.io/loopfade/argon-build@sha256:" + "b" * 64
                with patch.object(pipeline, "ROOT", self.root), \
                     patch.object(pipeline, "image_fingerprint", return_value="prepared"), \
                     patch.dict(os.environ, {"GITHUB_SHA": self.sha}):
                    pipeline.record(arch, "release", fingerprint, self.certificate, image)
                self.assertEqual(json.loads(self.metadata_path.read_text()), {
                    **self.metadata, "pipeline": {"fingerprint": fingerprint,
                        "prepared_inputs": "prepared", "image": image,
                        "certificate_sha256": self.certificate}})
                self.assertEqual(payloads, {path.name: path.read_bytes() for path in directory.iterdir()
                                           if path.name != "build-info.json"})

    def test_record_rejects_changed_plan_or_key_without_rewriting_metadata(self):
        self.record_fixture("arm64")
        before = self.metadata_path.read_bytes()
        fingerprint = pipeline.artifact_fingerprint(
            self.sha, "arm64", "release", self.certificate, "prepared")
        for sha, planned, certificate in (("b" * 40, fingerprint, self.certificate),
                                          (self.sha, "stale", self.certificate),
                                          (self.sha, fingerprint, "rotated-key")):
            with self.subTest(sha=sha, fingerprint=planned, certificate=certificate), \
                 patch.object(pipeline, "ROOT", self.root), \
                 patch.object(pipeline, "image_fingerprint", return_value="prepared"), \
                 patch.dict(os.environ, {"GITHUB_SHA": sha}):
                with self.assertRaises(ValueError):
                    pipeline.record("arm64", "release", planned, certificate, "image")
                self.assertEqual(self.metadata_path.read_bytes(), before)

    def test_workflow_records_in_the_signing_container_with_the_planned_source_sha(self):
        workflow = (ROOT / ".github/workflows/build-unified-arch.yml").read_text()
        step = workflow.split("      - name: Bind verified APK to this pipeline plan\n", 1)[1]
        step = step.split("\n      - name:", 1)[0]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        binary = self.root / "bin/docker"
        binary.parent.mkdir()
        binary.write_text(f"#!{sys.executable}\n" + '''import json, os, sys
with open(os.environ["CAPTURE_DOCKER"], "w") as output:
    json.dump({"args": sys.argv[1:], "sha": os.environ["GITHUB_SHA"]}, output)
''')
        binary.chmod(0o755)
        captured = self.root / "docker.json"
        for arch in ("arm64", "arm"):
            with self.subTest(arch=arch):
                env = {**os.environ, "PATH": str(binary.parent) + os.pathsep + os.environ["PATH"],
                       "CAPTURE_DOCKER": str(captured), "GITHUB_SHA": self.sha,
                       "BUILD_CONTAINER_NAME": "argon-build", "TARGET_ARCH": arch,
                       "SIGNING_MODE": "release", "BUILD_FINGERPRINT": "planned-fingerprint",
                       "BUILD_CERTIFICATE": "planned-certificate", "PREPARED_IMAGE": "planned-image"}
                result = subprocess.run(["bash", "-euo", "pipefail", "-c", script],
                                        cwd=self.root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(captured.read_text()), {"sha": self.sha, "args": [
                    "exec", "--env", "GITHUB_SHA", "argon-build", "python3",
                    "/workspace/.github/ci/full-build.py", "record", "--arch", arch,
                    "--mode", "release", "--fingerprint", "planned-fingerprint",
                    "--certificate", "planned-certificate", "--image", "planned-image"]})

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

    def test_both_manual_workflows_reuse_only_after_payload_verification(self):
        payload = self.root / "verified-payload"
        payload.mkdir()
        (payload / "verified.apk").write_bytes(b"verified APK")
        repository = "Loopfade/argon"
        for workflow in (".github/workflows/build-all.yml",
                         ".github/workflows/build-all-no-publish.yml"):
            source = {"path": workflow, "event": "workflow_dispatch", "head_sha": self.sha,
                      "head_branch": "main", "repository": {"full_name": repository},
                      "head_repository": {"full_name": repository}}
            artifact = {"id": 11, "name": "matching", "expired": False,
                        "created_at": "2026-10-19T00:00:00Z", "workflow_run": {"id": 41}}
            with self.subTest(workflow=workflow), \
                 patch.dict(os.environ, {"GITHUB_REPOSITORY": repository}), \
                 patch.object(pipeline, "ROOT", self.root), \
                 patch.object(pipeline, "api", side_effect=[[{"artifacts": [artifact]}], source]), \
                 patch.object(pipeline, "extract_artifact", return_value=payload), \
                 patch.object(pipeline, "verify_artifact", return_value=payload) as verify:
                self.assertEqual(pipeline.reusable_artifact("matching", self.sha, "arm64", "release",
                    "fingerprint", "certificate", Path("/sdk"), Path("/jdk")), 41)
                verify.assert_called_once_with(payload, self.sha, "arm64", "release", "fingerprint",
                                               "certificate", Path("/sdk"), Path("/jdk"))
                self.assertEqual((self.root / "artifacts/arm64-v8a/verified.apk").read_bytes(), b"verified APK")

    def test_unrelated_workflow_cannot_supply_reusable_artifacts(self):
        repository = "Loopfade/argon"
        source = {"path": ".github/workflows/publish-release.yml", "event": "workflow_dispatch",
                  "head_sha": self.sha, "head_branch": "main", "repository": {"full_name": repository},
                  "head_repository": {"full_name": repository}}
        artifact = {"id": 11, "name": "matching", "expired": False,
                    "created_at": "2026-10-19T00:00:00Z", "workflow_run": {"id": 41}}
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": repository}), \
             patch.object(pipeline, "api", side_effect=[[{"artifacts": [artifact]}], source]), \
             patch.object(pipeline, "extract_artifact") as extract:
            self.assertIsNone(pipeline.reusable_artifact("matching", self.sha, "arm64", "release",
                "fingerprint", "certificate", Path("/sdk"), Path("/jdk")))
            extract.assert_not_called()

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
