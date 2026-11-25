#!/usr/bin/env python3
"""Resolve a PR once and build its pinned revision with the main cache scope."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ".github/workflows/build-test-branch.yml"
FINAL_ARTIFACTS = {"arm64": "Argon-Test-arm64-v8a", "arm": "Argon-Test-armeabi-v7a"}
SOURCE_FILENAME = "test-source.json"


def api(path):
    args = ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}"]
    return json.loads(subprocess.check_output(args, text=True))


def emit(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in values.items():
            if "\n" in str(value) or "\r" in str(value):
                raise ValueError("Multiline workflow output")
            output.write(f"{name}={value}\n")


def positive_number(value):
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValueError("Expected a positive PR number or run ID")
    return int(value)


def validate_source(branch, sha):
    if (not isinstance(branch, str) or not branch or branch == "main"
            or branch.startswith("refs/") or any(char in branch for char in "\r\n")
            or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)):
        raise ValueError("Expected a pinned temporary-branch source")


def pull_request_source(number):
    pr = api(f"pulls/{number}")
    repository = os.environ["GITHUB_REPOSITORY"]
    head, base = pr.get("head") or {}, pr.get("base") or {}
    # The manual worker uses the repository's signing key and main cache.
    # Preserve the previous restriction to branches in this repository.
    if (pr.get("number") != number or pr.get("state") != "open"
            or base.get("ref") != "main"
            or (base.get("repo") or {}).get("full_name") != repository
            or (head.get("repo") or {}).get("full_name") != repository):
        raise ValueError("Expected an open PR into main from this repository")
    branch, sha = head.get("ref"), head.get("sha")
    validate_source(branch, sha)
    return {"branch": branch, "sha": sha}


def source_identity():
    number = os.environ.get("TEST_PR_NUMBER")
    if number is None:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        number = event["inputs"]["pr_number"]
    return {
        "schema": 1, "workflow": WORKFLOW,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": positive_number(os.environ["GITHUB_RUN_ID"]),
        "run_number": positive_number(os.environ["GITHUB_RUN_NUMBER"]),
        "pr_number": positive_number(number),
    }


def validate_snapshot(selected, identity, sha=None):
    if (not isinstance(selected, dict)
            or any(type(selected.get(key)) is not type(value) or selected.get(key) != value
                   for key, value in identity.items())):
        raise ValueError("Pinned source does not belong to this PR build run")
    validate_source(selected.get("branch"), selected.get("sha"))
    if sha is not None and selected["sha"] != sha:
        raise ValueError("Pinned source does not match the planned PR commit")
    return selected


def source_from_artifacts(pipeline, artifacts, identity, sha=None):
    available = [item for item in artifacts if not item["expired"]]
    snapshots = [item for item in available if item["name"] == "argon-test-source"]
    if len(snapshots) > 1:
        raise ValueError("Ambiguous pinned source snapshot")
    candidates = snapshots or [item for item in available
                               if item["name"] in FINAL_ARTIFACTS.values()
                               or re.fullmatch(r"Argon-(?:arm64|arm)-[0-9a-f]{40}-[0-9a-f]{64}",
                                               item["name"])]
    selected = None
    with tempfile.TemporaryDirectory(prefix="argon-test-source-") as temporary:
        for item in candidates:
            artifact_id = positive_number(str(item["id"]))
            destination = Path(temporary) / str(artifact_id)
            destination.mkdir()
            payload = pipeline.extract_artifact(artifact_id, destination)
            files = list(payload.rglob("source.json" if snapshots else SOURCE_FILENAME))
            if not files and not snapshots:
                continue  # Legacy APK artifacts did not contain the source journal.
            if len(files) != 1:
                raise ValueError("Expected one pinned source snapshot in the artifact")
            current = validate_snapshot(json.loads(files[0].read_text()), identity, sha)
            if selected is not None and current != selected:
                raise ValueError("APK artifacts contain conflicting source snapshots")
            selected = current
    if selected is None:
        raise FileNotFoundError("Pinned source snapshot is unavailable; never resolve a newer PR head")
    return selected


def artifact_inventory(pipeline, run_id):
    pages = pipeline.api(f"actions/runs/{run_id}/artifacts?per_page=100", paginate=True)
    return [item for page in pages for item in page["artifacts"]]


def source():
    if os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("Run the test workflow from main")
    attempt = positive_number(os.environ["GITHUB_RUN_ATTEMPT"])
    identity = source_identity()
    path = Path(os.environ["TEST_SOURCE_FILE"])
    if attempt == 1:
        if path.exists():
            raise ValueError("A source snapshot already exists in the first attempt")
        selected = {**identity, **pull_request_source(identity["pr_number"])}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(selected, indent=2) + "\n")
    else:
        # Re-run all jobs must use the first attempt's source too. Re-resolving
        # the PR here could silently build a newer push after a timeout.
        if path.exists():
            selected = validate_snapshot(json.loads(path.read_text()), identity)
        else:
            pipeline = module("branch_test_pipeline", ".github/ci/full-build.py")
            selected = source_from_artifacts(
                pipeline, artifact_inventory(pipeline, identity["run_id"]), identity)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(selected, indent=2) + "\n")
    emit(sha=selected["sha"])
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"## Источник тестовой сборки\n\n"
                      f"- PR: №{selected['pr_number']}.\n"
                      f"- Ветка: `{selected['branch']}`.\n- Коммит: `{selected['sha']}`.\n"
                      "- Область записи кеша GitHub Actions: `refs/heads/main`.\n"
                      "- Готовые APK: Artifacts этого запуска.\n")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def check_checkout():
    sha = os.environ["BUILD_SOURCE_SHA"]
    if (os.environ["GITHUB_REF"] != "refs/heads/main"
            or os.environ.get("ARGON_STANDALONE_TEST") != "true"
            or not re.fullmatch(r"[0-9a-f]{40}", sha)):
        raise ValueError("Expected a pinned standalone test build with main cache scope")
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if actual != sha:
        raise ValueError("Checkout changed after resolving the temporary branch")
    return sha


def plan():
    sha = check_checkout()
    pipeline = module("branch_test_pipeline", ".github/ci/full-build.py")
    # Supply the built revision explicitly, preserving the main run/cache ref.
    # A stable signer and an increasing code allow Test -> Test upgrades without
    # replacing the production package. Retries of this run keep the same code.
    number = os.environ["GITHUB_RUN_NUMBER"]
    if not re.fullmatch(r"[1-9][0-9]*", number) or int(number) > 1100000000:
        raise ValueError("Invalid test build sequence")
    version = str(1000000000 + int(number))
    os.environ["ARGON_TEST_VERSION_CODE"] = version
    pipeline.plan("release", source_sha=sha, reuse=False)
    emit(version_code=version)


def collection_plan():
    sha = check_checkout()
    run_id = positive_number(os.environ["GITHUB_RUN_ID"])
    certificate = os.environ["BUILD_CERTIFICATE"]
    if not re.fullmatch(r"[0-9a-f]{64}", certificate):
        raise ValueError("Expected the planned release certificate")
    pipeline = module("branch_test_pipeline", ".github/ci/full-build.py")
    lock = json.loads((ROOT / "build-lock.json").read_text())
    identity = pipeline.application_identity(lock, "release")
    if identity.get("version_code") != 1000000000 + positive_number(os.environ["GITHUB_RUN_NUMBER"]):
        raise ValueError("Test versionCode does not belong to this run")
    matrix = json.loads(os.environ["TEST_BUILD_MATRIX"])["include"]
    if (len(matrix) != 2 or {item["arch"] for item in matrix} != {"arm64", "arm"}):
        raise ValueError("Expected both planned test architectures")
    fingerprints = {item["arch"]: item["fingerprint"] for item in matrix}
    prepared = pipeline.image_fingerprint()
    for arch in ("arm64", "arm"):
        expected = pipeline.artifact_fingerprint(
            sha, arch, "release", certificate, prepared, True, identity["version_code"])
        if fingerprints[arch] != expected:
            raise ValueError("Test artifact fingerprint changed after planning")
    return sha, pipeline, certificate, fingerprints, artifact_inventory(pipeline, run_id)


def collect():
    """Verify both APKs from this run, including successful earlier attempts."""
    sha, pipeline, certificate, fingerprints, artifacts = collection_plan()
    attempt = positive_number(os.environ["GITHUB_RUN_ATTEMPT"])
    selected, backups = {}, {}
    for arch, fingerprint in fingerprints.items():
        name = f"Argon-{arch}-{sha}-{fingerprint}"
        matches = [item for item in artifacts if item["name"] == name and not item["expired"]]
        backups[arch] = not matches
        if not matches:
            matches = [item for item in artifacts
                       if item["name"] == FINAL_ARTIFACTS[arch] and not item["expired"]]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one available {arch} APK in this run")
        selected[arch] = matches[0]
        positive_number(str(selected[arch]["id"]))

    jdk = Path(os.environ["JAVA_HOME"])
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    # Verify the complete pair before exposing either output to upload-artifact.
    # Originals survive until both final uploads have been verified by prune.
    with tempfile.TemporaryDirectory(prefix="argon-test-apks-") as temporary:
        verified = {}
        for arch, item in selected.items():
            destination = Path(temporary) / arch
            destination.mkdir()
            payload = pipeline.extract_artifact(item["id"], destination)
            verified[arch] = pipeline.verify_artifact(
                payload, sha, arch, "release", fingerprints[arch], certificate, sdk, jdk)
        identity = source_identity()
        source_path = os.environ.get("TEST_SOURCE_FILE")
        embedded = [validate_snapshot(json.loads((directory / SOURCE_FILENAME).read_text()),
                                      identity, sha)
                    for directory in verified.values() if (directory / SOURCE_FILENAME).exists()]
        if embedded and any(value != embedded[0] for value in embedded):
            raise ValueError("APK artifacts contain conflicting source snapshots")
        snapshot = (validate_snapshot(json.loads(Path(source_path).read_text()), identity, sha)
                    if source_path and Path(source_path).exists()
                    else embedded[0] if embedded
                    else source_from_artifacts(pipeline, artifacts, identity, sha))
        if embedded and snapshot != embedded[0]:
            raise ValueError("Local and embedded source snapshots do not agree")
        for arch, directory in verified.items():
            output = ROOT / "artifacts" / pipeline.TARGET_ABIS[arch]
            shutil.copytree(directory, output)
            (output / SOURCE_FILENAME).write_text(json.dumps(snapshot, indent=2) + "\n")

    emit(**{f"backup_{arch}": "true" if needed else "false" for arch, needed in backups.items()},
         **{f"primary_{arch}": f"Argon-{arch}-{sha}-{fingerprint}"
            for arch, fingerprint in fingerprints.items()})

    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"## Оба тестовых APK для попытки №{attempt}\n\n"
                      f"Коммит: `{sha}`. Проверены подпись, происхождение и контрольные суммы.\n\n"
                      "Готовые файлы прикрепляются к этой попытке как "
                      "`Argon-Test-arm64-v8a` и `Argon-Test-armeabi-v7a`. "
                      "Повторная компиляция не выполняется.\n")


def prune_artifacts():
    """Delete intermediates only after the exact uploaded final pair is durable."""
    sha, pipeline, certificate, fingerprints, artifacts = collection_plan()
    final_ids = {arch: positive_number(os.environ[f"TEST_FINAL_{arch.upper()}_ID"])
                 for arch in FINAL_ARTIFACTS}
    selected = {}
    for arch, name in FINAL_ARTIFACTS.items():
        matches = [item for item in artifacts if item["name"] == name and not item["expired"]]
        if len(matches) != 1 or matches[0]["id"] != final_ids[arch]:
            raise ValueError("Both current final APK uploads must exist before artifact cleanup")
        selected[arch] = matches[0]
    identity = source_identity()
    jdk = Path(os.environ["JAVA_HOME"])
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    snapshot = None
    with tempfile.TemporaryDirectory(prefix="argon-final-apks-") as temporary:
        for arch, item in selected.items():
            destination = Path(temporary) / arch
            destination.mkdir()
            payload = pipeline.extract_artifact(item["id"], destination)
            directory = pipeline.verify_artifact(
                payload, sha, arch, "release", fingerprints[arch], certificate, sdk, jdk)
            current = validate_snapshot(
                json.loads((directory / SOURCE_FILENAME).read_text()), identity, sha)
            if snapshot is not None and snapshot != current:
                raise ValueError("Final APKs contain conflicting source snapshots")
            snapshot = current

    primary_names = {f"Argon-{arch}-{sha}-{fingerprint}"
                     for arch, fingerprint in fingerprints.items()}
    for item in artifacts:
        name = item["name"]
        if (name not in primary_names and name != "argon-test-source"
                and not name.endswith(".dockerbuild")
                and not re.fullmatch(r"argon-continuation-[1-9][0-9]*-(?:arm64|arm)", name)):
            continue
        artifact_id = positive_number(str(item["id"]))
        if artifact_id in final_ids.values():
            raise ValueError("Refusing to delete a final APK artifact")
        subprocess.run(["gh", "api", "--method", "DELETE",
                        f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/artifacts/{artifact_id}"],
                       check=True)
        print(f"Удалён служебный артефакт {name}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("source", "plan", "collect", "prune-artifacts"))
    parser.add_argument("--checkout-root", type=Path,
                        help="Pinned source checkout when this helper is staged outside it")
    args = parser.parse_args()
    if args.checkout_root is not None:
        ROOT = args.checkout_root.resolve()
    try:
        {"source": source, "plan": plan, "collect": collect,
         "prune-artifacts": prune_artifacts}[args.action]()
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
