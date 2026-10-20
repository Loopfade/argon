#!/usr/bin/env python3
"""Compare cached Clang module metadata; never write to the restored cache."""

import collections
import difflib
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from importlib.machinery import SourceFileLoader

manifest_tool = SourceFileLoader(
    "manifest_tool", str(pathlib.Path(__file__).with_name("inspect-compiler-cache.py"))
).load_module()
cache = pathlib.Path(sys.argv[1]).resolve()
results_by_size = sorted(cache.rglob("*R"), key=lambda p: p.stat().st_size, reverse=True)
seen = set()
modules = []
candidate_count = 0

with tempfile.TemporaryDirectory() as temp:
    root = pathlib.Path(temp)
    for result in results_by_size[:150]:
        key = "".join(result.relative_to(cache).parts)[:-1]
        for key, infos in [(key, [])]:
            if key in seen:
                continue
            seen.add(key)
            output = root / key
            output.mkdir()
            subprocess.run(["ccache", "--extract-result", str(result)], cwd=output, check=True)
            obj = output / "ccache-result.o"
            if not obj.is_file():
                continue
            with obj.open("rb") as stream:
                magic = stream.read(4)
            candidate_count += 1
            if candidate_count <= 12:
                print(f"CANDIDATE result={result.relative_to(cache)} "
                      f"dependencies={len(infos)} magic={magic.hex()} "
                      f"bytes={obj.stat().st_size}", flush=True)
            if magic != b"CPCH":
                shutil.rmtree(output)
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
            print(f"CACHED_MODULE key={key} result={result.relative_to(cache)} "
                  f"object_bytes={obj.stat().st_size} dependency_count={len(infos)}", flush=True)
            print("DEPENDENCY_TARGET " + deps.split(":", 1)[0], flush=True)
            if deps.startswith("obj/"):
                for line in selected:
                    if "SUBMODULE_" in line and re.search(r"sysroot/usr/include/[^/]+/asm/(?:hwcap|ptrace)\\.h", line):
                        print(f"MODULE_ARCH_HEADER {key} {line}", flush=True)
            if not selected:
                print("UNRECOGNIZED_DUMP " + dump[:4000], flush=True)
            modules.append((key, deps.split(":", 1)[0], selected))
    print(f"Cached modules found: {len(modules)}", flush=True)
    for i, (key, target, lines) in enumerate(modules):
        for other_key, other_target, other_lines in modules[i + 1:]:
            if target != other_target:
                continue
            print(f"MODULE_DIFF {key} {other_key} {target}", flush=True)
            def names(records):
                result = collections.Counter()
                for record in records:
                    match = re.search(r"<(\w+)[^>]*> blob data = '(.*)'", record)
                    if match:
                        result[match.groups()] += 1
                return result
            old_names, new_names = names(lines), names(other_lines)
            for record, count in (old_names - new_names).items():
                print(f"ONLY_FIRST {count} {record}", flush=True)
            for record, count in (new_names - old_names).items():
                print(f"ONLY_SECOND {count} {record}", flush=True)
