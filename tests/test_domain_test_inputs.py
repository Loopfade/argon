"""Check real Git histories for pinned Chromium with applied patch commits."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "domain_inputs", ROOT / ".github/ci/check-domain-test-inputs.py")
domain_inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(domain_inputs)


class DomainTestInputsTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    def commit(self, root, text):
        (root / "fixture").write_text(text)
        self.git(root, "add", "fixture")
        self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-q", "-m", text)
        return self.git(root, "rev-parse", "HEAD")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.src = self.root / "prepared/chromium/src"
        self.depot = self.root / "prepared/depot_tools"
        for path in (self.src, self.depot):
            path.mkdir(parents=True)
            self.git(path, "init", "-q")
        self.lock = {"chromium_commit": self.commit(self.src, "Chromium base"),
                     "depot_tools_commit": self.commit(self.depot, "depot_tools pin")}
        self.expected = self.root / "build-lock.json"
        self.expected.write_text(json.dumps(self.lock))
        self.prepared = self.root / "prepared/build-lock.json"
        self.prepared.write_text(json.dumps(self.lock))

    def test_exact_pins_are_accepted(self):
        domain_inputs.verify(self.expected, self.src, self.depot)

    def test_applied_chromium_patch_commits_preserve_the_pinned_base(self):
        patched = self.commit(self.src, "Applied Vanadium patch")
        self.assertNotEqual(patched, self.lock["chromium_commit"])
        domain_inputs.verify(self.expected, self.src, self.depot)

    def test_an_unrelated_chromium_history_is_rejected(self):
        self.git(self.src, "checkout", "-q", "--orphan", "unrelated")
        self.commit(self.src, "Other Chromium base")
        with self.assertRaisesRegex(ValueError, "does not descend"):
            domain_inputs.verify(self.expected, self.src, self.depot)

    def test_updated_depot_tools_and_changed_image_inputs_are_rejected(self):
        self.commit(self.depot, "Unexpected depot_tools update")
        with self.assertRaisesRegex(ValueError, "depot_tools revision"):
            domain_inputs.verify(self.expected, self.src, self.depot)
        self.prepared.write_text(json.dumps({**self.lock, "chromium_commit": "a" * 40}))
        with self.assertRaisesRegex(ValueError, "Prepared image inputs"):
            domain_inputs.verify(self.expected, self.src, self.depot)


if __name__ == "__main__":
    unittest.main()
