"""Both image retention callers share paginated active-build safeguards."""
import subprocess
import unittest
from unittest.mock import patch

from test_ci_cache import ci_module

cleanup = ci_module("prune-prepared-images")


class PreparedImageCleanupTests(unittest.TestCase):
    versions = [{"id": 1, "metadata": {"container": {"tags": []}}},
                {"id": 2, "metadata": {"container": {"tags": ["branch-main"]}}}]

    def test_active_consumer_on_later_page_prevents_any_delete(self):
        def pages(path):
            if "build-chromium-image" in path:
                return [{"workflow_runs": [{"id": 100, "status": "in_progress"}]}]
            return [{"workflow_runs": [{"id": 2, "status": "completed"}]},
                    {"workflow_runs": [{"id": 3, "status": "queued"}]}]
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
