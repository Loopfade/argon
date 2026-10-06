"""Exercise scheduling and rerunning with realistic GitHub run/PR responses."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuation", ROOT / ".github/ci/continue-build.py")
continuation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(continuation)


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.head, self.merge = "a" * 40, "b" * 40
        self.repository = "Loopfade/argon"
        self.run = {"id": 123, "path": ".github/workflows/build.yml",
                    "repository": {"full_name": self.repository},
                    "head_repository": {"full_name": self.repository},
                    "head_sha": self.head, "head_branch": "fix/m154-upstream-validation",
                    "event": "pull_request", "pull_requests": [{"number": 11}],
                    "run_attempt": 3, "status": "completed", "conclusion": "failure"}
        self.pr = {"state": "open", "head": {"repo": {"full_name": self.repository},
                                               "sha": self.head},
                   "merge_commit_sha": self.merge}
        self.branch_sha = self.head
        self.env = {"GITHUB_REPOSITORY": self.repository, "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "3", "GITHUB_SHA": self.merge,
                    "GITHUB_REF": "refs/pull/11/merge", "GITHUB_REF_NAME": "11/merge",
                    "CONTINUATION_COUNT": "0", "AUTO_CONTINUE_MAX": "10"}
        self.controller_env = {**self.env, "GITHUB_REF": "refs/heads/main",
                               "GITHUB_SHA": "c" * 40,
                               "RESUME_RUN": "123", "RESUME_ATTEMPT": "3",
                               "RESUME_SHA": self.merge}
        self.writes = []
        self.jobs = [{"id": 201 + index, "name": name, "conclusion": "failure"}
                     for index, name in enumerate(continuation.BUILD_JOBS)]
        self.artifacts = [{"name": f"argon-continuation-3-{arch}", "expired": False}
                          for arch in ("arm64", "arm")]

    def api(self, repository, path, payload=None):
        self.assertEqual(repository, self.repository)
        if payload is not None:
            self.writes.append((path, payload))
            return None
        if path == "actions/runs/123":
            return copy.deepcopy(self.run)
        if path == "git/ref/heads/fix/m154-upstream-validation":
            return {"object": {"sha": self.branch_sha}}
        if path == "pulls/11":
            return copy.deepcopy(self.pr)
        if path == "actions/runs/123/attempts/3/jobs?per_page=100":
            return {"total_count": len(self.jobs), "jobs": copy.deepcopy(self.jobs)}
        if path == "actions/runs/123/artifacts?per_page=100":
            return {"total_count": len(self.artifacts),
                    "artifacts": copy.deepcopy(self.artifacts)}
        self.fail(f"Unexpected API path {path}")

    def test_pr_schedules_main_controller_with_original_merge_sha(self):
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.schedule(self.env)
        self.assertEqual(self.writes, [("actions/workflows/continue-build.yml/dispatches", {
            "ref": "main", "inputs": {
                "resume_run": "123", "resume_attempt": "3", "resume_sha": self.merge}})])

    def test_controller_reruns_original_pr(self):
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_mixed_budget_and_compiler_or_oom_failure_retries_only_budget_job(self):
        # The run-level conclusion cannot distinguish compiler errors from OOM.
        # Only the explicit per-attempt budget marker authorizes continuation.
        for budget_arch in ("arm64", "arm"):
            with self.subTest(budget_arch=budget_arch):
                self.writes.clear()
                self.artifacts = [{"name": f"argon-continuation-3-{budget_arch}",
                                   "expired": False}]
                job = next(job for job in self.jobs
                           if continuation.BUILD_JOBS[job["name"]] == budget_arch)
                with patch.object(continuation, "api", side_effect=self.api):
                    continuation.resume(self.controller_env)
                self.assertEqual(self.writes, [(f"actions/jobs/{job['id']}/rerun", {})])

    def test_successful_architecture_is_not_retried(self):
        self.jobs[0]["conclusion"] = "success"
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [(f"actions/jobs/{self.jobs[1]['id']}/rerun", {})])

    def test_older_compiler_failure_absent_from_attempt_list_is_not_retried(self):
        self.jobs = self.jobs[:1]
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [(f"actions/jobs/{self.jobs[0]['id']}/rerun", {})])

    def test_stale_expired_or_missing_budget_markers_do_not_authorize_retry(self):
        for artifacts in ([], [{"name": "argon-continuation-2-arm", "expired": False}],
                          [{"name": "argon-continuation-3-arm", "expired": True}]):
            with self.subTest(artifacts=artifacts):
                self.artifacts = artifacts
                with patch.object(continuation, "api", side_effect=self.api), \
                     self.assertRaisesRegex(ValueError, "No failed job"):
                    continuation.resume(self.controller_env)
                self.assertEqual(self.writes, [])

    def test_truncated_job_or_artifact_lists_fail_closed(self):
        original = self.api
        for collection in ("jobs", "artifacts"):
            def truncated(repository, path, payload=None):
                response = original(repository, path, payload)
                if isinstance(response, dict) and collection in response:
                    response["total_count"] = 101
                return response
            with self.subTest(collection=collection), \
                 patch.object(continuation, "api", side_effect=truncated), \
                 self.assertRaisesRegex(ValueError, "Incomplete"):
                continuation.resume(self.controller_env)
            self.assertEqual(self.writes, [])

    def test_controller_waits_for_cleanup(self):
        self.run.update(status="in_progress", conclusion=None)
        def finish(_seconds):
            self.run.update(status="completed", conclusion="failure")
        with patch.object(continuation, "api", side_effect=self.api), \
             patch.object(continuation.time, "sleep", side_effect=finish) as sleep:
            continuation.resume(self.controller_env)
        sleep.assert_called_once_with(5)
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_manual_build_continues_after_main_advances(self):
        self.run.update(event="workflow_dispatch", head_branch="main", pull_requests=[])
        manual_env = {**self.env, "GITHUB_SHA": self.head}
        controller = {**self.controller_env, "RESUME_SHA": self.head, "GITHUB_SHA": "d" * 40}
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.schedule(manual_env)
            self.writes.clear()
            continuation.resume(controller)
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_changed_pr_head_or_base_never_continues(self):
        for changed in ("head", "merge"):
            with self.subTest(changed=changed):
                self.setUp()
                self.branch_sha = "c" * 40 if changed == "head" else self.head
                self.pr["merge_commit_sha"] = "c" * 40 if changed == "merge" else self.merge
                for function, env in [(continuation.schedule, self.env),
                                      (continuation.resume, self.controller_env)]:
                    with patch.object(continuation, "api", side_effect=self.api), \
                         self.assertRaisesRegex(ValueError, "changed"):
                        function(env)
        self.assertEqual(self.writes, [])

    def test_foreign_or_legacy_source_never_runs(self):
        for change in ("workflow", "repository", "fork", "closed", "legacy_event"):
            with self.subTest(change=change):
                self.setUp()
                if change == "workflow":
                    self.run["path"] = ".github/workflows/publish-release.yml"
                elif change == "repository":
                    self.run["repository"]["full_name"] = "other/argon"
                elif change == "fork":
                    self.run["head_repository"]["full_name"] = "other/argon"
                elif change == "closed":
                    self.pr["state"] = "closed"
                else:
                    self.run.update(event="push", pull_requests=[])
                with patch.object(continuation, "api", side_effect=self.api), \
                     self.assertRaises(ValueError):
                    continuation.schedule(self.env)
                self.assertEqual(self.writes, [])

    def test_cancelled_or_successful_source_is_not_restarted(self):
        for conclusion in ("cancelled", "success"):
            self.run["conclusion"] = conclusion
            with self.subTest(conclusion=conclusion), \
                 patch.object(continuation, "api", side_effect=self.api), \
                 self.assertRaisesRegex(ValueError, "Only a failed run"):
                continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [])

    def test_newer_attempt_does_not_duplicate(self):
        self.run["run_attempt"] = 4
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [])

    def test_controller_must_run_from_main(self):
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "must run from main"):
            continuation.resume({**self.controller_env, "GITHUB_REF": "refs/heads/topic"})
        self.assertEqual(self.writes, [])

    def test_hung_source_has_bounded_wait(self):
        self.run.update(status="in_progress", conclusion=None)
        with patch.object(continuation, "api", side_effect=self.api), \
             patch.object(continuation.time, "sleep") as sleep, \
             self.assertRaisesRegex(ValueError, "did not finish"):
            continuation.resume(self.controller_env, max_polls=2)
        sleep.assert_called_once_with(5)

    def test_limit_counts_reruns(self):
        self.run["run_attempt"] = 11
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "limit"):
            continuation.schedule({**self.env, "GITHUB_RUN_ATTEMPT": "11"})
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "limit"):
            continuation.resume({**self.controller_env, "RESUME_ATTEMPT": "11"})

    def test_api_failure_is_propagated(self):
        with patch.object(continuation, "api", side_effect=RuntimeError("HTTP 403")), \
             self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            continuation.schedule(self.env)


if __name__ == "__main__":
    unittest.main()
