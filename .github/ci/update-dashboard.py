#!/usr/bin/env python3
"""Publish an idempotent dashboard snapshot with every build attempt."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote
import zipfile
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from release_policy import unsafe_release_payload_drift

RUN_FIELDS = (
    "id", "run_number", "event", "status", "conclusion", "head_branch", "path", "display_title",
    "head_sha", "html_url", "created_at", "updated_at", "run_started_at", "run_attempt",
)
JOB_FIELDS = ("id", "name", "status", "conclusion", "html_url", "started_at", "completed_at")
STEP_FIELDS = ("name", "status", "conclusion", "number", "started_at", "completed_at")
DATA_BRANCH = "dashboard"


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
    match = re.search(r"(?:Build Argon run #|Сборка Argon №)([0-9]+)", release.get("body") or "")
    return match.group(1) if match else None


def same_payload(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return ({key: value for key, value in left.items() if key != "generated_at"} ==
            {key: value for key, value in right.items() if key != "generated_at"})


# Only these entry points compile APKs. Helpers, validation and Pages are excluded.
RELEASE_WORKFLOWS = {".github/workflows/build-all.yml"}
TEST_WORKFLOWS = {".github/workflows/build-test-branch.yml", ".github/workflows/build-test-main.yml"}
LEGACY_WORKFLOWS = {".github/workflows/build.yml"}
FINAL_TEST_APKS = {"Argon-Test-arm64-v8a", "Argon-Test-armeabi-v7a"}
MOSCOW = ZoneInfo("Europe/Moscow")
SHA = re.compile(r"[0-9a-f]{40}")


def iso(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def collection(path, key=None):
    pages = api(path, paginate=True)
    return [item for page in pages for item in (page[key] if key else page)]


def pr_summary(pr):
    return {**select(pr, ("number", "title", "html_url", "state", "draft", "created_at", "updated_at", "merged_at")),
            "head_sha": (pr.get("head") or {}).get("sha"),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "author": (pr.get("user") or {}).get("login")}


def test_source(repository, run, artifacts, jobs=None):
    # A manual PR build's head_sha belongs to main, not to the compiled PR.
    # Read the pinned source JSON; never substitute today's PR head.
    source = next((a for a in artifacts if a["name"] == "argon-test-source" and not a.get("expired")), None)
    if source:
        try:
            blob = subprocess.check_output(["gh", "api", f"repos/{repository}/actions/artifacts/{source['id']}/zip"])
        except subprocess.CalledProcessError:
            pass  # The collector can remove the source archive after this listing.
        else:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                info = archive.getinfo("source.json")
                if info.file_size > 16_384:
                    raise ValueError("Oversized test source snapshot")
                data = json.loads(archive.read(info))
            if (data.get("schema") != 1 or data.get("repository") != repository
                    or data.get("workflow") != ".github/workflows/build-test-branch.yml"
                    or data.get("run_id") != run["id"] or data.get("run_number") != run.get("run_number")
                    or not SHA.fullmatch(data.get("sha", ""))
                    or type(data.get("pr_number")) is not int or data["pr_number"] < 1):
                raise ValueError("Unrelated test source snapshot")
            return {"source_sha": data["sha"], "pr_number": data["pr_number"], "source_verified": True}
    sources = {m[1] for a in artifacts
               if (m := re.match(r"^Argon-(?:arm64|arm)-([0-9a-f]{40})-", a["name"]))}
    if not sources:
        path = run.get("path")
        plan_name = None
        if path == ".github/workflows/build-test-main.yml":
            plan_name = "Проверить исходную ветку и закрепить коммит"
        elif (path == ".github/workflows/build-test-branch.yml"
              and any(item["name"] in FINAL_TEST_APKS for item in artifacts)):
            plan_name = "Проверить PR и закрепить исходный коммит"
        plan = next((job for job in jobs or [] if job["name"] == plan_name), None) if plan_name else None
        if plan:
            # This is the pinned value passed to the plan step, also available
            # for completed runs whose final APKs predate embedded journals.
            try:
                log = subprocess.check_output(["gh", "api", f"repos/{repository}/actions/jobs/{plan['id']}/logs"], text=True)
            except subprocess.CalledProcessError:
                pass
            else:
                sources = set(re.findall(
                    r"(?m)^(?:\S+Z)?[ \t]*BUILD_SOURCE_SHA:[ \t]*([0-9a-f]{40})[ \t\r]*$", log))
    title = re.search(r"PR[ №#]+([1-9][0-9]*)", run.get("display_title") or "")
    return {"source_sha": next(iter(sources)) if len(sources) == 1 else None,
            "pr_number": int(title[1]) if title else None, "source_verified": len(sources) == 1}


def read_test_source(repository, run, artifacts, jobs):
    try:
        return test_source(repository, run, artifacts, jobs)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile, subprocess.CalledProcessError):
        return {"source_sha": None, "pr_number": None, "source_verified": False}


def collect_build(repository, raw_run, now, previous=None):
    if previous and all(previous["run"].get(k) == raw_run.get(k)
                        for k in ("id", "status", "conclusion", "run_attempt", "updated_at")) \
            and raw_run.get("status") == "completed":
        cached = json.loads(json.dumps(previous))
        if (cached.get("kind") == "test" and not cached.get("source_verified")
                and any(item["name"] in FINAL_TEST_APKS for item in cached["artifacts"])):
            cached.update(read_test_source(repository, raw_run, cached["artifacts"], cached["jobs"]))
        return cached
    path = f"repos/{repository}/actions/runs/{raw_run['id']}"
    count = raw_run.get("run_attempt") or 1
    attempts = []
    for number in range(1, count + 1):
        metadata = raw_run if number == count else api(f"{path}/attempts/{number}")
        jobs = []
        for raw_job in collection(f"{path}/attempts/{number}/jobs?per_page=100", "jobs"):
            job = select(raw_job, JOB_FIELDS)
            job["run_attempt"] = raw_job.get("run_attempt") or number
            job["steps"] = [select(step, STEP_FIELDS) for step in raw_job.get("steps") or []]
            jobs.append(job)
        attempts.append({"number": number, "run_started_at": metadata.get("run_started_at"),
                         "conclusion": metadata.get("conclusion"), "jobs": jobs})
    jobs = aggregate_jobs(attempts)
    artifacts = [select(a, ("id", "name", "size_in_bytes", "expired", "created_at", "expires_at"))
                 for a in collection(f"{path}/artifacts?per_page=100", "artifacts")]
    run = select(raw_run, RUN_FIELDS)
    starts = [a["run_started_at"] for a in attempts if a.get("run_started_at")]
    starts += [j["started_at"] for j in jobs if j.get("started_at")]
    run["first_started_at"] = min(starts) if starts else raw_run.get("created_at")
    ends = [j["completed_at"] for j in jobs if j.get("completed_at")]
    run["completed_at"] = (max(ends) if ends else raw_run.get("updated_at")) if run["status"] == "completed" else None
    run["duration_ms"] = milliseconds(run["first_started_at"], run["completed_at"] or iso(now))
    # Successful jobs can reappear in a partial retry: count each job ID once.
    seen = set()
    run["runner_ms"] = sum(milliseconds(j.get("started_at"), j.get("completed_at") or iso(now)) or 0
                           for a in attempts for j in a["jobs"]
                           if j.get("started_at") and j["id"] not in seen and not seen.add(j["id"]))
    kind = "test" if run["path"] in TEST_WORKFLOWS else "release" if run["path"] in RELEASE_WORKFLOWS else "legacy"
    if kind == "legacy" and any(j["conclusion"] == "success" and j["name"].startswith(
            ("Publish verified dual-ARM release", "Опубликовать проверенный релиз для двух архитектур ARM")) for j in jobs):
        kind = "release"
    if (kind == "test" and run["status"] == "completed" and len(jobs) == 1
            and jobs[0]["name"] == "Передать коммит ветки исполнителю с кешем main"):
        kind = "dispatch"
    result = {"kind": kind, "run": run, "jobs": jobs, "attempts": attempts, "artifacts": artifacts}
    if kind == "test":
        if previous and previous.get("source_verified"):
            result.update({k: previous.get(k) for k in ("source_sha", "pr_number", "source_verified")})
        else:
            result.update(read_test_source(repository, raw_run, artifacts, jobs))
    else:
        result.update(source_sha=raw_run.get("head_sha"), source_verified=True, pr_number=None)
    return result


class Comparisons:
    def __init__(self, repository, cached=None):
        self.repository, self.cached, self.used = repository, cached or {}, {}

    def get(self, base, head):
        if not head or not SHA.fullmatch(head) or (base and not SHA.fullmatch(base)):
            return None
        key = f"{base or 'root'}...{head}"
        if key in self.used:
            return self.used[key]
        if key in self.cached:
            self.used[key] = self.cached[key]
            return self.used[key]
        root = f"repos/{self.repository}"
        try:
            if base:
                pages = api(f"{root}/compare/{base}...{head}?per_page=100", paginate=True)
                first = pages[0]
                commits = {c["sha"] for p in pages for c in p.get("commits", [])}
                files = first.get("files", [])
                complete = len(commits) == first.get("total_commits", len(commits))
                result = {"base_sha": base, "head_sha": head, "status": first.get("status"),
                          "html_url": f"https://github.com/{self.repository}/compare/{base}...{head}",
                          "commits": first.get("total_commits", len(commits)), "commit_shas": sorted(commits),
                          "commits_complete": complete, "files_complete": len(files) < 300,
                          "changed_files": len(files), "additions": sum(f.get("additions", 0) for f in files),
                          "deletions": sum(f.get("deletions", 0) for f in files),
                          "files": [select(f, ("filename", "previous_filename", "status", "additions", "deletions"))
                                    for f in files]}
            else:
                commits, complete = [], False
                for page in range(1, 11):
                    rows = api(f"{root}/commits?sha={head}&per_page=100" + (f"&page={page}" if page > 1 else ""))
                    commits.extend(rows)
                    if len(rows) < 100:
                        complete = True
                        break
                result = {"base_sha": None, "head_sha": head, "status": "initial", "html_url": None,
                          "commits": len(commits), "commit_shas": sorted({c["sha"] for c in commits}),
                          "commits_complete": complete, "files_complete": False, "changed_files": None,
                          "additions": None, "deletions": None, "files": []}
            self.used[key] = result
            return result
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
            return None


def included_prs(comparison, merged):
    if not comparison or not comparison["commits_complete"]:
        return None
    commits = set(comparison["commit_shas"])
    return [pr_summary(pr) for pr in merged if pr.get("merge_commit_sha") in commits]


def public_comparison(value):
    return {k: v for k, v in value.items() if k != "commit_shas"} if value else None


def build_snapshot(repository, event="workflow_dispatch", trigger="", *, now=None, previous=None):
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(MOSCOW).date()
    first_day = today - timedelta(days=29)
    since = iso(datetime.combine(first_day, datetime.min.time(), MOSCOW))
    root = f"repos/{repository}"
    previous = previous if previous and previous.get("schema_version") == 2 else {}
    old_builds = {b["run"]["id"]: b for b in previous.get("builds", [])}
    comparisons = Comparisons(repository, previous.get("comparison_cache"))
    catalog = [r for r in collection(f"{root}/releases?per_page=100")
               if not r.get("draft") and r.get("published_at")]
    catalog.sort(key=lambda r: r["published_at"], reverse=True)
    raw_runs = collection(f"{root}/actions/runs?event=workflow_dispatch&created={quote('>=' + since, safe='')}&per_page=100", "workflow_runs")
    # GitHub returns at most 1000 filtered runs. Mark a full window as partial.
    truncated = len(raw_runs) >= 1000
    raw_runs = [r for r in raw_runs if r.get("path", "").split("@", 1)[0] in
                RELEASE_WORKFLOWS | TEST_WORKFLOWS | LEGACY_WORKFLOWS]
    for run in raw_runs:
        run["path"] = run["path"].split("@", 1)[0]
    run_index = {r["id"]: r for r in raw_runs}
    merged = [p for p in collection(f"{root}/pulls?state=closed&base=main&per_page=100") if p.get("merged_at")]
    main = api(f"{root}/git/ref/heads/main")["object"]["sha"]
    releases = []
    # Keep all releases in the window plus the latest APK even in quiet months.
    selected = [r for r in catalog if r["published_at"] >= since] or catalog[:1]
    needed = {i for i, r in enumerate(catalog) if r in selected}
    needed |= {i + 1 for i in list(needed) if i + 1 < len(catalog)}
    release_sources = {}
    for index in sorted(needed):
        release = catalog[index]
        reference = run_reference(release)
        if not reference:
            release_sources[index] = None
            continue
        raw = run_index.get(int(reference)) or api(f"{root}/actions/runs/{reference}")
        path = raw.get("path", "").split("@", 1)[0]
        source = raw.get("head_sha")
        valid = (path in RELEASE_WORKFLOWS | LEGACY_WORKFLOWS and raw.get("event") == "workflow_dispatch"
                 and raw.get("conclusion") == "success" and SHA.fullmatch(source or ""))
        if valid and release.get("target_commitish") != source:
            drift = comparisons.get(release.get("target_commitish"), source)
            valid = bool(drift and not unsafe_release_payload_drift({"status": drift["status"], "files": drift["files"]}))
        release_sources[index] = source if valid else None
    for index, release in enumerate(catalog):
        if release not in selected:
            continue
        source, base = release_sources.get(index), release_sources.get(index + 1)
        comparison = comparisons.get(base, source) if source and (base or index + 1 == len(catalog)) else None
        prs = included_prs(comparison, merged)
        clean = select(release, ("id", "html_url", "tag_name", "name", "published_at"))
        clean.update(source_sha=source, source_run_id=int(run_reference(release)) if run_reference(release) else None,
                     previous_sha=base, pr_count=len(prs) if prs is not None else None,
                     included_prs=prs or [], comparison=public_comparison(comparison), initial_release=index + 1 == len(catalog))
        ancestry = included_prs(comparisons.get(None, source), merged) if source else None
        clean["total_pr_count"] = len(ancestry) if ancestry is not None else None
        clean["assets"] = [select(a, ("name", "size", "browser_download_url")) for a in release.get("assets", [])]
        releases.append(clean)
    release_by_run = {r["source_run_id"]: r for r in releases if r["source_run_id"]}
    builds = [collect_build(repository, r, now, old_builds.get(r["id"])) for r in raw_runs]
    builds = [b for b in builds if b["kind"] != "dispatch"]
    builds.sort(key=lambda b: (b["run"].get("created_at") or "", b["run"]["id"]), reverse=True)
    latest_release = releases[0] if releases else None
    baseline = latest_release and latest_release["source_sha"]
    for build in builds:
        release = release_by_run.get(build["run"]["id"])
        build["release"] = release
        if release:
            build.update(pr_count=release["pr_count"], included_prs=release["included_prs"], comparison=release["comparison"])
        else:
            prior = next((i for i, r in enumerate(catalog)
                          if r["published_at"] <= (build["run"].get("created_at") or "")), None)
            anchor = baseline if build["kind"] == "test" else release_sources.get(prior)
            diff = comparisons.get(anchor, build.get("source_sha")) if anchor else None
            prs = included_prs(diff, merged) if build["kind"] != "test" else None
            build.update(pr_count=len(prs) if prs is not None else None,
                         included_prs=prs or [], comparison=public_comparison(diff))
            build["baseline_tag"] = (latest_release or {}).get("tag_name") if build["kind"] == "test" else catalog[prior]["tag_name"] if prior is not None else None
    open_prs = []
    for item in collection(f"{root}/pulls?state=open&base=main&per_page=100"):
        pr = api(f"{root}/pulls/{item['number']}")
        summary = pr_summary(pr)
        summary.update(select(pr, ("additions", "deletions", "changed_files", "commits")))
        summary["comparison"] = public_comparison(comparisons.get(baseline, summary["head_sha"])) if baseline else None
        summary["test_runs"] = [{"id": b["run"]["id"], "html_url": b["run"]["html_url"],
                                  "status": b["run"]["status"], "conclusion": b["run"]["conclusion"],
                                  "source_sha": b.get("source_sha"),
                                  "current": b.get("source_sha") == summary["head_sha"]}
                                 for b in builds if b["kind"] == "test" and b.get("pr_number") == pr["number"]]
        open_prs.append(summary)
    diff = comparisons.get(baseline, main) if baseline else None
    pending = included_prs(diff, merged)
    days = []
    for offset in range(30):
        day = first_day + timedelta(days=offset)
        daily = [b for b in builds if timestamp(b["run"].get("created_at"))
                 and timestamp(b["run"]["created_at"]).astimezone(MOSCOW).date() == day]
        days.append({"date": day.isoformat(), "release": sum(b["kind"] == "release" for b in daily),
                     "test": sum(b["kind"] == "test" for b in daily),
                     "legacy": sum(b["kind"] == "legacy" for b in daily),
                     "failed": sum(b["run"].get("conclusion") in ("failure", "timed_out", "startup_failure") for b in daily),
                     "builds": len(daily)})
    return {"schema_version": 2, "generated_at": iso(now), "repository": repository,
            "window": {"days": 30, "start": first_day.isoformat(), "end": today.isoformat(), "timezone": "Europe/Moscow", "partial": truncated},
            "builds": builds, "releases": releases, "release": latest_release,
            "latest": builds[0]["run"] if builds else None, "runs": [b["run"] for b in builds],
            "open_prs": open_prs, "unreleased": {"base_sha": baseline, "head_sha": main,
                "comparison": public_comparison(diff), "pr_count": len(pending) if pending is not None else None,
                "included_prs": pending or []}, "days": days, "comparison_cache": comparisons.used}


def snapshot_branch(repository, *, create=False):
    ref = f"refs/heads/{DATA_BRANCH}"
    refs = api(f"repos/{repository}/git/matching-refs/heads/{DATA_BRANCH}")
    if any(item["ref"] == ref for item in refs):
        return True
    if not create:
        return False
    main = api(f"repos/{repository}/git/ref/heads/main")
    api(f"repos/{repository}/git/refs", "POST", {"ref": ref, "sha": main["object"]["sha"]})
    return True


def saved_snapshot(repository):
    path = f"repos/{repository}/contents/dashboard-data.json"
    # API/decoding errors must stop publication; never overwrite unknown data.
    current = api(path + f"?ref={DATA_BRANCH}")
    existing = None
    # Retry histories can exceed the Contents API's 1 MB inline-content limit.
    if current.get("encoding") == "none":
        existing = api(path + f"?ref={DATA_BRANCH}", raw=True)
    elif current.get("content"):
        existing = json.loads(base64.b64decode(current["content"]))
    return current, existing


def publish(repository, snapshot):
    snapshot_branch(repository, create=True)
    current, existing = saved_snapshot(repository)
    if same_payload(existing, snapshot):
        print("В служебной ветке уже находятся те же данные панели; обновление не требуется.")
        return
    payload = {"message": "Обновить данные панели Argon", "branch": DATA_BRANCH,
               "content": base64.b64encode((json.dumps(snapshot, indent=2) + "\n").encode()).decode(),
               "sha": current["sha"]}
    api(f"repos/{repository}/contents/dashboard-data.json", "PUT", payload)
    api(f"repos/{repository}/actions/workflows/deploy-dashboard.yml/dispatches", "POST", {"ref": "main"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--publish", action="store_true")
    action.add_argument("--read-published", action="store_true")
    args = parser.parse_args()
    repository = os.environ["GH_REPO"]
    target = Path("dashboard-data.json")
    if args.read_published:
        if snapshot_branch(repository):
            _, snapshot = saved_snapshot(repository)
            if not isinstance(snapshot, dict):
                raise ValueError("Invalid published dashboard snapshot")
            target.write_text(json.dumps(snapshot, indent=2) + "\n")
        # Before the first update, use the snapshot already checked out.
        return
    if args.publish:
        publish(repository, json.loads(target.read_text()))
        return
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("updated=false\n")
    if os.environ["EVENT_NAME"] == "push" and "[history-only]" in os.environ.get("HEAD_COMMIT_MESSAGE", ""):
        print("Изменение только истории сохраняет опубликованные данные панели.")
        return
    previous = None
    if snapshot_branch(repository):
        _, previous = saved_snapshot(repository)
    snapshot = build_snapshot(repository, os.environ["EVENT_NAME"], os.environ.get("TRIGGER_RUN_ID", ""), previous=previous)
    if snapshot is None:
        return
    if same_payload(previous, snapshot):
        print("Данные панели не изменились; время обновления сохранено.")
        return
    target.write_text(json.dumps(snapshot, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write("updated=true\n")


if __name__ == "__main__":
    main()
