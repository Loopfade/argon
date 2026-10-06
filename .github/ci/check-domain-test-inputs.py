#!/usr/bin/env python3
"""Verify pinned inputs without rejecting Chromium's applied patch commits."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def verify(lock_path, src, depot):
    expected = json.loads(lock_path.read_text())
    prepared = json.loads((src.parents[1] / "build-lock.json").read_text())
    if prepared != expected:
        raise ValueError("Prepared image inputs do not match build-lock.json")
    # build.sh uses git am for the selected Vanadium patches. The pinned
    # Chromium commit must be the base of those commits, rather than HEAD.
    ancestor = subprocess.run(
        ["git", "-C", str(src), "merge-base", "--is-ancestor",
         expected["chromium_commit"], "HEAD"], capture_output=True, text=True,
    )
    if ancestor.returncode != 0:
        raise ValueError("Prepared Chromium does not descend from its pinned revision")
    actual_depot = subprocess.check_output(
        ["git", "-C", str(depot), "rev-parse", "HEAD"], text=True,
    ).strip()
    if actual_depot != expected["depot_tools_commit"]:
        raise ValueError("Prepared depot_tools revision does not match build-lock.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--depot", required=True, type=Path)
    args = parser.parse_args()
    verify(args.lock, args.src.resolve(), args.depot.resolve())
    print("Domain test sources and toolchain match the pinned inputs.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
