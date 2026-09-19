#!/usr/bin/env python3
"""Check inputs and available resources before a full Chromium build."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from apply_scoped_ca import ROOT, load_root, root_header


def validate_inputs() -> dict:
    lock = json.loads((ROOT / "build-lock.json").read_text())
    actual = subprocess.check_output(
        ["git", "-C", str(ROOT / "vanadium"), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != lock["vanadium_commit"]:
        raise ValueError("Vanadium submodule differs from build-lock.json")
    version = re.search(
        r'android_default_version_name = "([^"]+)"',
        (ROOT / "vanadium/args.gn").read_text(),
    )[1]
    if version != lock["chromium_version"]:
        raise ValueError("Vanadium/Chromium versions differ")
    der = load_root()
    if (ROOT / "chromium_overlay/net/cert/titanium_ru_root.h").read_text() != root_header(der):
        raise ValueError("Root header differs from the pinned certificate")
    args = (ROOT / "args.gn").read_text()
    for required in ['target_cpu = "arm64"', 'is_desktop_android = true',
                     f'chrome_public_manifest_package = "{lock["application_id"]}"']:
        if required not in args:
            raise ValueError(f"Required build argument missing: {required}")
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-only", action="store_true")
    args = parser.parse_args()
    lock = validate_inputs()
    print(f'Inputs verified: Chromium {lock["chromium_version"]}, arm64, extensions enabled')
    print(f'Russian root DER SHA-256: {hashlib.sha256(load_root()).hexdigest()}')
    if not args.inputs_only:
        free = shutil.disk_usage(ROOT).free / (1024 ** 3)
        if free < 100:
            raise SystemExit(
                f"Full Chromium build blocked: {free:.1f} GiB free; at least 100 GiB required. "
                "Use a dedicated Linux runner with sufficient disk space."
            )


if __name__ == "__main__":
    main()
