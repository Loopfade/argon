"""Retries, parallel execution and idempotent dashboard publication."""
import base64
import copy
import json
import unittest
from unittest.mock import patch

from test_ci_cache import ci_module

dashboard = ci_module("update-dashboard")


def job(id, name, start, end, conclusion="success"):
    return {"id": id, "name": name, "status": "completed", "conclusion": conclusion,
            "started_at": "2026-10-05T" + start + "Z", "completed_at": "2026-10-05T" + end + "Z",
            "steps": [{"name": "Warm local compiler cache (checkpoint 1/20)",
                       "conclusion": conclusion}]}


class DashboardSnapshotTests(unittest.TestCase):
    def fixture(self):
        release = {"draft": False, "published_at": "2026-10-05T13:01:00Z",
                   "body": "Build Argon run #42", "target_commitish": "a" * 40, "assets": []}
        run = {"id": 42, "name": "Build Argon", "event": "workflow_dispatch", "conclusion": "success",
               "head_sha": "a" * 40, "run_attempt": 2, "run_started_at": "2026-10-05T10:00:00Z"}
        responses = {
            "repos/fixture/argon/releases?per_page=20": [release],
            "repos/fixture/argon/actions/runs/42": run,
            "repos/fixture/argon/actions/runs/42/attempts/1": {"run_started_at": "2026-10-05T01:00:00Z", "conclusion": "failure"},
            "repos/fixture/argon/actions/runs/42/attempts/1/jobs?per_page=100": [
                {"jobs": [job(1, "arm64", "01:00:00", "07:00:00", "failure")]},
                {"jobs": [job(2, "arm", "01:00:00", "02:00:00")]}],
            "repos/fixture/argon/actions/runs/42/attempts/2/jobs?per_page=100": [{"jobs": [
                job(3, "arm64", "10:00:00", "13:00:00"),
                job(2, "arm", "01:00:00", "02:00:00"),
                job(4, "publish", "13:00:00", "13:02:00")]}],
            "repos/fixture/argon/actions/runs/42/artifacts?per_page=100": [{"artifacts": []}],
        }
        return responses

    def test_failed_first_attempt_and_retry_wait_are_counted_without_adding_parallel_jobs(self):
        responses = self.fixture()
        with patch.object(dashboard, "api", side_effect=lambda path, **kw: responses[path]):
            snapshot = dashboard.build_snapshot("fixture/argon", "workflow_run", "42")
        build = snapshot["builds"][0]
        self.assertEqual(build["run"]["duration_ms"], (12 * 60 + 2) * 60_000)
        self.assertEqual(build["run"]["first_started_at"], "2026-10-05T01:00:00Z")
        jobs = {item["name"]: item for item in build["jobs"]}
        self.assertEqual(jobs["arm64"]["duration_ms"], 9 * 3600_000)
        self.assertEqual(jobs["arm"]["duration_ms"], 3600_000)
        self.assertEqual(len(jobs["arm"]["attempts"]), 1)
        self.assertEqual(jobs["arm64"]["attempts"][0]["conclusion"], "failure")
        self.assertEqual(jobs["arm64"]["attempts"][0]["steps"][0]["conclusion"], "failure")
        self.assertEqual(len(build["attempts"]), 2)

    def test_unpublished_trigger_exits_after_release_lookup(self):
        with patch.object(dashboard, "api", return_value=self.fixture()["repos/fixture/argon/releases?per_page=20"]) as api:
            self.assertIsNone(dashboard.build_snapshot("fixture/argon", "workflow_run", "99"))
        self.assertEqual(api.call_count, 1)

    def test_timestamp_only_change_never_commits_or_dispatches(self):
        snapshot = {"generated_at": "now", "builds": [{"run": {"id": 42}}]}
        existing = {**snapshot, "generated_at": "yesterday"}
        current = {"sha": "blob", "content": base64.b64encode(json.dumps(existing).encode()).decode()}
        with patch.object(dashboard, "api", return_value=current) as api:
            dashboard.publish("fixture/argon", snapshot)
        self.assertEqual(api.call_count, 1)
        changed = copy.deepcopy(snapshot)
        changed["builds"][0]["run"]["id"] = 43
        self.assertFalse(dashboard.same_payload(snapshot, changed))

    def test_changed_payload_commits_before_deploying(self):
        with patch.object(dashboard, "api", return_value={"sha": "blob"}) as api:
            dashboard.publish("fixture/argon", {"builds": []})
        self.assertEqual([call.args[1] for call in api.call_args_list[1:]], ["PUT", "POST"])
        self.assertEqual(api.call_args_list[1].args[2]["sha"], "blob")

    def test_large_snapshot_uses_raw_content_and_still_skips_timestamp_only_write(self):
        snapshot = {"generated_at": "today", "builds": []}
        current = {"sha": "blob", "encoding": "none", "content": ""}
        with patch.object(dashboard, "api", side_effect=[current, {**snapshot, "generated_at": "yesterday"}]) as api:
            dashboard.publish("fixture/argon", snapshot)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(api.call_args_list[1].kwargs, {"raw": True})
