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
        self.controller_env = {**self.env, "GITHUB_SHA": self.head,
                               "RESUME_RUN": "123", "RESUME_ATTEMPT": "3",
                               "RESUME_SHA": self.merge}
        self.writes = []

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
        self.fail(f"Unexpected API path {path}")

    def test_pr_schedules_controller_with_original_merge_sha_and_run_id(self):
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.schedule(self.env)
        self.assertEqual(self.writes, [("actions/workflows/continue-build.yml/dispatches", {
            "ref": self.run["head_branch"], "inputs": {
                "resume_run": "123", "resume_attempt": "3", "resume_sha": self.merge}})])

    def test_controller_reruns_original_pr_instead_of_dispatching_branch_build(self):
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_controller_waits_for_source_cleanup_before_rerun(self):
        self.run.update(status="in_progress", conclusion=None)
        def finish(_seconds):
            self.assertEqual(self.writes, [])
            self.run.update(status="completed", conclusion="failure")
        with patch.object(continuation, "api", side_effect=self.api), \
             patch.object(continuation.time, "sleep", side_effect=finish) as sleep:
            continuation.resume(self.controller_env)
        sleep.assert_called_once_with(5)
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_push_continuation_keeps_original_run_and_commit(self):
        self.run.update(event="push", pull_requests=[])
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.schedule({**self.env, "GITHUB_SHA": self.head})
            self.writes.clear()
            continuation.resume({**self.controller_env, "RESUME_SHA": self.head})
        self.assertEqual(self.writes, [("actions/runs/123/rerun-failed-jobs", {})])

    def test_changed_branch_or_pr_base_never_dispatches_or_reruns(self):
        for changed in ("head", "merge"):
            with self.subTest(changed=changed):
                self.branch_sha = "c" * 40 if changed == "head" else self.head
                self.pr["merge_commit_sha"] = "c" * 40 if changed == "merge" else self.merge
                for function, env in [(continuation.schedule, self.env),
                                      (continuation.resume, self.controller_env)]:
                    with patch.object(continuation, "api", side_effect=self.api), \
                         self.assertRaisesRegex(ValueError, "changed"):
                        function(env)
        self.assertEqual(self.writes, [])

    def test_foreign_workflow_fork_or_closed_pr_never_runs(self):
        for change in ("workflow", "repository", "fork", "closed"):
            with self.subTest(change=change):
                self.setUp()
                if change == "workflow":
                    self.run["path"] = ".github/workflows/publish-release.yml"
                elif change == "repository":
                    self.run["repository"]["full_name"] = "other/argon"
                elif change == "fork":
                    self.run["head_repository"]["full_name"] = "other/argon"
                else:
                    self.pr["state"] = "closed"
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

    def test_newer_attempt_does_not_schedule_duplicate(self):
        self.run["run_attempt"] = 4
        with patch.object(continuation, "api", side_effect=self.api):
            continuation.resume(self.controller_env)
        self.assertEqual(self.writes, [])

    def test_wrong_controller_commit_is_rejected(self):
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "Controller does not match"):
            continuation.resume({**self.controller_env, "GITHUB_SHA": "c" * 40})
        self.assertEqual(self.writes, [])

    def test_hung_source_has_a_bounded_wait(self):
        self.run.update(status="in_progress", conclusion=None)
        with patch.object(continuation, "api", side_effect=self.api), \
             patch.object(continuation.time, "sleep") as sleep, \
             self.assertRaisesRegex(ValueError, "did not finish"):
            continuation.resume(self.controller_env, max_polls=2)
        sleep.assert_called_once_with(5)
        self.assertEqual(self.writes, [])

    def test_limit_counts_reruns_even_when_dispatch_input_stays_zero(self):
        self.run["run_attempt"] = 11
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "limit"):
            continuation.schedule({**self.env, "GITHUB_RUN_ATTEMPT": "11"})
        with patch.object(continuation, "api", side_effect=self.api), \
             self.assertRaisesRegex(ValueError, "limit"):
            continuation.resume({**self.controller_env, "RESUME_ATTEMPT": "11"})
        self.assertEqual(self.writes, [])

    def test_api_failure_does_not_claim_to_have_queued_continuation(self):
        with patch.object(continuation, "api", side_effect=RuntimeError("HTTP 403")), \
             self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            continuation.schedule(self.env)
        self.assertEqual(self.writes, [])


if __name__ == "__main__":
    unittest.main()
