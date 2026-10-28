#!/usr/bin/env python3
"""Read a sample of ccache manifests without changing the cache."""

import argparse
import collections
import concurrent.futures
import json
import pathlib
import random
import re
import subprocess
import time


def parse_manifest(text):
    paths_text, infos_text = text.split("File infos (", 1)
    infos_text, results_text = infos_text.split("Results (", 1)
    paths = dict(re.findall(r"^  (\d+): (.+)$", paths_text, re.MULTILINE))
    infos = {}
    for match in re.finditer(
        r"^  (\d+):\n    Path index: (\d+)\n    Hash: (\S+)\n"
        r"    File size: (\d+)\n    Mtime: ([^\n]+)\n    Ctime: ([^\n]+)",
        infos_text,
        re.MULTILINE,
    ):
        index, path_index, digest, size, mtime, ctime = match.groups()
        infos[index] = (paths[path_index], digest, int(size), mtime, ctime)
    results = []
    for indexes, key in re.findall(
        r"^    File info indexes:([^\n]*)\n    Key: (\S+)",
        results_text,
        re.MULTILINE,
    ):
        results.append((key, [infos[i] for i in indexes.split()]))
    if not results:
        raise ValueError("No result records parsed")
    return results


def inspect_manifest(path):
    result = subprocess.run(
        ["ccache", "--inspect", str(path)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return parse_manifest(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=pathlib.Path)
    parser.add_argument("--limit", type=int, default=8000)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    manifests = sorted(args.cache.rglob("*M"))
    manifests = [p for p in manifests if p.is_file()]
    random.Random(39320555336).shuffle(manifests)
    selected = manifests[: args.limit]
    changed = collections.Counter()
    variants = collections.defaultdict(collections.Counter)
    modules = collections.defaultdict(collections.Counter)
    examples = []
    errors = []
    counts = collections.Counter()
    deadline = time.monotonic() + args.seconds

    def inspect(path):
        if time.monotonic() > deadline:
            return path, None, None
        try:
            return path, inspect_manifest(path), None
        except (ValueError, subprocess.SubprocessError) as exc:
            return path, None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for path, results, error in pool.map(inspect, selected):
            if error:
                counts["errors"] += 1
                if len(errors) < 3:
                    errors.append({"manifest": str(path), "error": error})
                continue
            if results is None:
                counts["skipped_time_limit"] += 1
                continue
            counts["inspected"] += 1
            counts["results"] += len(results)
            counts["multiple_results"] += len(results) > 1
            by_path = collections.defaultdict(dict)
            for key, infos in results:
                for name, digest, size, mtime, ctime in infos:
                    by_path[name][digest] = {
                        "hash": digest, "size": size, "mtime": mtime,
                        "ctime": ctime, "result": key,
                    }
            differing = {}
            for name, hashes in by_path.items():
                if name.endswith((".pcm", ".modulemap")):
                    modules[name].update(hashes.keys())
                if len(hashes) > 1:
                    changed[name] += 1
                    variants[name].update(hashes.keys())
                    differing[name] = list(hashes.values())
            if differing and len(examples) < 5:
                examples.append({
                    "manifest": str(path.relative_to(args.cache)),
                    "results": len(results),
                    "changed": dict(list(differing.items())[:12]),
                })
            if counts["inspected"] % 1000 == 0:
                print(f"Проверено манифестов: {counts['inspected']}", flush=True)
    report = {
        "manifest_files": len(manifests),
        "selected": len(selected),
        "counts": dict(counts),
        "changed_dependencies_within_same_manifest": [
            {"path": path, "manifests": count,
             "hashes": variants[path].most_common(8)}
            for path, count in changed.most_common(40)
        ],
        "module_variants_across_manifests": [
            {"path": path, "hashes": hashes.most_common(8)}
            for path, hashes in sorted(
                modules.items(), key=lambda item: sum(item[1].values()), reverse=True
            )[:40]
        ],
        "examples": examples,
        "errors": errors,
    }
    print(json.dumps(report, indent=2), flush=True)
    if not counts["inspected"]:
        raise SystemExit("No manifests could be inspected")


if __name__ == "__main__":
    main()
