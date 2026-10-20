"""Check cache diagnostics against manifest and module metadata fixtures."""

import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifests = load("inspect-compiler-cache")
modules = load("inspect-cached-modules")


class CacheInspectionTests(unittest.TestCase):
    def test_manifest_keeps_dependency_hashes_associated_with_each_result(self):
        text = "File paths (1):\n  0: gen/build/modules/module.modulemap\nFile infos (2):\n"
        for index, digest, size in ((0, "old-hash", 189321), (1, "new-hash", 188932)):
            text += (f"  {index}:\n    Path index: 0\n    Hash: {digest}\n"
                     f"    File size: {size}\n    Mtime: 0\n    Ctime: 0\n")
        text += ("Results (2):\n    File info indexes: 0\n    Key: old-result\n"
                 "    File info indexes: 1\n    Key: new-result\n")
        self.assertEqual(manifests.parse_manifest(text), [
            ("old-result", [("gen/build/modules/module.modulemap", "old-hash", 189321, "0", "0")]),
            ("new-result", [("gen/build/modules/module.modulemap", "new-hash", 188932, "0", "0")]),
        ])

    def test_unrecognized_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            manifests.parse_manifest("Unrecognized ccache output")

    def test_module_inspection_extracts_outside_cache_and_reports_foreign_header(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache"
            cache.mkdir()
            (cache / "aR").write_bytes(b"old cache result with extra headers")
            (cache / "bR").write_bytes(b"new cache result")
            before = {p.name: p.read_bytes() for p in cache.iterdir()}
            extracted = []

            def run(command, **kwargs):
                if command[:2] == ["ccache", "--extract-result"]:
                    output = Path(kwargs["cwd"])
                    self.assertFalse(output.is_relative_to(cache))
                    extracted.append(output)
                    (output / "ccache-result.o").write_bytes(b"CPCH" + Path(command[2]).name.encode())
                    (output / "ccache-result.d").write_text("obj/base.pcm: system headers\n")
                    return subprocess.CompletedProcess(command, 0)
                self.assertEqual(command[:2], ["llvm-bcanalyzer-18", "--dump"])
                old = Path(command[2]).read_bytes().endswith(b"aR")
                record_id = 1 if old else 22
                dump = f"<SUBMODULE_NAME op0={record_id}/> blob data = 'stdlib.h'\n"
                if old:
                    dump += ("<SUBMODULE_TEXTUAL_HEADER op0=2/> blob data = "
                             "'sysroot/usr/include/riscv64-linux-android/asm/hwcap.h'\n")
                return subprocess.CompletedProcess(command, 0, stdout=dump)

            output = io.StringIO()
            with patch.object(modules.subprocess, "run", side_effect=run), redirect_stdout(output):
                modules.inspect_modules(cache)
            self.assertEqual(before, {p.name: p.read_bytes() for p in cache.iterdir()})
            self.assertTrue(all(not path.exists() for path in extracted))
            report = output.getvalue()
            self.assertIn("MODULE_ARCH_HEADER a", report)
            self.assertIn("MODULE_DIFF a b obj/base.pcm", report)
            self.assertIn("ONLY_FIRST 1 ('SUBMODULE_TEXTUAL_HEADER'", report)
            self.assertNotIn("ONLY_SECOND", report)
            self.assertNotIn("ONLY_FIRST 1 ('SUBMODULE_NAME'", report)


if __name__ == "__main__":
    unittest.main()
