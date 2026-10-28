"""Execute release/dashboard shell steps with a local GitHub API fixture."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "154.0.8037.57"

GH_FIXTURE = '''#!/usr/bin/env python3
import hashlib, json, os
from pathlib import Path
import sys

root = Path(os.environ["WORKFLOW_FIXTURE"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
release_path = root / "release.json"
release = json.loads(release_path.read_text())
if args[:2] == ["release", "upload"]:
    for filename in args[3:]:
        path = Path(filename)
        release["assets"].append({"name": path.name, "digest": "sha256:" +
                                  hashlib.sha256(path.read_bytes()).hexdigest()})
    release_path.write_text(json.dumps(release))
elif args[:2] == ["release", "edit"]:
    pass
elif args[0] == "api":
    if "--method" in args:
        sys.stdin.read()
        print("{}")
        sys.exit(0)
    url = next(arg for arg in args[1:] if arg.startswith("repos/"))
    if os.environ.get("FAIL_INVENTORY") and os.environ["FAIL_INVENTORY"] in url:
        raise SystemExit("Fixture GitHub inventory request failed")
    if "/git/ref/heads/main" in url:
        print(os.environ["RELEASE_TARGET"])
    elif "/releases/tags/" in url:
        print(json.dumps(release))
    elif "/releases?" in url:
        history = root / "releases.json"
        items = json.loads(history.read_text()) if history.exists() else [release]
        pages = [items[i:i+100] for i in range(0, len(items), 100)] or [[]]
        print(json.dumps(pages if "--slurp" in args else items))
    elif "/tags?" in url:
        history = root / "tags.json"
        items = json.loads(history.read_text()) if history.exists() else []
        pages = [items[i:i+100] for i in range(0, len(items), 100)] or [[]]
        print(json.dumps(pages if "--slurp" in args else items))
    elif "/actions/runs/42/attempts/1/jobs" in url:
        print('[{"jobs":[]}]')
    elif "/actions/runs/42/artifacts" in url:
        print('[{"artifacts":[]}]')
    elif "/actions/runs/42" in url:
        print(json.dumps({"id":42, "name":os.environ.get("FIXTURE_RUN_NAME", "Full Argon build (release)"),
                          "path":os.environ.get("FIXTURE_RUN_PATH", ".github/workflows/build-all.yml"),
                          "event":os.environ.get("FIXTURE_RUN_EVENT", "workflow_dispatch"),
                          "status":"completed", "conclusion":"success",
                          "head_sha":os.environ.get("FIXTURE_RUN_SHA", "a"*40),
                          "head_branch":os.environ.get("FIXTURE_RUN_BRANCH", "main"),
                          "repository":{"full_name":os.environ.get("FIXTURE_RUN_REPOSITORY", os.environ["GH_REPO"])}}))
    elif "/contents/dashboard-data.json" in url:
        print('{"sha":"existing-snapshot-sha"}')
    else:
        raise SystemExit("Unexpected fixture URL: " + url)
else:
    raise SystemExit("Unexpected fixture command: " + repr(args))
'''


def workflow_step(filename, step_id):
    workflow = (ROOT / ".github/workflows" / filename).read_text()
    blocks = re.split(r"(?m)^      - ", workflow)[1:]
    block, = [block for block in blocks if f"\n        id: {step_id}\n" in block]
    script = textwrap.dedent(block.split("        run: |\n", 1)[1])
    # Materialize read-only process substitutions for environments without
    # /dev/fd. The shell validation and all GitHub operations stay unchanged.
    script = script.replace(
        "mapfile -d '' assets < <(find public-assets -type f -print0 | sort -z)",
        "find public-assets -type f -print0 | sort -z > fixture-assets\n"
        "mapfile -d '' assets < fixture-assets")
    script = script.replace("builds='[]'", "builds='[]'\n"
                            "jq -c '.[]' <<<\"$releases\" > fixture-releases")
    script = script.replace("done < <(jq -c '.[]' <<<\"$releases\")",
                            "done < fixture-releases")
    return block, script


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "bash and jq required")
class WorkflowExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = root = Path(self.temp.name)
        (root / "bin").mkdir()
        gh = root / "bin/gh"
        gh.write_text(GH_FIXTURE)
        gh.chmod(0o755)
        (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
        (root / ".github").symlink_to(ROOT / ".github", target_is_directory=True)
        assets = root / "public-assets"
        assets.mkdir()
        names = [f"Argon-{VERSION}-release-{abi}.apk{suffix}"
                 for abi in ("arm64-v8a", "armeabi-v7a") for suffix in ("", ".sha256")]
        names += ["Chromium-LICENSE.txt", "Ruthenium-LICENSE.txt", "Titanium-LICENSE.txt"]
        published = []
        for name in names:
            data = (f"{hashlib.sha256((assets / name[:-7]).read_bytes()).hexdigest()}  "
                    f"{name[:-7]}\n").encode() if name.endswith(".sha256") else name.encode()
            (assets / name).write_bytes(data)
            published.append({"name": name, "digest": "sha256:" +
                              hashlib.sha256(data).hexdigest()})
        self.release = {"draft": False, "target_commitish": "a" * 40,
                        "html_url": "https://example.invalid/release",
                        "body": "Build Argon run #42", "tag_name": f"v{VERSION}-2",
                        "published_at": "2026-10-06T00:00:00Z", "assets": published}
        self.env = {**os.environ, "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
                    "WORKFLOW_FIXTURE": str(root), "TMPDIR": str(root),
                    "GH_REPO": "fixture/argon", "GITHUB_REPOSITORY": "fixture/argon",
                    "RELEASE_TAG": f"v{VERSION}-2", "RELEASE_TARGET": "a" * 40,
                    "RELEASE_VERSION": VERSION, "RELEASE_REVISION": "2", "SOURCE_RUN": "42",
                    "RELEASE_TITLE": "Fixture", "GITHUB_STEP_SUMMARY": str(root / "summary"),
                    "GITHUB_OUTPUT": str(root / "output"), "EVENT_NAME": "workflow_run"}

    def execute(self, filename, name):
        (self.root / "release.json").write_text(json.dumps(self.release))
        _, script = workflow_step(filename, name)
        return subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script],
                              cwd=self.root, env=self.env, capture_output=True, text=True)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def publish(self):
        return self.execute("publish-release.yml", "publish")

    def prepare_provenance(self):
        assets = self.root / "public-assets"
        for abi, cpu in (("arm64-v8a", "arm64"), ("armeabi-v7a", "arm")):
            target = self.root / "release-assets" / abi
            target.mkdir(parents=True)
            apk_name = f"Argon-{VERSION}-release-{abi}.apk"
            for name in (apk_name, apk_name + ".sha256", "Chromium-LICENSE.txt",
                         "Ruthenium-LICENSE.txt", "Titanium-LICENSE.txt"):
                shutil.copy(assets / name, target / name)
            # Old images carry a fixed revision; future metadata can omit it.
            # Neither form is allowed to override the automatic release number.
            inputs = {"chromium_version": VERSION}
            if cpu == "arm64":
                inputs["argon_revision"] = 99
            (target / "build-info.json").write_text(json.dumps({
                "source_commit": "a" * 40, "inputs": inputs, "abi": abi,
                "target_cpu": cpu, "signing_mode": "release",
                "apk_sha256": hashlib.sha256((target / apk_name).read_bytes()).hexdigest(),
            }))
        shutil.rmtree(assets)
        self.env.update(EVENT_NAME="workflow_dispatch", SOURCE_RUN_ID="42",
                        GITHUB_RUN_ID="42", AUTO_SOURCE_SHA="a" * 40)

    def select_revision(self):
        return self.execute("publish-release.yml", "release")

    def test_first_publication_ignores_legacy_revision_in_artifacts(self):
        self.prepare_provenance()
        (self.root / "releases.json").write_text("[]")
        (self.root / "tags.json").write_text("[]")
        result = self.select_revision()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"tag=v{VERSION}-1\n", (self.root / "output").read_text())
        self.assertFalse(any(call[0] == "release" for call in self.calls()))

    def test_full_pipeline_can_publish_only_from_its_trusted_workflow_path(self):
        self.prepare_provenance()
        for title, path, trusted in (
            ("Полная сборка Argon (Релизная)", ".github/workflows/build-all.yml", True),
            ("Full Argon build (release)", ".github/workflows/build-all.yml", True),
            ("Renamed build run", ".github/workflows/build-all.yml", True),
            ("Full Argon build without APK publishing (release)", ".github/workflows/build-all-no-publish.yml", False),
            ("Build Argon", ".github/workflows/untrusted.yml", False),
            ("Build Argon (full pipeline)", ".github/workflows/untrusted.yml", False),
            ("Полная сборка Argon (Релизная)", ".github/workflows/untrusted.yml", False),
            ("Full Argon build (release)", ".github/workflows/build.yml", False),
            ("Full Argon build (release)", "", False),
        ):
            with self.subTest(title=title, path=path):
                self.env.update(FIXTURE_RUN_NAME=title, FIXTURE_RUN_PATH=path)
                shutil.rmtree(self.root / "public-assets", ignore_errors=True)
                result = self.select_revision()
                if trusted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("доверенный процесс сборки Argon", result.stdout)
        self.assertFalse(any(call[0] == "release" for call in self.calls()))

    def test_trusted_path_still_requires_the_matching_repository_main_sha_and_manual_event(self):
        self.prepare_provenance()
        original_env = dict(self.env)
        for name, value in (("FIXTURE_RUN_REPOSITORY", "foreign/argon"),
                            ("FIXTURE_RUN_BRANCH", "topic"),
                            ("FIXTURE_RUN_SHA", "b" * 40),
                            ("FIXTURE_RUN_EVENT", "pull_request")):
            with self.subTest(field=name):
                self.env = {**original_env, name: value}
                shutil.rmtree(self.root / "public-assets", ignore_errors=True)
                result = self.select_revision()
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == "release" for call in self.calls()))

    def test_revision_selection_includes_later_inventory_pages(self):
        self.prepare_provenance()
        (self.root / "releases.json").write_text("[]")
        tags = [{"name": f"v100.0.0.0-{i}", "commit": {"sha": "b" * 40}}
                for i in range(1, 101)]
        tags += [{"name": f"v{VERSION}-10", "commit": {"sha": "b" * 40}}]
        (self.root / "tags.json").write_text(json.dumps(tags))
        result = self.select_revision()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"tag=v{VERSION}-11\n", (self.root / "output").read_text())
        requests = [call for call in self.calls() if "/tags?" in " ".join(call)
                    or "/releases?" in " ".join(call)]
        self.assertEqual(len(requests), 2)
        self.assertTrue(all("--paginate" in call and "--slurp" in call for call in requests))

    def test_publication_retry_reuses_existing_revision(self):
        self.prepare_provenance()
        result = self.select_revision()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"tag=v{VERSION}-2\n", (self.root / "output").read_text())

    def test_inventory_failure_does_not_allocate_or_publish(self):
        self.prepare_provenance()
        self.env["FAIL_INVENTORY"] = "/tags?"
        result = self.select_revision()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "output").exists())
        self.assertFalse(any(call[0] == "release" for call in self.calls()))

    def test_history_only_push_preserves_dashboard_without_api_calls(self):
        self.env.update(EVENT_NAME="push", HEAD_COMMIT_MESSAGE="Merge Titanium [history-only]")
        snapshot = self.root / "dashboard-data.json"
        snapshot.write_bytes(b"existing published snapshot")
        result = self.execute("update-dashboard.yml", "snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(snapshot.read_bytes(), b"existing published snapshot")
        self.assertEqual((self.root / "output").read_text(), "updated=false\n")
        self.assertFalse((self.root / "calls.jsonl").exists())

    def test_normal_push_still_refreshes_dashboard(self):
        self.env.update(EVENT_NAME="push", HEAD_COMMIT_MESSAGE="Update dashboard workflow")
        result = self.execute("update-dashboard.yml", "snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "output").read_text(), "updated=false\nupdated=true\n")
        self.assertEqual(json.loads((self.root / "dashboard-data.json").read_text())["latest"]["id"], 42)
        self.assertTrue(self.calls())

    def test_bad_base_asset_digest_blocks_all_release_mutations(self):
        for name in (self.release["assets"][0]["name"], "Chromium-LICENSE.txt"):
            with self.subTest(asset=name):
                original = next(item for item in self.release["assets"] if item["name"] == name)
                digest = original["digest"]
                original["digest"] = "sha256:" + "0" * 64
                result = self.publish()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("digest mismatch", result.stderr)
                self.assertFalse(any(call[0] == "release" for call in self.calls()))
                original["digest"] = digest

    def test_verified_legacy_release_uploads_pair_then_becomes_idempotent(self):
        self.release["assets"] = [item for item in self.release["assets"]
                                  if "armeabi-v7a" not in item["name"]]
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[:2] == ["release", "upload"] for call in self.calls()), 1)
        self.release = json.loads((self.root / "release.json").read_text())
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[:2] == ["release", "upload"] for call in self.calls()), 1)

    def test_partial_duplicate_and_extra_assets_block_release_mutations(self):
        original = self.release["assets"]
        for bad in (original[:3] + original[4:], original + [original[0]],
                    original + [{"name": "unexpected.txt", "digest": "sha256:" + "0" * 64}]):
            with self.subTest(assets=bad):
                self.release["assets"] = bad
                result = self.publish()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "release" for call in self.calls()))

    def test_dashboard_skip_preserves_checkout_and_does_not_write_or_deploy(self):
        self.env["TRIGGER_RUN_ID"] = "999"
        old = '{"generated_at":"old-checkout-snapshot"}\n'
        (self.root / "dashboard-data.json").write_text(old)
        result = self.execute("update-dashboard.yml", "snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "dashboard-data.json").read_text(), old)
        self.assertEqual((self.root / "output").read_text(), "updated=false\n")
        publish, _ = workflow_step("update-dashboard.yml", "publish")
        self.assertIn("if: steps.snapshot.outputs.updated == 'true'", publish)
        self.assertFalse(any("--method" in call for call in self.calls()))

    def test_updated_dashboard_publishes_and_dispatches_pages(self):
        self.env["TRIGGER_RUN_ID"] = "42"
        result = self.execute("update-dashboard.yml", "snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "output").read_text().splitlines()[-1], "updated=true")
        result = self.execute("update-dashboard.yml", "publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [call for call in self.calls() if "--method" in call]
        self.assertEqual([call[call.index("--method") + 1] for call in writes], ["PUT", "POST"])


if __name__ == "__main__":
    unittest.main()
