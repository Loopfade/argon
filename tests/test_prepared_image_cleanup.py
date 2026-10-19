"""Both image retention callers share paginated active-build safeguards."""
import subprocess
import unittest
from unittest.mock import patch

from test_ci_cache import ci_module

cleanup = ci_module("prune-prepared-images")


class PreparedImageCleanupTests(unittest.TestCase):
    versions = [{"id": 1, "metadata": {"container": {"tags": []}}},
                {"id": 2, "metadata": {"container": {"tags": ["branch-main"]}}}]

    def test_completed_full_cycle_can_exclude_itself_but_preserves_other_consumers(self):
        for workflow in (".github/workflows/build-all.yml",
                         ".github/workflows/build-all-no-publish.yml"):
            with self.subTest(workflow=workflow):
                runs = [{"id": 100, "status": "in_progress", "path": workflow}]
                def pages(path):
                    return [{"workflow_runs": runs}]
                with patch.object(cleanup, "pages", side_effect=pages):
                    self.assertTrue(cleanup.consumers_active("fixture/argon"))
                    self.assertFalse(cleanup.consumers_active("fixture/argon", "100"))
                    runs.append({"id": 101, "status": "queued", "path": ".github/workflows/build.yml"})
                    self.assertTrue(cleanup.consumers_active("fixture/argon", "100"))

    def test_artifacts_only_build_prevents_deleting_its_image_from_another_run(self):
        def pages(path):
            if "build-chromium-image" in path:
                return [{"workflow_runs": []}]
            return [{"workflow_runs": []}, {"workflow_runs": [
                {"id": 101, "status": "in_progress", "path": ".github/workflows/build-all-no-publish.yml"}]}]
        with patch.object(cleanup, "pages", side_effect=pages), patch.object(cleanup.subprocess, "run") as delete:
            cleanup.prune("fixture/argon", "100", "/versions", "branch-main", exclude_consumer="100")
            delete.assert_not_called()

    def test_active_consumer_on_later_page_prevents_any_delete(self):
        def pages(path):
            if "build-chromium-image" in path:
                return [{"workflow_runs": [{"id": 100, "status": "in_progress"}]}]
            return [{"workflow_runs": [{"id": 2, "status": "completed", "path": ".github/workflows/build-all.yml"}]},
                    {"workflow_runs": [{"id": 3, "status": "queued", "path": ".github/workflows/build.yml"}]}]
        with patch.object(cleanup, "pages", side_effect=pages), patch.object(cleanup.subprocess, "run") as delete:
            cleanup.prune("fixture/argon", "100", "/versions", "branch-main")
            delete.assert_not_called()

    def test_api_failure_stops_cleanup(self):
        with patch.object(cleanup, "pages", side_effect=subprocess.CalledProcessError(1, "gh")), \
             patch.object(cleanup.subprocess, "run") as delete:
            with self.assertRaises(subprocess.CalledProcessError):
                cleanup.prune("fixture/argon", "100", "/versions", "branch-main")
            delete.assert_not_called()

    def test_starting_build_during_cleanup_preserves_remaining_versions(self):
        with patch.object(cleanup, "publication_active", return_value=False), \
             patch.object(cleanup, "consumers_active", side_effect=[False, True]), \
             patch.object(cleanup, "pages", return_value=[self.versions]), \
             patch.object(cleanup.subprocess, "run") as delete:
            cleanup.prune("fixture/argon", "100", "/versions", "branch-main")
            delete.assert_not_called()

    def test_only_untagged_old_version_is_removed(self):
        with patch.object(cleanup, "publication_active", return_value=False), \
             patch.object(cleanup, "consumers_active", return_value=False), \
             patch.object(cleanup, "pages", return_value=[self.versions]), \
             patch.object(cleanup.subprocess, "run") as delete:
            cleanup.prune("fixture/argon", "100", "/versions", "branch-main")
            delete.assert_called_once_with(["gh", "api", "--method", "DELETE", "/versions/1"], check=True)

    def test_missing_or_ambiguous_moving_tag_never_selects_a_version(self):
        for versions in ([], [self.versions[1], self.versions[1]]):
            with self.assertRaises(ValueError):
                cleanup.retained_version(versions, "branch-main")
