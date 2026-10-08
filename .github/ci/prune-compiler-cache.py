#!/usr/bin/env python3
"""Isolate Chromium versions and retain same-version progress until verified."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def cache_prefix(lock, arch):
    if arch not in ("arm64", "arm", "x64", "x86"):
        raise ValueError("Unsupported compiler-cache architecture")
    version, revision = lock["chromium_version"], lock["chromium_commit"]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid pinned Chromium version/revision")
    return f"argon-ccache-v3-{arch}-{version}-{revision}"


def old_version_caches(caches, prefix, arch, ref):
    family = re.compile(rf"^argon-ccache-v([0-9]+)-{re.escape(arch)}-")
    current = family.match(prefix)
    current_version = tuple(map(int, prefix[current.end():].split("-", 1)[0].split(".")))
    obsolete = []
    for entry in caches:
        match = family.match(entry["key"])
        if entry["ref"] != ref or not match or int(match[1]) > int(current[1]):
            continue
        if entry["key"].startswith(prefix + "-"):
            continue
        version = re.match(r"([0-9]+(?:\.[0-9]+){3})-", entry["key"][match.end():])
        # A delayed older build must never purge a newer version's progress.
        if version and tuple(map(int, version[1].split("."))) >= current_version:
            continue
        obsolete.append(entry)
    return obsolete


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
    parser.add_argument("replacement_key", nargs="?")
    parser.add_argument("--prefix-for", choices=("arm64", "arm", "x64", "x86"))
    parser.add_argument("--remove-old-versions", choices=("arm64", "arm", "x64", "x86"))
    args = parser.parse_args()
    if args.prefix_for:
        print(cache_prefix(json.loads(Path("build-lock.json").read_text()), args.prefix_for))
        return
    prefix = os.environ["CCACHE_CACHE_PREFIX"]
    if args.remove_old_versions and prefix != cache_prefix(
            json.loads(Path("build-lock.json").read_text()), args.remove_old_versions):
        raise SystemExit("Current compiler-cache prefix does not match the pinned version")
    ref = os.environ["GITHUB_REF"]
    repository = os.environ["GITHUB_REPOSITORY"]
    if not args.remove_old_versions and (not args.replacement_key or not args.replacement_key.startswith(prefix + "-")):
        raise SystemExit("Replacement key does not match this compiler-cache prefix")
    result = subprocess.check_output([
        "gh", "api", "--method", "GET", f"repos/{repository}/actions/caches",
        "-f", f"ref={ref}", "-f", "key=argon-ccache-", "-f", "per_page=100",
        "--paginate", "--slurp",
    ], text=True)
    caches = [entry for page in json.loads(result) for entry in page["actions_caches"]]
    obsolete = (old_version_caches(caches, prefix, args.remove_old_versions, ref)
                if args.remove_old_versions else
                obsolete_caches(caches, args.replacement_key, prefix, ref))
    if obsolete is None:
        print("::warning::Replacement cache is not confirmed; keeping previous snapshots.")
        return
    for entry in obsolete:
        subprocess.run([
            "gh", "cache", "delete", str(entry["id"]), "--repo", repository,
        ], check=True)
    if args.replacement_key:
        print(f"Verified {args.replacement_key}; removed {len(obsolete)} older snapshots.")
    else:
        print(f"Removed {len(obsolete)} old-version compiler-cache snapshots.")


if __name__ == "__main__":
    main()
