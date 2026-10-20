#!/usr/bin/env python3
"""Compare cached Clang module metadata; never write to the restored cache."""

import collections
import difflib
import pathlib
import re
import subprocess
import sys
import tempfile

from importlib.machinery import SourceFileLoader

manifest_tool = SourceFileLoader(
    "manifest_tool", str(pathlib.Path(__file__).with_name("inspect-compiler-cache.py"))
).load_module()
cache = pathlib.Path(sys.argv[1]).resolve()
manifests = sorted(cache.rglob("*M"), key=lambda p: p.stat().st_size, reverse=True)
results_by_key = {"".join(p.relative_to(cache).parts)[:-1]: p for p in cache.rglob("*R")}
seen = set()
modules = []

with tempfile.TemporaryDirectory() as temp:
    root = pathlib.Path(temp)
    for manifest in manifests[:250]:
        records = manifest_tool.inspect_manifest(manifest)
        for key, infos in records:
            if key in seen:
                continue
            seen.add(key)
            if len(infos) < 500 or any(
                name.endswith("gen/build/modules/module.modulemap")
                for name, *_ in infos
            ):
                continue
            result = results_by_key.get(key)
            if result is None:
                continue
            output = root / key
            output.mkdir()
            subprocess.run(["ccache", "--extract-result", str(result)], cwd=output, check=True)
            obj = output / "ccache-result.o"
            if not obj.is_file():
                continue
            with obj.open("rb") as stream:
                magic = stream.read(4)
            if magic != b"CPCH":
                continue
            depfile = output / "ccache-result.d"
            deps = depfile.read_text() if depfile.exists() else ""
            dump = subprocess.run(
                ["llvm-bcanalyzer-18", "--dump", str(obj)],
                capture_output=True, text=True, check=True,
            ).stdout
            # Input paths and submodule declarations show changes in the module
            # map without printing the module's serialized C++ AST.
            selected = [line for line in dump.splitlines() if re.search(
                r"<(?:INPUT_FILE|SUBMODULE_[A-Z_]+|MODULE_NAME|MODULE_DIRECTORY)\b", line
            )]
            print(f"CACHED_MODULE key={key} manifest={manifest.relative_to(cache)} "
                  f"object_bytes={obj.stat().st_size} dependency_count={len(infos)}", flush=True)
            print("DEPENDENCY_TARGET " + deps.split(":", 1)[0], flush=True)
            modules.append((key, deps.split(":", 1)[0], selected))
    print(f"Cached modules found: {len(modules)}", flush=True)
    for key, target, lines in modules:
        print(f"MODULE_METADATA {key} {target}", flush=True)
        # Per-module names are much smaller than the full AST.
        for line in lines:
            if "SUBMODULE_DEFINITION" in line or "MODULE_NAME" in line:
                print(line, flush=True)
    for i, (key, target, lines) in enumerate(modules):
        for other_key, other_target, other_lines in modules[i + 1:]:
            if target != other_target:
                continue
            print(f"MODULE_DIFF {key} {other_key} {target}", flush=True)
            diff = list(difflib.unified_diff(lines, other_lines, fromfile=key, tofile=other_key))
            print("\n".join(diff[:700]), flush=True)
