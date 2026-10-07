#!/usr/bin/env python3
"""Keep the previous compiler cache unless its replacement exists on GitHub."""
import argparse
import json
import os
import subprocess


def obsolete_caches(caches, replacement_key, prefix, ref):
    matching = [entry for entry in caches
                if entry["ref"] == ref and entry["key"].startswith(prefix + "-")]
    replacement = next((entry for entry in matching
                        if entry["key"] == replacement_key
                        and entry["size_in_bytes"] > 0), None)
    if replacement is None:
        return None
    # Never delete another ABI/ref or a snapshot created by a newer run.
    return [entry for entry in matching
            if entry["key"] != replacement_key
            and entry.get("version") == replacement.get("version")
            and entry["created_at"] < replacement["created_at"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replacement_key")
    args = parser.parse_args()
    prefix = os.environ["CCACHE_CACHE_PREFIX"]
    ref = os.environ["GITHUB_REF"]
    repository = os.environ["GITHUB_REPOSITORY"]
    if not args.replacement_key.startswith(prefix + "-"):
        raise SystemExit("Replacement key does not match this compiler-cache prefix")
    result = subprocess.check_output([
        "gh", "api", "--method", "GET", f"repos/{repository}/actions/caches",
        "-f", f"ref={ref}", "-f", f"key={prefix}-", "-f", "per_page=100",
        "--paginate", "--slurp",
    ], text=True)
    caches = [entry for page in json.loads(result) for entry in page["actions_caches"]]
    obsolete = obsolete_caches(caches, args.replacement_key, prefix, ref)
    if obsolete is None:
        print("::warning::Replacement cache is not confirmed; keeping previous snapshots.")
        return
    for entry in obsolete:
        subprocess.run([
            "gh", "cache", "delete", str(entry["id"]), "--repo", repository,
        ], check=True)
    print(f"Verified {args.replacement_key}; removed {len(obsolete)} older snapshots.")


if __name__ == "__main__":
    main()
