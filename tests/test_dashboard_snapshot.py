"""Retries, parallel execution and idempotent dashboard publication."""
import base64
import copy
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from test_ci_cache import ci_module

dashboard = ci_module("update-dashboard")


def job(id, name, start, end, conclusion="success"):
    return {"id": id, "name": name, "status": "completed", "conclusion": conclusion,
            "started_at": "2026-10-05T" + start + "Z", "completed_at": "2026-10-05T" + end + "Z",
            "steps": [{"name": "Warm local compiler cache (checkpoint 1/20)",
                       "conclusion": conclusion}]}


def monthly_fixture(now=None, repository="fixture/argon"):
    now = now or datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
    day = now.date().isoformat()
    release = {"id": 1, "tag_name": "v157-1", "html_url": "https://example.invalid/release",
               "draft": False, "published_at": day + "T13:01:00Z", "body": "Build Argon run #42",
               "target_commitish": "a" * 40, "assets": []}
    run = {"id": 42, "run_number": 42, "name": "Build Argon", "event": "workflow_dispatch",
           "path": ".github/workflows/build-all.yml", "status": "completed", "conclusion": "success",
           "head_sha": "a" * 40, "head_branch": "main", "run_attempt": 2,
           "created_at": day + "T01:00:00Z", "updated_at": day + "T13:02:00Z",
           "run_started_at": day + "T10:00:00Z", "html_url": "https://example.invalid/run/42"}
    def dated_job(*args):
        value = job(*args)
        return json.loads(json.dumps(value).replace("2026-10-05", day))
    first_day = now.astimezone(dashboard.MOSCOW).date() - timedelta(days=29)
    since = dashboard.iso(datetime.combine(first_day, datetime.min.time(), dashboard.MOSCOW))
    root = f"repos/{repository}"
    return {
        f"{root}/releases?per_page=100": [release],
        f"{root}/actions/runs?event=workflow_dispatch&created={dashboard.quote('>=' + since, safe='')}&per_page=100": {"total_count": 1, "workflow_runs": [run]},
        f"{root}/actions/runs/42": run,
        f"{root}/actions/runs/42/attempts/1": {"run_started_at": day + "T01:00:00Z", "conclusion": "failure"},
        f"{root}/actions/runs/42/attempts/1/jobs?per_page=100": {"jobs": [dated_job(1, "arm64", "01:00:00", "07:00:00", "failure"), dated_job(2, "arm", "01:00:00", "02:00:00")]},
        f"{root}/actions/runs/42/attempts/2/jobs?per_page=100": {"jobs": [dated_job(3, "arm64", "10:00:00", "13:00:00"), dated_job(2, "arm", "01:00:00", "02:00:00"), dated_job(4, "publish", "13:00:00", "13:02:00")]},
        f"{root}/actions/runs/42/artifacts?per_page=100": {"artifacts": []},
        f"{root}/pulls?state=closed&base=main&per_page=100": [],
        f"{root}/pulls?state=open&base=main&per_page=100": [],
        f"{root}/git/ref/heads/main": {"object": {"sha": "a" * 40}},
        f"{root}/contents/build-lock.json?ref={'a' * 40}": {
            "content": base64.b64encode(json.dumps({"chromium_version": "157.0.8103.78"}).encode()).decode()},
        f"{root}/commits?sha={'a' * 40}&per_page=100": [{"sha": "a" * 40}],
        f"{root}/compare/{'a' * 40}...{'a' * 40}?per_page=100": {"status": "identical", "total_commits": 0, "commits": [], "files": []},
    }


def respond_with(values):
    def respond(path, paginate=False, **kwargs):
        value = values[path]
        return value.get("__pages__") if paginate and isinstance(value, dict) and "__pages__" in value else [value] if paginate else value
    return respond


class DashboardSnapshotTests(unittest.TestCase):
    now = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)

    def snapshot(self, values=None, **kwargs):
        with patch.object(dashboard, "api", side_effect=respond_with(values or monthly_fixture(self.now))):
            return dashboard.build_snapshot("fixture/argon", now=self.now, **kwargs)

    def test_release_run_reference_supports_russian_and_legacy_notes(self):
        for body in ("Сборка: [Сборка Argon №42](https://example.invalid)", "Build Argon run #42"):
            self.assertEqual(dashboard.run_reference({"body": body}), "42")
        self.assertIsNone(dashboard.run_reference({"body": "Без ссылки на сборку"}))

    def test_retry_wall_time_and_runner_time_do_not_double_count_parallel_jobs(self):
        snapshot = self.snapshot()
        build = snapshot["builds"][0]
        self.assertEqual(build["run"]["duration_ms"], (12 * 60 + 2) * 60_000)
        self.assertEqual(build["run"]["runner_ms"], (10 * 60 + 2) * 60_000)
        jobs = {item["name"]: item for item in build["jobs"]}
        self.assertEqual(jobs["arm64"]["duration_ms"], 9 * 3600_000)
        self.assertEqual(jobs["arm"]["duration_ms"], 3600_000)
        self.assertEqual(len(jobs["arm"]["attempts"]), 1)
        self.assertEqual(jobs["arm64"]["attempts"][0]["conclusion"], "failure")
        self.assertEqual(len(snapshot["days"]), 30)
        self.assertEqual(snapshot["window"]["timezone"], "Europe/Moscow")
        self.assertEqual(build["chromium_version"], "157.0.8103.78")

    def test_workflow_path_selects_builds_independently_of_title(self):
        values = monthly_fixture(self.now)
        for title in ("Полная сборка Argon", "Renamed build run"):
            values["repos/fixture/argon/actions/runs/42"]["name"] = title
            self.assertEqual(self.snapshot(values)["latest"]["id"], 42)
        values["repos/fixture/argon/actions/runs/42"]["path"] = ".github/workflows/untrusted.yml"
        self.assertEqual(self.snapshot(values)["builds"], [])

    def test_month_includes_failed_test_build_and_uses_compiled_pr_sha(self):
        values = monthly_fixture(self.now)
        run = {**values["repos/fixture/argon/actions/runs/42"], "id": 43, "run_number": 43,
               "run_attempt": 1, "conclusion": "failure", "display_title": "Тестовая сборка — PR №7",
               "path": ".github/workflows/build-test-branch.yml"}
        listing = next(value for path, value in values.items() if "/actions/runs?" in path)
        listing["workflow_runs"].append(run)
        values["repos/fixture/argon/actions/runs/43/attempts/1/jobs?per_page=100"] = {"jobs": [job(5, "Собрать и проверить arm", "10:00:00", "11:00:00", "failure")]}
        values["repos/fixture/argon/actions/runs/43/artifacts?per_page=100"] = {"artifacts": [{"id": 2, "name": "Argon-arm64-" + "b" * 40 + "-test", "expired": False}]}
        values[f"repos/fixture/argon/compare/{'a' * 40}...{'b' * 40}?per_page=100"] = {"status": "ahead", "total_commits": 1, "commits": [{"sha": "b" * 40}], "files": []}
        values[f"repos/fixture/argon/contents/build-lock.json?ref={'b' * 40}"] = {
            "content": base64.b64encode(json.dumps({"chromium_version": "157.0.8103.97"}).encode()).decode()}
        snapshot = self.snapshot(values)
        test = next(b for b in snapshot["builds"] if b["kind"] == "test")
        self.assertEqual(test["source_sha"], "b" * 40)
        self.assertEqual(test["run"]["head_sha"], "a" * 40)
        self.assertEqual(test["pr_number"], 7)
        self.assertEqual(test["chromium_version"], "157.0.8103.97")
        self.assertEqual(next(b for b in snapshot["builds"] if b["kind"] == "release")["chromium_version"],
                         "157.0.8103.78")
        self.assertEqual(snapshot["days"][-1]["test"], 1)
        self.assertEqual(snapshot["days"][-1]["failed"], 1)

    def test_cached_completed_jobs_and_immutable_comparisons_avoid_repeat_requests(self):
        before = self.snapshot()
        values = monthly_fixture(self.now)
        with patch.object(dashboard, "api", side_effect=respond_with(values)) as api:
            after = dashboard.build_snapshot("fixture/argon", now=self.now, previous=before)
        paths = [call.args[0] for call in api.call_args_list]
        self.assertFalse(any("/attempts/" in path or "/compare/" in path or "/commits?" in path
                             or "/contents/build-lock.json" in path for path in paths))
        self.assertEqual(before, after)

    def test_unknown_source_does_not_fall_back_to_current_main_or_old_release(self):
        with patch.object(dashboard, "api") as api:
            self.assertIsNone(dashboard.source_chromium_version("fixture/argon", None))
            self.assertIsNone(dashboard.source_chromium_version("fixture/argon", "invalid"))
            api.assert_not_called()
        with patch.object(dashboard, "api", return_value={"content": base64.b64encode(
                json.dumps({"chromium_version": "unknown"}).encode()).decode()}):
            self.assertIsNone(dashboard.source_chromium_version("fixture/argon", "a" * 40))

    def test_source_snapshot_is_bound_to_run_and_never_uses_main_as_pr_source(self):
        run = {"id": 42, "run_number": 9, "head_sha": "a" * 40}
        data = {"schema": 1, "repository": "fixture/argon", "workflow": ".github/workflows/build-test-branch.yml",
                "run_id": 42, "run_number": 9, "pr_number": 7, "sha": "b" * 40}
        def archive(payload):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as stream:
                stream.writestr("source.json", json.dumps(payload))
            return output.getvalue()
        artifact = [{"id": 2, "name": "argon-test-source", "expired": False}]
        with patch.object(dashboard.subprocess, "check_output", return_value=archive(data)):
            self.assertEqual(dashboard.test_source("fixture/argon", run, artifact)["source_sha"], "b" * 40)
        with patch.object(dashboard.subprocess, "check_output", return_value=archive({**data, "run_id": 43})):
            with self.assertRaises(ValueError):
                dashboard.test_source("fixture/argon", run, artifact)
        self.assertIsNone(dashboard.test_source("fixture/argon", run, [])["source_sha"])

    def test_pr_count_uses_reachable_merge_commits_and_excludes_closed_unmerged_prs(self):
        values = monthly_fixture(self.now)
        merged = {"number": 7, "title": "Feature", "html_url": "https://example.invalid/pr/7", "merged_at": "2026-10-05T00:00:00Z", "merge_commit_sha": "a" * 40}
        values["repos/fixture/argon/pulls?state=closed&base=main&per_page=100"] = [merged, {**merged, "number": 8, "merged_at": None}]
        snapshot = self.snapshot(values)
        self.assertEqual(snapshot["releases"][0]["pr_count"], 1)
        self.assertEqual(snapshot["releases"][0]["total_pr_count"], 1)
        self.assertEqual(snapshot["unreleased"]["pr_count"], 0)

    def test_truncated_compare_never_claims_exact_file_or_pr_count(self):
        values = {"repos/x/compare/" + "a" * 40 + "..." + "b" * 40 + "?per_page=100": {"status": "ahead", "total_commits": 3, "commits": [{"sha": "c" * 40}], "files": [{"filename": f"f{i}"} for i in range(300)]}}
        with patch.object(dashboard, "api", side_effect=respond_with(values)):
            comparison = dashboard.Comparisons("x").get("a" * 40, "b" * 40)
        self.assertFalse(comparison["files_complete"])
        self.assertFalse(comparison["commits_complete"])
        self.assertIsNone(dashboard.included_prs(comparison, []))

    def test_timestamp_only_change_never_commits_or_dispatches(self):
        snapshot = {"generated_at": "now", "builds": [{"run": {"id": 42}}]}
        existing = {**snapshot, "generated_at": "yesterday"}
        current = {"sha": "blob", "content": base64.b64encode(json.dumps(existing).encode()).decode()}
        with patch.object(dashboard, "snapshot_branch", return_value=True), \
             patch.object(dashboard, "api", return_value=current) as api:
            dashboard.publish("fixture/argon", snapshot)
        self.assertEqual(api.call_count, 1)
        changed = copy.deepcopy(snapshot)
        changed["builds"][0]["run"]["id"] = 43
        self.assertFalse(dashboard.same_payload(snapshot, changed))

    def test_changed_payload_commits_before_deploying(self):
        with patch.object(dashboard, "snapshot_branch", return_value=True), \
             patch.object(dashboard, "api", return_value={"sha": "blob"}) as api:
            dashboard.publish("fixture/argon", {"builds": []})
        self.assertEqual([call.args[1] for call in api.call_args_list[1:]], ["PUT", "POST"])
        self.assertEqual(api.call_args_list[1].args[2]["sha"], "blob")
        self.assertEqual(api.call_args_list[1].args[2]["branch"], dashboard.DATA_BRANCH)
        self.assertEqual(dashboard.DATA_BRANCH, "dashboard")
        self.assertEqual(api.call_args_list[0].args[0],
                         "repos/fixture/argon/contents/dashboard-data.json?ref=dashboard")
        self.assertEqual(api.call_args_list[2].args[2], {"ref": "main"})

    def test_large_snapshot_uses_raw_content_and_still_skips_timestamp_only_write(self):
        snapshot = {"generated_at": "today", "builds": []}
        current = {"sha": "blob", "encoding": "none", "content": ""}
        with patch.object(dashboard, "snapshot_branch", return_value=True), \
             patch.object(dashboard, "api", side_effect=[current, {**snapshot, "generated_at": "yesterday"}]) as api:
            dashboard.publish("fixture/argon", snapshot)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[1].kwargs, {"raw": True})

    def test_first_snapshot_creates_data_branch_and_never_writes_main(self):
        responses = [[], {"object": {"sha": "a" * 40}}, {}, {"sha": "blob"}, {}, {}]
        with patch.object(dashboard, "api", side_effect=responses) as api:
            dashboard.publish("fixture/argon", {"builds": []})
        creation = api.call_args_list[2]
        self.assertEqual(creation.args[1:], ("POST", {
            "ref": "refs/heads/dashboard", "sha": "a" * 40}))
        write = api.call_args_list[4]
        self.assertEqual(write.args[1], "PUT")
        self.assertEqual(write.args[2]["branch"], "dashboard")
        self.assertEqual(api.call_args_list[5].args[2], {"ref": "main"})

    def test_similar_branch_does_not_replace_exact_snapshot_branch(self):
        with patch.object(dashboard, "api", return_value=[{
                "ref": "refs/heads/dashboard-old"}]) as api:
            self.assertFalse(dashboard.snapshot_branch("fixture/argon"))
        self.assertEqual(api.call_count, 1)
