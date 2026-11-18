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


def source():
    if os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("Run the test workflow from main")
    attempt = positive_number(os.environ["GITHUB_RUN_ATTEMPT"])
    identity = {
        "schema": 1, "workflow": WORKFLOW,
        "repository": os.environ["GITHUB_REPOSITORY"],
        "run_id": positive_number(os.environ["GITHUB_RUN_ID"]),
        "run_number": positive_number(os.environ["GITHUB_RUN_NUMBER"]),
        "pr_number": positive_number(os.environ["TEST_PR_NUMBER"]),
    }
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
        selected = json.loads(path.read_text())
        if (not isinstance(selected, dict)
                or any(type(selected.get(key)) is not type(value) or selected.get(key) != value
                       for key, value in identity.items())):
            raise ValueError("Pinned source does not belong to this PR build run")
        validate_source(selected.get("branch"), selected.get("sha"))
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


def collect():
    """Verify both APKs from this run, including successful earlier attempts."""
    sha = check_checkout()
    run_id = positive_number(os.environ["GITHUB_RUN_ID"])
    attempt = positive_number(os.environ["GITHUB_RUN_ATTEMPT"])
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
    pages = pipeline.api(f"actions/runs/{run_id}/artifacts?per_page=100", paginate=True)
    artifacts = [item for page in pages for item in page["artifacts"]]
    selected = {}
    for arch in ("arm64", "arm"):
        expected = pipeline.artifact_fingerprint(
            sha, arch, "release", certificate, prepared, True, identity["version_code"])
        if fingerprints[arch] != expected:
            raise ValueError("Test artifact fingerprint changed after planning")
        name = f"Argon-{arch}-{sha}-{expected}"
        matches = [item for item in artifacts if item["name"] == name and not item["expired"]]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one available {arch} APK in this run")
        selected[arch] = matches[0]
        positive_number(str(selected[arch]["id"]))

    jdk = Path(os.environ["JAVA_HOME"])
    sdk = Path(os.environ.get("ANDROID_HOME") or os.environ["ANDROID_SDK_ROOT"])
    # Verify the complete pair before exposing either output to upload-artifact.
    # The immutable per-architecture build artifacts remain untouched.
    with tempfile.TemporaryDirectory(prefix="argon-test-apks-") as temporary:
        verified = {}
        for arch, item in selected.items():
            destination = Path(temporary) / arch
            destination.mkdir()
            payload = pipeline.extract_artifact(item["id"], destination)
            verified[arch] = pipeline.verify_artifact(
                payload, sha, arch, "release", fingerprints[arch], certificate, sdk, jdk)
        for arch, directory in verified.items():
            shutil.copytree(directory, ROOT / "artifacts" / pipeline.TARGET_ABIS[arch])

    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"## Оба тестовых APK для попытки №{attempt}\n\n"
                      f"Коммит: `{sha}`. Проверены подпись, происхождение и контрольные суммы.\n\n"
                      "Готовые файлы прикрепляются к этой попытке как "
                      "`Argon-Test-arm64-v8a` и `Argon-Test-armeabi-v7a`. "
                      "Повторная компиляция не выполняется.\n")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("source", "plan", "collect"))
    args = parser.parse_args()
    try:
        {"source": source, "plan": plan, "collect": collect}[args.action]()
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
