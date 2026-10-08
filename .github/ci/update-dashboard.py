#!/usr/bin/env python3
"""Publish an idempotent dashboard snapshot with every build attempt."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from release_policy import unsafe_release_payload_drift

RUN_FIELDS = (
    "id", "run_number", "event", "status", "conclusion", "head_branch",
    "head_sha", "html_url", "created_at", "updated_at", "run_started_at", "run_attempt",
)
JOB_FIELDS = ("id", "name", "status", "conclusion", "html_url", "started_at", "completed_at")
STEP_FIELDS = ("name", "status", "conclusion", "number", "started_at", "completed_at")


def api(path, method=None, payload=None, paginate=False, raw=False):
    args = ["gh", "api", path]
    if paginate:
        args += ["--paginate", "--slurp"]
    if raw:
        args += ["-H", "Accept: application/vnd.github.raw+json"]
    if method:
        args += ["--method", method, "--input", "-"]
    result = subprocess.check_output(
        args, input=json.dumps(payload) if payload is not None else None, text=True)
    return json.loads(result) if result.strip() else {}


def select(item, fields):
    return {key: item.get(key) for key in fields}


def milliseconds(start, end):
    if not start or not end:
        return None
    return max(0, int((datetime.fromisoformat(end.replace("Z", "+00:00")) -
                       datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000))


def aggregate_jobs(attempts):
    groups, seen = {}, set()
    for attempt in attempts:
        for job in attempt["jobs"]:
            # Partial reruns can expose an already-successful job again.
            if job["id"] in seen:
                continue
            seen.add(job["id"])
            groups.setdefault(job["name"], []).append(job)
    result = []
    for jobs in groups.values():
        latest = dict(jobs[-1])
        starts = [job["started_at"] for job in jobs if job.get("started_at")]
        ends = [job["completed_at"] for job in jobs if job.get("completed_at")]
        latest.update(
            started_at=min(starts) if starts else None,
            completed_at=max(ends) if ends else None,
            duration_ms=sum(milliseconds(job.get("started_at"), job.get("completed_at")) or 0 for job in jobs),
            attempts=jobs,
        )
        result.append(latest)
    return result


def run_reference(release):
    match = re.search(r"Build Argon run #([0-9]+)", release.get("body") or "")
    return match.group(1) if match else None


def same_payload(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return ({key: value for key, value in left.items() if key != "generated_at"} ==
            {key: value for key, value in right.items() if key != "generated_at"})


def build_snapshot(repository, event, trigger):
    releases = [release for release in api(f"repos/{repository}/releases?per_page=20")
                if not release.get("draft") and release.get("published_at")]
    # Check publication before fetching any jobs, attempts or artifacts.
    latest_reference = next((run_reference(release) for release in releases if run_reference(release)), None)
    if event == "workflow_run" and latest_reference != trigger:
        print(f"Build Argon run {trigger} did not publish a new release; dashboard is unchanged.")
        return None
    builds = []
    for release in releases:
        run_id = run_reference(release)
        if not run_id:
            continue
        path = f"repos/{repository}/actions/runs/{run_id}"
        raw_run = api(path)
        if (raw_run.get("name") != "Build Argon" or raw_run.get("event") != "workflow_dispatch"
                or raw_run.get("conclusion") != "success"):
            continue
        run_sha, release_sha = raw_run["head_sha"], release["target_commitish"]
        if run_sha != release_sha and unsafe_release_payload_drift(
                api(f"repos/{repository}/compare/{release_sha}...{run_sha}")):
            print(f"::warning::Skipping release {release.get('tag_name')}: APK payload drift.")
            continue
        run = select(raw_run, RUN_FIELDS)
        count = raw_run.get("run_attempt") or 1
        attempts = []
        for number in range(1, count + 1):
            metadata = raw_run if number == count else api(f"{path}/attempts/{number}")
            jobs = []
            for page in api(f"{path}/attempts/{number}/jobs?per_page=100", paginate=True):
                for raw_job in page["jobs"]:
                    job = select(raw_job, JOB_FIELDS)
                    job["run_attempt"] = raw_job.get("run_attempt") or number
                    job["steps"] = [select(step, STEP_FIELDS) for step in raw_job.get("steps", [])]
                    jobs.append(job)
            attempts.append({"number": number, "run_started_at": metadata.get("run_started_at"),
                             "conclusion": metadata.get("conclusion"), "jobs": jobs})
        jobs = aggregate_jobs(attempts)
        starts = [attempt["run_started_at"] for attempt in attempts if attempt["run_started_at"]]
        starts += [job["started_at"] for job in jobs if job["started_at"]]
        ends = [job["completed_at"] for job in jobs if job["completed_at"]]
        ends.append(release["published_at"])
        run["first_started_at"] = min(starts) if starts else raw_run.get("created_at")
        run["completed_at"] = max(ends)
        run["duration_ms"] = milliseconds(run["first_started_at"], run["completed_at"])
        artifacts = [select(item, ("id", "name", "size_in_bytes", "expired", "created_at", "expires_at"))
                     for page in api(f"{path}/artifacts?per_page=100", paginate=True) for item in page["artifacts"]]
        clean_release = select(release, ("html_url", "tag_name", "name", "published_at"))
        clean_release["assets"] = [select(asset, ("name", "size", "browser_download_url"))
                                   for asset in release.get("assets", [])]
        builds.append({"run": run, "jobs": jobs, "attempts": attempts,
                       "artifacts": artifacts, "release": clean_release})
        if len(builds) == 3:
            break
    if event == "workflow_run" and (not builds or str(builds[0]["run"]["id"]) != trigger):
        return None
    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latest": builds[0]["run"] if builds else None,
            "runs": [build["run"] for build in builds], "builds": builds,
            "release": builds[0]["release"] if builds else None}


def publish(repository, snapshot):
    path = f"repos/{repository}/contents/dashboard-data.json"
    # The committed snapshot exists. API/decoding errors must stop publication.
    current = api(path + "?ref=main")
    existing = None
    # Retry histories can exceed the Contents API's 1 MB inline-content limit.
    if current.get("encoding") == "none":
        existing = api(path + "?ref=main", raw=True)
    elif current.get("content"):
        existing = json.loads(base64.b64decode(current["content"]))
    if same_payload(existing, snapshot):
        print("Main already contains the same dashboard payload; no commit or deployment.")
        return
    payload = {"message": "pages: refresh Argon dashboard data", "branch": "main",
               "content": base64.b64encode((json.dumps(snapshot, indent=2) + "\n").encode()).decode(),
               "sha": current["sha"]}
    api(path, "PUT", payload)
    api(f"repos/{repository}/actions/workflows/deploy-dashboard.yml/dispatches", "POST", {"ref": "main"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    repository = os.environ["GH_REPO"]
    target = Path("dashboard-data.json")
    if args.publish:
        publish(repository, json.loads(target.read_text()))
        return
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("updated=false\n")
    if os.environ["EVENT_NAME"] == "push" and "[history-only]" in os.environ.get("HEAD_COMMIT_MESSAGE", ""):
        print("History-only push preserves the published dashboard snapshot.")
        return
    snapshot = build_snapshot(repository, os.environ["EVENT_NAME"], os.environ.get("TRIGGER_RUN_ID", ""))
    if snapshot is None:
        return
    if target.exists() and same_payload(json.loads(target.read_text()), snapshot):
        print("Dashboard payload is unchanged; preserving its timestamp.")
        return
    target.write_text(json.dumps(snapshot, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("updated=true\n")


if __name__ == "__main__":
    main()
