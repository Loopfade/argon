"""The reusable source layer tracks pins, independent of Argon metadata."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from image_source_inputs import source_inputs


class ImageSourceInputsTests(unittest.TestCase):
    def test_metadata_changes_reuse_sources_but_every_source_pin_changes_them(self):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        self.assertEqual(source_inputs(lock), source_inputs({**lock, "argon_revision": 99,
                         "titanium_commit": "new-history", "application_id": "other.app"}))
        for key in source_inputs(lock):
            changed = copy.deepcopy(lock)
            changed[key] = "different"
            self.assertNotEqual(source_inputs(lock), source_inputs(changed), key)

    def test_stage_validation_rejects_mismatched_pins(self):
        lock = json.loads((ROOT / "build-lock.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "source-lock.json"
            marker.write_text(json.dumps(source_inputs({**lock, "chromium_commit": "b" * 40})))
            result = subprocess.run([sys.executable, ROOT / "scripts/image_source_inputs.py",
                                     "--lock", ROOT / "build-lock.json", "--check", marker],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Prepared source pins differ", result.stderr)
