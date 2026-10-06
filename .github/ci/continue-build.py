#!/usr/bin/env python3
"""Resume the original build run without losing its event, SHA or cache scope."""
import json
import os
import re
import subprocess
import sys
import time
from urllib.parse import quote

WORKFLOW = ".github/workflows/build.yml"
CONTROLLER_WORKFLOW = ".github/workflows/continue-build.yml"
BUILD_JOBS = {
    f"Build and verify {arch} / Build, sign and verify APK ({arch})": arch
    for arch in ("arm64", "arm")
}


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
            or run["event"] not in ("pull_request", "workflow_dispatch")):
        raise ValueError("Refusing to continue an unrelated or unsupported workflow")


def check_revision(run, repository, checkout_sha):
    if run["event"] == "workflow_dispatch":
        if run["head_sha"] != checkout_sha:
            raise ValueError("Checkout does not match the original manual run")
        return

    branch = quote(run["head_branch"], safe="/")
    head = api(repository, f"git/ref/heads/{branch}")["object"]["sha"]
    if head != run["head_sha"]:
        raise ValueError("Branch changed; refusing to resume stale code")
    if len(run["pull_requests"]) != 1:
        raise ValueError("Expected one pull request for this run")
    pr = api(repository, f"pulls/{run['pull_requests'][0]['number']}")
    if (pr["state"] != "open" or pr["head"]["repo"]["full_name"] != repository
            or pr["head"]["sha"] != head or pr["merge_commit_sha"] != checkout_sha):
        raise ValueError("Pull request head/base changed; refusing a stale merge build")


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

    api(repository, "actions/workflows/continue-build.yml/dispatches", {
        "ref": "main",
        "inputs": {"resume_run": str(run_id), "resume_attempt": str(attempt),
                   "resume_sha": env["GITHUB_SHA"]},
    })
    print(f"Queued continuation {count + 1} for run {run_id}, attempt {attempt}")


def continuation_jobs(repository, run_id, attempt):
    """Select jobs with a saved cache and this attempt's budget marker."""
    response = api(repository, f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    jobs = response.get("jobs")
    if (not isinstance(jobs, list) or response.get("total_count") != len(jobs)
            or len(jobs) >= 100):
        raise ValueError("Incomplete job list; refusing an unbounded retry")
    response = api(repository, f"actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = response.get("artifacts")
    if (not isinstance(artifacts, list) or response.get("total_count") != len(artifacts)
            or len(artifacts) >= 100):
        raise ValueError("Incomplete artifact list; refusing an unbounded retry")
    markers = {item["name"] for item in artifacts if item.get("expired") is False}
    failed = [job for job in jobs if job.get("conclusion") in ("failure", "timed_out")]
    eligible = [job for job in failed
                if job.get("name") in BUILD_JOBS
                and f"argon-continuation-{attempt}-{BUILD_JOBS[job['name']]}" in markers]
    if not eligible:
        raise ValueError("No failed job has a saved-cache budget marker for this attempt")
    return failed, eligible


def resume(env, max_polls=120):
    repository = env["GITHUB_REPOSITORY"]
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Continuation controller must run from main")

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
        if run["run_attempt"] != attempt:
            raise ValueError("Controller does not match the original run attempt")
        if run["status"] == "completed":
            break
        if poll + 1 < max_polls:
            time.sleep(5)
    else:
        raise ValueError("The source run did not finish cleanup in time")

    if run["conclusion"] != "failure":
        raise ValueError("Only a failed run may be continued; preserving cancellations")
    check_revision(run, repository, checkout_sha)
    failed, eligible = continuation_jobs(repository, run_id, attempt)
    if len(eligible) == 1:
        # Always retry by ID: a previous attempt's compiler/OOM failure can be
        # absent from the current attempt's job list and must stay untouched.
        api(repository, f"actions/jobs/{eligible[0]['id']}/rerun", {})
    elif len(eligible) == len(failed) == len(BUILD_JOBS):
        # One request keeps both budget-limited architectures in one attempt.
        api(repository, f"actions/runs/{run_id}/rerun-failed-jobs", {})
    else:
        raise ValueError("Cannot group eligible jobs without retrying unrelated failures")
    print(f"Resumed original run {run_id}; its event, SHA and cache scope are preserved")


if __name__ == "__main__":
    try:
        {"schedule": schedule, "resume": resume}[sys.argv[1]](os.environ)
    except (ValueError, KeyError, IndexError, TypeError, subprocess.CalledProcessError) as error:
        print(f"::error::Build continuation failed: {error}", file=sys.stderr)
        sys.exit(1)
