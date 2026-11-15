"""Exercise check selection against real merge commits, including renames."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validation_scope", ROOT / ".github/ci/validation-scope.py")
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)


class ValidationScopeTests(unittest.TestCase):
    def test_documentation_only_changes_skip_expensive_steps(self):
        self.assertEqual(scope.classify(["README.md", "docs/build-pipeline.md"]),
                         {"full": False, "domain": False})

    def test_workflow_names_still_get_unit_checks_without_android_builds(self):
        self.assertEqual(scope.classify([".github/workflows/build-test-branch.yml", "docs/build-pipeline.md"]),
                         {"full": True, "domain": False})

    def test_unknown_paths_sources_and_the_validation_workflow_get_all_checks(self):
        for path in ("new-build-input", "chromium_overlay/file.cc", "tests/domain_policy/BUILD.gn",
                     ".github/ci/validation-scope.py", ".github/workflows/validate.yml",
                     "docker/chromium/Dockerfile", "build-lock.json"):
            with self.subTest(path=path):
                self.assertEqual(scope.classify(["README.md", path]), {"full": True, "domain": True})

    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    def test_complete_diff_catches_code_renamed_into_docs_after_thousands_of_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.git(root, "init", "-q", "-b", "main")
            self.git(root, "config", "user.name", "Fixture")
            self.git(root, "config", "user.email", "fixture@example.invalid")
            (root / "source.cc").write_text("production source")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "base")
            base = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-qb", "topic")
            (root / "docs").mkdir()
            (root / "source.cc").rename(root / "docs/renamed.md")
            for number in range(3001):
                (root / f"docs/{number}.md").write_text("documentation")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "changes")
            head = self.git(root, "rev-parse", "HEAD")
            self.git(root, "checkout", "-q", "main")
            self.git(root, "merge", "--no-ff", "-qm", "merge", "topic")
            paths = scope.changed_paths(root, base, head)
            self.assertEqual(len(paths), 3003)
            self.assertIn("source.cc", paths)
            self.assertEqual(scope.classify(paths), {"full": True, "domain": True})
            self.assertIsNone(scope.changed_paths(root, head, base))

    def test_invalid_event_commits_cannot_produce_a_skip(self):
        with self.assertRaises(ValueError):
            scope.changed_paths(ROOT, "not-a-commit", "b" * 40)

    def test_only_trusted_main_events_can_seed_shared_cache_without_pr_checks(self):
        with patch.object(scope, "changed_paths") as changed:
            for event in ("push", "workflow_dispatch"):
                self.assertEqual(scope.select_checks(ROOT, event, "refs/heads/main"),
                                 {"full": False, "domain": True})
                with self.assertRaises(ValueError):
                    scope.select_checks(ROOT, event, "refs/heads/topic")
            for event in ("pull_request_target", "workflow_run"):
                with self.assertRaises(ValueError):
                    scope.select_checks(ROOT, event, "refs/heads/main")
            changed.assert_not_called()

    def test_cache_key_tracks_compiler_inputs_but_survives_argon_release_bumps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("build-lock.json", "args.gn", "scripts/configure_build.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / name).read_bytes())
            path = root / "build-lock.json"
            lock = json.loads(path.read_text())
            prefix = scope.cache_prefix(root)
            self.assertRegex(prefix, rf"^argon-ccache-validation-v2-{lock['chromium_version']}-[0-9a-f]{{64}}$")
            for key in ("argon_revision", "chromium_version", "chromium_commit", "depot_tools_commit",
                        "boringssl_commit", "vanadium_commit"):
                updated = {**lock, key: {"argon_revision": lock["argon_revision"] + 1,
                                         "chromium_version": "158.0.1.1"}.get(key, "b" * 40)}
                path.write_text(json.dumps(updated))
                self.assertEqual(scope.cache_prefix(root) == prefix, key == "argon_revision", key)
            path.write_text(json.dumps(lock))
            for name in ("args.gn", "scripts/configure_build.py"):
                target = root / name
                target.write_bytes((ROOT / name).read_bytes() + b"\n# changed compiler configuration\n")
                self.assertNotEqual(scope.cache_prefix(root), prefix)
                target.write_bytes((ROOT / name).read_bytes())
            path.write_text(json.dumps({**lock, "chromium_version": "bad-version"}))
            with self.assertRaises(ValueError):
                scope.cache_prefix(root)


if __name__ == "__main__":
    unittest.main()
