"""Execute the APK image gate against complete Git tree snapshots."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = "1" * 40
HEAD = "2" * 40
GH_FIXTURE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["IMAGE_GATE_FIXTURE"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
data = json.loads((root / "responses.json").read_text())
url = next(arg for arg in args if arg.startswith("repos/"))
if url.endswith("/cancel"):
    print("{}")
elif "/git/trees/" in url:
    key = url.split("/git/trees/", 1)[1].split("?", 1)[0]
    response = data["trees"][key]
    if response == "api-error":
        raise SystemExit(1)
    print(json.dumps(response))
elif "status=success" in url:
    print(data["latest"])
elif "/actions/workflows/build-chromium-image.yml/runs?" in url:
    print(data["active"])
else:
    raise SystemExit("Unexpected gate API request: " + url)
'''


def tree():
    paths = (".gclient", "build-lock.json", "build.sh", "args.gn", "patch.sh",
             ".github/workflows/build-chromium-image.yml",
             ".github/workflows/build.yml", "chromium_overlay/net/cert/root.h",
             "certificates/root.pem", "extensions/bundle.py", "res/icon.svg",
             "docker/chromium/Dockerfile", "README.md")
    entries = [{"path": path, "type": "blob", "mode": "100644", "sha": "a" * 40}
               for path in paths]
    entries += [{"path": "vanadium", "type": "commit", "mode": "160000", "sha": "b" * 40},
                {"path": ".github", "type": "tree", "mode": "040000", "sha": "c" * 40}]
    return {"truncated": False, "tree": entries}


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "bash and jq required")
class ImageGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(GH_FIXTURE)
        gh.chmod(0o755)
        self.base, self.head = tree(), tree()
        self.env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                    "IMAGE_GATE_FIXTURE": str(self.root), "GH_REPO": "fixture/argon",
                    "CURRENT_SHA": HEAD, "CURRENT_RUN_ID": "42", "EVENT_NAME": "workflow_dispatch",
                    "GITHUB_OUTPUT": str(self.root / "output")}
        workflow = (ROOT / ".github/workflows/build.yml").read_text()
        block = workflow.split("      - name: Gate APK build on prepared image\n", 1)[1]
        self.script = textwrap.dedent(block.split("        run: |\n", 1)[1].split("\n  build:", 1)[0])

    def execute(self, *, latest=BASE, active=0):
        output, calls = self.root / "output", self.root / "calls.jsonl"
        output.unlink(missing_ok=True)
        calls.unlink(missing_ok=True)
        (self.root / "responses.json").write_text(json.dumps({
            "latest": latest, "active": active, "trees": {BASE: self.base, HEAD: self.head}}))
        result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", self.script],
                                cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("/compare/", calls.read_text())
        return output.read_text()

    def entry(self, path):
        return next(item for item in self.head["tree"] if item["path"] == path)

    def test_rewritten_history_with_identical_inputs_uses_existing_image(self):
        self.assertEqual(self.execute(), "build=true\n")

    def test_runtime_workflow_and_documentation_changes_use_existing_image(self):
        for path in (".github", ".github/workflows/build.yml", "README.md"):
            self.entry(path)["sha"] = "d" * 40
        self.head["tree"].append({"path": "tests/test_image_gate.py", "type": "blob",
                                  "mode": "100644", "sha": "e" * 40})
        self.assertEqual(self.execute(), "build=true\n")

    def test_each_changed_prepared_input_requires_a_new_image(self):
        for path in (".gclient", "build-lock.json", "build.sh", "args.gn", "patch.sh",
                     ".github/workflows/build-chromium-image.yml",
                     "chromium_overlay/net/cert/root.h", "certificates/root.pem",
                     "extensions/bundle.py", "res/icon.svg", "docker/chromium/Dockerfile",
                     "vanadium"):
            with self.subTest(path=path):
                self.head = tree()
                self.entry(path)["sha"] = "f" * 40
                self.assertEqual(self.execute(), "build=false\n")

    def test_additions_deletions_and_file_mode_changes_are_detected(self):
        self.head["tree"].append({"path": "chromium_overlay/new.cc", "type": "blob",
                                  "mode": "100644", "sha": "e" * 40})
        self.assertEqual(self.execute(), "build=false\n")
        self.head = tree()
        self.head["tree"] = [item for item in self.head["tree"] if item["path"] != "res/icon.svg"]
        self.assertEqual(self.execute(), "build=false\n")
        self.head = tree()
        self.entry("extensions/bundle.py")["mode"] = "100755"
        self.assertEqual(self.execute(), "build=false\n")

    def test_incomplete_or_unavailable_tree_cannot_prove_compatibility(self):
        invalid = [{"truncated": True, "tree": []}, {"tree": []},
                   {"truncated": False, "tree": None}, "api-error"]
        for side in ("base", "head"):
            for value in invalid:
                with self.subTest(side=side, value=value):
                    self.base, self.head = tree(), tree()
                    setattr(self, side, copy.deepcopy(value))
                    self.assertEqual(self.execute(), "build=false\n")

    def test_complete_trees_detect_inputs_beyond_the_old_compare_file_limit(self):
        self.head["tree"] += [
            {"path": f"docs/generated/{index:03}.md", "type": "blob",
             "mode": "100644", "sha": "e" * 40}
            for index in range(400)
        ]
        self.assertEqual(self.execute(), "build=true\n")
        self.entry("vanadium")["sha"] = "f" * 40
        self.assertEqual(self.execute(), "build=false\n")

    def test_active_image_build_still_defers_apk(self):
        self.assertEqual(self.execute(active=1), "build=false\n")

    def test_missing_image_history_keeps_direct_input_verification(self):
        self.assertEqual(self.execute(latest=""), "build=true\n")


if __name__ == "__main__":
    unittest.main()
