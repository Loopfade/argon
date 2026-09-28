#!/usr/bin/env python3
"""Resume the original build run without losing its PR-scoped compiler cache."""
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

WORKFLOW = ".github/workflows/build.yml"


def api(repository, path, payload=None):
    command = ["gh", "api", f"repos/{repository}/{path}"]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def number(value, minimum=1):
    if not re.fullmatch(r"0|[1-9][0-9]*", value) or int(value) < minimum:
        raise ValueError("Invalid continuation counter or run ID")
    return int(value)


def check_run(run, repository, run_id):
    if (run["id"] != run_id or run["path"] != WORKFLOW
            or run["repository"]["full_name"] != repository
            or run["head_repository"]["full_name"] != repository
            or run["event"] not in ("pull_request", "push", "workflow_dispatch", "workflow_run")):
        raise ValueError("Refusing to continue an unrelated or fork workflow")


def check_revision(run, repository, checkout_sha):
    branch = quote(run["head_branch"], safe="/")
    head = api(repository, f"git/ref/heads/{branch}")["object"]["sha"]
    if head != run["head_sha"]:
        raise ValueError("Branch changed; refusing to resume stale code")
    if run["event"] == "pull_request":
        if len(run["pull_requests"]) != 1:
            raise ValueError("Expected one pull request for this run")
        pr = api(repository, f"pulls/{run['pull_requests'][0]['number']}")
        if (pr["state"] != "open" or pr["head"]["repo"]["full_name"] != repository
                or pr["head"]["sha"] != head or pr["merge_commit_sha"] != checkout_sha):
            raise ValueError("Pull request head/base changed; refusing a stale merge build")
    elif head != checkout_sha:
        raise ValueError("Checkout does not match the branch commit")


def schedule(env):
    repository = env["GITHUB_REPOSITORY"]
    run_id = number(env["GITHUB_RUN_ID"])
    attempt = number(env["GITHUB_RUN_ATTEMPT"])
    count = number(env.get("CONTINUATION_COUNT", "0"), minimum=0) + attempt - 1
    if count >= number(env["AUTO_CONTINUE_MAX"]):
        raise ValueError("Automatic continuation limit reached")
    run = api(repository, f"actions/runs/{run_id}")
    check_run(run, repository, run_id)
    if run["run_attempt"] != attempt:
        raise ValueError("The build attempt changed")
    check_revision(run, repository, env["GITHUB_SHA"])
    # Dispatch only a small controller on the head branch. The actual build
    # is rerun with the original event/ref/SHA and can read the PR cache.
    api(repository, "actions/workflows/build.yml/dispatches", {
        "ref": run["head_branch"],
        "inputs": {"resume_run": str(run_id), "resume_attempt": str(attempt),
                   "resume_sha": env["GITHUB_SHA"], "signing": "test"},
    })
    print(f"Queued continuation {count + 1} for run {run_id}, attempt {attempt}")


def resume(env, max_polls=120):
    repository = env["GITHUB_REPOSITORY"]
    run_id = number(env["RESUME_RUN"])
    attempt = number(env["RESUME_ATTEMPT"])
    if attempt > number(env["AUTO_CONTINUE_MAX"]):
        raise ValueError("Automatic continuation limit reached")
    checkout_sha = env["RESUME_SHA"]
    if not re.fullmatch(r"[0-9a-f]{40}", checkout_sha):
        raise ValueError("Invalid checkout SHA")
    for poll in range(max_polls):
        run = api(repository, f"actions/runs/{run_id}")
        check_run(run, repository, run_id)
        if run["run_attempt"] > attempt:
            print("The run was already retried; no duplicate continuation needed")
            return
        if run["run_attempt"] != attempt or run["head_sha"] != env["GITHUB_SHA"]:
            raise ValueError("Controller does not match the original run/commit")
        if run["status"] == "completed":
            break
        if poll + 1 < max_polls:
            time.sleep(5)
    else:
        raise ValueError("The source run did not finish cleanup in time")
    if run["conclusion"] != "failure":
        raise ValueError("Only a failed run may be continued; preserving cancellations")
    check_revision(run, repository, checkout_sha)
    api(repository, f"actions/runs/{run_id}/rerun-failed-jobs", {})
    print(f"Resumed original run {run_id}; its event, SHA and cache scope are preserved")


if __name__ == "__main__":
    try:
        {"schedule": schedule, "resume": resume}[sys.argv[1]](os.environ)
    except (ValueError, KeyError, IndexError, TypeError, subprocess.CalledProcessError) as error:
        print(f"::error::Build continuation failed: {error}", file=sys.stderr)
        sys.exit(1)
