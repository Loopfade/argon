#!/usr/bin/env python3
"""Resolve the locked Chromium version to an immutable prepared-image digest."""
import argparse
import json
from pathlib import Path
import re
import subprocess


DIGEST = r"sha256:[0-9a-f]{64}"
REPOSITORY = r"ghcr\.io/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*"


def resolve(image: str, version: str) -> str:
    image = image.lower()
    if re.fullmatch(REPOSITORY + "@" + DIGEST, image):
        return image
    if not re.fullmatch(REPOSITORY, image):
        raise ValueError("Expected a GHCR repository or an immutable SHA-256 image")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", version):
        raise ValueError("Invalid locked Chromium version")
    tag = f"{image}:chromium-{version}"
    # Inspect only the registry manifest, without downloading Chromium layers.
    manifest = json.loads(subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", tag,
         "--format", "{{json .Manifest}}"], text=True,
    ))
    digest = manifest.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(DIGEST, digest):
        raise ValueError(f"Registry did not return a valid digest for {tag}")
    return f"{image}@{digest}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--lock", type=Path, default=Path("build-lock.json"))
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    print(resolve(args.image, lock["chromium_version"]))


if __name__ == "__main__":
    main()
