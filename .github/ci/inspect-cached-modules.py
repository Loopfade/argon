#!/usr/bin/env python3
"""Compare cached Clang module metadata without changing the restored cache."""

import argparse
import collections
import pathlib
import re
import subprocess
import tempfile


def record_names(records):
    result = collections.Counter()
    for record in records:
        match = re.search(r"<(\w+)[^>]*> blob data = '(.*)'", record)
        if match:
            result[match.groups()] += 1
    return result


def inspect_modules(cache, limit=150):
    cache = cache.resolve()
    results = sorted(
        (path for path in cache.rglob("*R") if path.is_file()),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    modules = []
    for result in results[:limit]:
        key = "".join(result.relative_to(cache).parts)[:-1]
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            subprocess.run(
                ["ccache", "--extract-result", str(result)],
                cwd=output, check=True, timeout=30,
            )
            obj = output / "ccache-result.o"
            if not obj.is_file():
                continue
            with obj.open("rb") as stream:
                if stream.read(4) != b"CPCH":
                    continue
            depfile = output / "ccache-result.d"
            deps = depfile.read_text() if depfile.exists() else ""
            dump = subprocess.run(
                ["llvm-bcanalyzer-18", "--dump", str(obj)],
                capture_output=True, text=True, check=True, timeout=90,
            ).stdout
            # Keep paths and submodule declarations, without printing the AST.
            selected = [
                line for line in dump.splitlines()
                if re.search(
                    r"<(?:INPUT_FILE|SUBMODULE_[A-Z_]+|MODULE_NAME|MODULE_DIRECTORY)\b",
                    line,
                )
            ]
            target = deps.split(":", 1)[0]
            print(
                f"CACHED_MODULE key={key} result={result.relative_to(cache)} "
                f"object_bytes={obj.stat().st_size}", flush=True,
            )
            print("DEPENDENCY_TARGET " + target, flush=True)
            if deps.startswith("obj/"):
                for line in selected:
                    if "SUBMODULE_" in line and re.search(
                        r"sysroot/usr/include/[^/]+/asm/(?:hwcap|ptrace)\.h", line
                    ):
                        print(f"MODULE_ARCH_HEADER {key} {line}", flush=True)
            modules.append((key, target, selected))
    print(f"Cached modules found: {len(modules)}", flush=True)
    for i, (key, target, lines) in enumerate(modules):
        for other_key, other_target, other_lines in modules[i + 1:]:
            if not target or target != other_target:
                continue
            print(f"MODULE_DIFF {key} {other_key} {target}", flush=True)
            old_names, new_names = record_names(lines), record_names(other_lines)
            for record, count in (old_names - new_names).items():
                print(f"ONLY_FIRST {count} {record}", flush=True)
            for record, count in (new_names - old_names).items():
                print(f"ONLY_SECOND {count} {record}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=150)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    inspect_modules(args.cache, args.limit)


if __name__ == "__main__":
    main()
