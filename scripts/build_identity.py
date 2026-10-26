#!/usr/bin/env python3
"""Select the standalone test app only when explicitly requested by its workflow."""
import argparse
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STANDALONE_TEST_ENV = "ARGON_STANDALONE_TEST"
CHANNEL_CONSTANTS = Path("chrome/android/java/res_titanium_base/values/channel_constants.xml")


def application_identity(lock, mode, *, standalone_test=None):
    if standalone_test is None:
        value = os.environ.get(STANDALONE_TEST_ENV, "false")
        if value not in ("true", "false"):
            raise ValueError(f"{STANDALONE_TEST_ENV} must be true or false")
        standalone_test = value == "true"
    if standalone_test and mode != "test":
        raise ValueError("The standalone test app requires test signing")
    return {
        "variant": "standalone-test" if standalone_test else "standard",
        "application_id": lock["application_id"] + (".test" if standalone_test else ""),
        "application_label": "Argon Test" if standalone_test else "Argon",
    }


def configure(src, output, arch, ccache, mode):
    from configure_build import render_gn_args

    lock = json.loads((ROOT / "build-lock.json").read_text())
    identity = application_identity(lock, mode)
    args = render_gn_args(arch, ccache=ccache)
    before = f'chrome_public_manifest_package = "{lock["application_id"]}"'
    if args.splitlines().count(before) != 1:
        raise ValueError("Expected one reviewed manifest package assignment")
    args = args.replace(before, f'chrome_public_manifest_package = "{identity["application_id"]}"', 1)
    resource = src / CHANNEL_CONSTANTS
    text = resource.read_text()
    pattern = r'(<string\b[^>]*\bname="app_name"[^>]*>)(Argon(?: Test)?)(</string>)'
    if len(re.findall(pattern, text)) != 1:
        raise ValueError("Expected the reviewed Argon application name resource")
    branded = re.sub(pattern, lambda m: m[1] + identity["application_label"] + m[3], text)
    # Validate both inputs before writing, and preserve timestamps on resumes.
    if branded != text:
        resource.write_text(branded)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text() != args:
        output.write_text(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--arch", required=True, choices=("arm64", "arm"))
    parser.add_argument("--ccache", action="store_true")
    parser.add_argument("--mode", required=True, choices=("test", "release"))
    args = parser.parse_args()
    configure(args.chromium_src, args.output, args.arch, args.ccache, args.mode)


if __name__ == "__main__":
    main()
