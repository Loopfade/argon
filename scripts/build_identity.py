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
MANIFEST = Path("chrome/android/java/AndroidManifest.xml")
VERSION_CODE_ENV = "ARGON_TEST_VERSION_CODE"
GOOGLE_CHROME_SCHEME = '<data android:scheme="googlechrome" />'
DISABLED_SCHEME = '{# Argon Test omits the production googlechrome URI scheme. #}'
ORIGINAL_PACKAGE = '<original-package android:name="org.chromium.chrome" />'
DISABLED_ORIGINAL = '{# Argon Test does not adopt an existing Chromium package. #}'


def application_identity(lock, mode, *, standalone_test=None):
    if mode not in ("test", "release"):
        raise ValueError("Unknown signing mode")
    if standalone_test is None:
        value = os.environ.get(STANDALONE_TEST_ENV, "false")
        if value not in ("true", "false"):
            raise ValueError(f"{STANDALONE_TEST_ENV} must be true or false")
        standalone_test = value == "true"
    identity = {
        "variant": "standalone-test" if standalone_test else "standard",
        "application_id": lock["application_id"] + (".test" if standalone_test else ""),
        "application_label": "Argon Test" if standalone_test else "Argon",
    }
    version = os.environ.get(VERSION_CODE_ENV, "") if standalone_test else ""
    if version:
        if not re.fullmatch(r"[1-9][0-9]*", version) or int(version) > 2100000000:
            raise ValueError("Invalid standalone test versionCode")
        identity["version_code"] = int(version)
    return identity


def apk_filename(lock, mode, identity, abi):
    name = "Argon-Test" if identity["variant"] == "standalone-test" else "Argon"
    return f'{name}-{lock["chromium_version"]}-{mode}-{abi}.apk'


def configure(src, output, arch, ccache, mode):
    from configure_build import render_gn_args

    lock = json.loads((ROOT / "build-lock.json").read_text())
    identity = application_identity(lock, mode)
    args = render_gn_args(arch, ccache=ccache)
    before = f'chrome_public_manifest_package = "{lock["application_id"]}"'
    if args.splitlines().count(before) != 1:
        raise ValueError("Expected one reviewed manifest package assignment")
    args = args.replace(before, f'chrome_public_manifest_package = "{identity["application_id"]}"', 1)
    if "version_code" in identity:
        args += f'\nandroid_override_version_code = "{identity["version_code"]}"\n'
    resource = src / CHANNEL_CONSTANTS
    text = resource.read_text()
    pattern = r'(<string\b[^>]*\bname="app_name"[^>]*>)(Argon(?: Test)?)(</string>)'
    if len(re.findall(pattern, text)) != 1:
        raise ValueError("Expected the reviewed Argon application name resource")
    branded = re.sub(pattern, lambda m: m[1] + identity["application_label"] + m[3], text)
    manifest_path = src / MANIFEST
    manifest = manifest_path.read_text()
    if manifest.count(GOOGLE_CHROME_SCHEME) + manifest.count(DISABLED_SCHEME) != 1:
        raise ValueError("Expected one reviewed googlechrome URI registration")
    # http/https remain ordinary browser handlers. The Chrome-specific scheme
    # must not route production/authentication links to the side-by-side app.
    scheme = DISABLED_SCHEME if identity["variant"] == "standalone-test" else GOOGLE_CHROME_SCHEME
    branded_manifest = manifest.replace(GOOGLE_CHROME_SCHEME, scheme).replace(DISABLED_SCHEME, scheme)
    if "<original-package" in manifest and manifest.count(ORIGINAL_PACKAGE) != 1:
        raise ValueError("Unexpected original-package declaration")
    if manifest.count(ORIGINAL_PACKAGE) + manifest.count(DISABLED_ORIGINAL) > 1:
        raise ValueError("Duplicate original-package declaration")
    original = DISABLED_ORIGINAL if identity["variant"] == "standalone-test" else ORIGINAL_PACKAGE
    branded_manifest = branded_manifest.replace(ORIGINAL_PACKAGE, original).replace(DISABLED_ORIGINAL, original)
    # Validate both inputs before writing, and preserve timestamps on resumes.
    if branded != text:
        resource.write_text(branded)
    if branded_manifest != manifest:
        manifest_path.write_text(branded_manifest)
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
