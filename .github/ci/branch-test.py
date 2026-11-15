#!/usr/bin/env python3
"""Build a pinned temporary-branch revision while keeping Actions cache on main."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ".github/workflows/build-test-branch.yml"
WORKER = ".github/workflows/build-test-main.yml"


def api(path, payload=None):
    args = ["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def emit(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for name, value in values.items():
            if "\n" in str(value) or "\r" in str(value):
                raise ValueError("Multiline workflow output")
            output.write(f"{name}={value}\n")


def source_run(run_id):
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise ValueError("Invalid source run ID")
    run = api(f"actions/runs/{run_id}")
    repository = os.environ["GITHUB_REPOSITORY"]
    branch = run.get("head_branch", "")
    if (run.get("id") != int(run_id) or run.get("path") != ENTRY
            or run.get("event") != "workflow_dispatch"
            or run.get("repository", {}).get("full_name") != repository
            or run.get("head_repository", {}).get("full_name") != repository
            or not branch or branch == "main" or branch.startswith("refs/")
            or any(char in branch for char in "\r\n")
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))):
        raise ValueError("Expected a manual temporary-branch test run in this repository")
    return run


def dispatch(max_polls=30):
    ref = os.environ["GITHUB_REF"]
    if (os.environ.get("GITHUB_REF_TYPE") != "branch"
            or not ref.startswith("refs/heads/") or ref == "refs/heads/main"):
        raise ValueError("Select a temporary branch; main and tags are not accepted")
    run_id = os.environ["GITHUB_RUN_ID"]
    run = source_run(run_id)
    if run["head_sha"] != os.environ["GITHUB_SHA"] or ref != "refs/heads/" + run["head_branch"]:
        raise ValueError("Source ref or SHA does not match the current run")
    if os.environ["TEST_AUTO_CONTINUE"] not in ("true", "false"):
        raise ValueError("Invalid automatic continuation selection")
    try:
        api(f"actions/workflows/{Path(WORKER).name}/dispatches", {
            "ref": "main", "inputs": {
                "source_run": run_id,
                "runner": os.environ["TEST_RUNNER"],
                "auto_continue": os.environ["TEST_AUTO_CONTINUE"],
            },
        })
    except subprocess.CalledProcessError:
        raise ValueError("Install build-test-main.yml in main before launching branch tests") from None
    title = f"Argon Test — исходный запуск №{run_id}"
    url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/{Path(WORKER).name}"
    for poll in range(max_polls):
        result = api(f"actions/workflows/{Path(WORKER).name}/runs?event=workflow_dispatch&branch=main&per_page=100")
        found = [item for item in result["workflow_runs"] if item.get("display_title") == title]
        if found:
            url = max(found, key=lambda item: item["id"])["html_url"]
            break
        if poll + 1 < max_polls:
            time.sleep(2)
    emit(build_url=url)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"## Тестовая сборка временной ветки\n\n"
                      f"- Исходники: `{run['head_branch']}` / `{run['head_sha']}`.\n"
                      f"- [Ход сборки и готовые APK в Artifacts]({url}).\n"
                      "- Подпись: постоянный релизный ключ; отдельное приложение Argon Test.\n"
                      "- Кеш компиляции читается и пополняется в main.\n"
                      "- Этот запуск передаёт задание; результат компиляции показан по ссылке.\n")


def source():
    if os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("The shared-cache executor must run on main")
    run = source_run(os.environ["TEST_SOURCE_RUN"])
    # A tag can have a bare head_branch name in the REST response. Require
    # evidence that the entry's branch-only guard succeeded in this attempt.
    jobs = api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    if not any(step.get("name") == "Отклонить запуск из main или тега"
               and step.get("conclusion") == "success"
               for job in jobs["jobs"] for step in job.get("steps", [])):
        raise ValueError("Source run has not passed its temporary-branch guard")
    emit(sha=run["head_sha"])
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"## Источник тестовой сборки\n\n"
                      f"- Ветка: `{run['head_branch']}`.\n- Коммит: `{run['head_sha']}`.\n"
                      "- Область записи кеша GitHub Actions: `refs/heads/main`.\n")


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



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dispatch", "source", "plan"))
    args = parser.parse_args()
    try:
        {"dispatch": dispatch, "source": source, "plan": plan}[args.action]()
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
