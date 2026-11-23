"""Guard Chromium's D8 probe across record-capable and legacy toolchains."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/ci/check-exit-dex.py"
spec = importlib.util.spec_from_file_location("argon_check_exit_dex", SCRIPT)
dex_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dex_check)


class RecordDexProbeTests(unittest.TestCase):
    def test_record_capable_d8_must_emit_real_dex(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "record-probe.dex.jar"
            with zipfile.ZipFile(output, "w") as jar:
                jar.writestr("classes.dex", b"dex\n035\0LRecordProbe;")
            result = subprocess.CompletedProcess([], 0, "")
            self.assertIn("accepts Java records", dex_check.verify_record_probe(result, output))

    def test_success_without_valid_dex_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "record-probe.dex.jar"
            with zipfile.ZipFile(output, "w") as jar:
                jar.writestr("classes.dex", b"dex\n035\0LUnrelated;")
            with self.assertRaisesRegex(ValueError, "valid RecordProbe"):
                dex_check.verify_record_probe(subprocess.CompletedProcess([], 0, ""), output)

    def test_legacy_explicit_record_rejection_is_allowed(self):
        result = subprocess.CompletedProcess([], 1, "Java records are not supported.")
        self.assertIn("rejects Java records", dex_check.verify_record_probe(result, None))

    def test_unexpected_toolchain_failure_remains_fatal(self):
        result = subprocess.CompletedProcess([], 1, "D8 crashed for unrelated reason")
        with self.assertRaisesRegex(ValueError, "Unexpected DEX record probe failure"):
            dex_check.verify_record_probe(result, None)


if __name__ == "__main__":
    unittest.main()
