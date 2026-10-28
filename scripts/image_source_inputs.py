#!/usr/bin/env python3
"""Canonical inputs to the reusable Chromium/toolchain Docker layer."""
import argparse
import json
from pathlib import Path


SOURCE_KEYS = (
    "chromium_version", "chromium_commit", "depot_tools_commit",
    "vanadium_commit", "boringssl_commit", "extension_url", "extension_sha256",
    "filter_lists",
)


def source_inputs(lock):
    return {key: lock[key] for key in SOURCE_KEYS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("build-lock.json"))
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    inputs = source_inputs(json.loads(args.lock.read_text()))
    if args.check:
        if inputs != json.loads(args.check.read_text()):
            raise SystemExit("Prepared source pins differ from build-lock.json")
    else:
        print(json.dumps(inputs, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
