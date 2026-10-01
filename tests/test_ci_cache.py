"""Regression tests for safe remote cache replacement and prepared inputs."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def ci_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cache = ci_module("prune-compiler-cache")
prepared = ci_module("check-prepared-inputs")


class CacheReplacementTests(unittest.TestCase):
    prefix = "argon-ccache-v2-arm64"
    ref = "refs/heads/experimental/ca-domain-allowlist"

    def entry(self, id, **overrides):
        return {"id": id, "key": f"{self.prefix}-{id}", "ref": self.ref,
                "version": "compatible", "size_in_bytes": 4000,
                "created_at": f"2026-09-24T01:{id:02}:00Z", **overrides}

    def test_missing_or_empty_replacement_preserves_previous_cache(self):
        old, new = self.entry(1), self.entry(2)
        for entries in [[old], [old, {**new, "size_in_bytes": 0}],
                        [old, {**new, "ref": "refs/heads/main"}]]:
            with self.subTest(entries=entries):
                self.assertIsNone(cache.obsolete_caches(entries, new["key"], self.prefix, self.ref))

    def test_only_older_caches_of_the_same_ref_arch_and_version_are_removed(self):
        old, new, later = self.entry(1), self.entry(2), self.entry(3)
        entries = [old, new, later, self.entry(4, ref="refs/heads/main"),
                   self.entry(5, key="argon-ccache-v2-x86-1"),
                   self.entry(6, version="other", created_at=old["created_at"])]
        self.assertEqual(cache.obsolete_caches(entries, new["key"], self.prefix, self.ref), [old])

    def invoke(self, response=None, error=None):
        environment = {"CCACHE_CACHE_PREFIX": self.prefix, "GITHUB_REF": self.ref,
                       "GITHUB_REPOSITORY": "Loopfade/argon"}
        with patch.dict(os.environ, environment), \
             patch.object(sys, "argv", ["prune", self.entry(2)["key"]]), \
             patch.object(cache.subprocess, "check_output", return_value=response,
                          side_effect=error), \
             patch.object(cache.subprocess, "run") as delete:
            if error:
                with self.assertRaises(subprocess.CalledProcessError):
                    cache.main()
            else:
                cache.main()
            return delete.call_args_list

    def test_api_failure_never_deletes_a_snapshot(self):
        self.assertEqual(self.invoke(error=subprocess.CalledProcessError(1, "gh api")), [])

    def test_upload_warning_with_no_remote_replacement_never_deletes(self):
        response = json.dumps([{"actions_caches": [self.entry(1)]}])
        self.assertEqual(self.invoke(response), [])

    def test_paginated_replacement_is_confirmed_before_deletion_by_id(self):
        response = json.dumps([{"actions_caches": [self.entry(1)]},
                               {"actions_caches": [self.entry(2), self.entry(3)]}])
        calls = self.invoke(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0],
                         ["gh", "cache", "delete", "1", "--repo", "Loopfade/argon"])


class PreparedImageTests(unittest.TestCase):
    def compare(self, before, after):
        with tempfile.TemporaryDirectory() as tmp:
            roots = [Path(tmp) / "image", Path(tmp) / "checkout"]
            for root, files in zip(roots, [before, after]):
                fixture = {"build.sh": "runtime\nelse\nexport VERSION\nprepare\nconfigure_args=()\n",
                           **files}
                for name, content in fixture.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(content)
            with patch.object(prepared, "PREPARED_INPUTS", ("build-lock.json", "extensions")), \
                 patch.object(sys, "argv", ["check", "--prepared", str(roots[0]),
                                             "--checkout", str(roots[1])]):
                prepared.main()

    def test_generated_extension_downloads_are_ignored(self):
        self.compare({"build-lock.json": "pinned", "extensions/bundle.py": "source",
                      "extensions/dist/payload.crx": "download"},
                     {"build-lock.json": "pinned", "extensions/bundle.py": "source"})

    def test_changed_added_and_removed_source_files_require_new_image(self):
        baseline = {"build-lock.json": "pinned", "extensions/bundle.py": "source"}
        for candidate in [dict(baseline, **{"build-lock.json": "updated"}),
                          dict(baseline, **{"extensions/new.py": "new"}),
                          {"build-lock.json": "pinned", "extensions/other.py": "source"}]:
            with self.subTest(candidate=candidate), self.assertRaisesRegex(SystemExit, "stale"):
                self.compare(baseline, candidate)

    def test_runtime_build_changes_are_allowed_but_source_preparation_changes_are_not(self):
        baseline = {"build-lock.json": "pinned", "extensions/bundle.py": "source"}
        self.compare(baseline, {**baseline, "build.sh":
                     "updated runtime\nelse\nexport VERSION\nprepare\nconfigure_args=(new)\n"})
        with self.assertRaisesRegex(SystemExit, "source preparation"):
            self.compare(baseline, {**baseline, "build.sh":
                         "runtime\nelse\nexport VERSION\ndifferent patches\nconfigure_args=()\n"})


if __name__ == "__main__":
    unittest.main()
