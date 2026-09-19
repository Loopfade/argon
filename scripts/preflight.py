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
from configure_build import TARGET_ABIS, render_gn_args, target_cpu


def validate_inputs(arch: str = "arm64") -> dict:
    cpu = target_cpu(arch)
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
    args = render_gn_args(cpu)
    for required in [f'target_cpu = "{cpu}"', 'target_os = "android"',
                     'enable_android_secondary_abi = false', 'is_desktop_android = true',
                     f'chrome_public_manifest_package = "{lock["application_id"]}"']:
        if required not in args:
            raise ValueError(f"Required build argument missing: {required}")
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-only", action="store_true")
    parser.add_argument("--arch", type=target_cpu, default="arm64")
    args = parser.parse_args()
    lock = validate_inputs(args.arch)
    print(f'Inputs verified: Chromium {lock["chromium_version"]}, '
          f'{TARGET_ABIS[args.arch]} ({args.arch}), extensions enabled')
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
