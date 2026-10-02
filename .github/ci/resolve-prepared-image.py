#!/usr/bin/env python3
"""Resolve the single moving prepared-image tag to an immutable digest."""
import argparse
import json
import re
import subprocess


DIGEST = r"sha256:[0-9a-f]{64}"
REPOSITORY = r"ghcr\.io/[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*"
TAG = "branch-main"


def resolve(image: str) -> str:
    image = image.lower()
    if re.fullmatch(REPOSITORY + "@" + DIGEST, image):
        return image

    if re.fullmatch(REPOSITORY, image):
        repository = image
        tag = f"{image}:{TAG}"
    elif re.fullmatch(REPOSITORY + ":" + TAG, image):
        repository = image.rsplit(":", 1)[0]
        tag = image
    else:
        raise ValueError(
            "Expected a GHCR repository, its :branch-main tag, or an immutable SHA-256 image"
        )

    # Inspect only the registry manifest, without downloading Chromium layers.
    manifest = json.loads(subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", tag,
         "--format", "{{json .Manifest}}"], text=True,
    ))
    digest = manifest.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(DIGEST, digest):
        raise ValueError(f"Registry did not return a valid digest for {tag}")
    return f"{repository}@{digest}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    print(resolve(args.image))


if __name__ == "__main__":
    main()
