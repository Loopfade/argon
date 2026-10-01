#!/usr/bin/env python3
"""Reject a prepared image whose source inputs differ from the checkout."""
import argparse
from pathlib import Path


# Runtime CI/signing changes can use the existing image. These files, however,
# have already been applied to the Chromium tree baked into that image.
PREPARED_INPUTS = (
    ".gclient", "build-lock.json", "common.sh", "patch.sh",
    "scripts/apply_scoped_ca.py", "scripts/fetch_pinned_extension.py",
    "scripts/fetch_pinned_filter_lists.py", "chromium_overlay", "certificates",
    "extensions", "res",
)


def inputs(root):
    files = {}
    for relative in PREPARED_INPUTS:
        source = root / relative
        if not source.exists():
            raise ValueError(f"Missing prepared input: {source}")
        for path in source.rglob("*") if source.is_dir() else [source]:
            if not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            # Generated extension payloads are pinned in build-lock.json.
            if name.startswith("extensions/dist/"):
                continue
            files[name] = path.read_bytes()
    # build.sh also contains runtime checkpoint/signing code, which need not
    # invalidate the image. Compare its source-preparation branch separately.
    script = (root / "build.sh").read_text()
    start, end = "else\nexport VERSION\n", "\nconfigure_args="
    if script.count(start) != 1 or script.count(end) != 1:
        raise ValueError("Cannot identify the prepared-source section of build.sh")
    files["build.sh (source preparation)"] = script.split(start, 1)[1].split(end, 1)[0].encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--checkout", required=True, type=Path)
    args = parser.parse_args()
    prepared, checkout = inputs(args.prepared), inputs(args.checkout)
    changed = sorted(path for path in prepared.keys() | checkout.keys()
                     if prepared.get(path) != checkout.get(path))
    if changed:
        raise SystemExit("Prepared image is stale; rebuild and pin its digest. Changed inputs: "
                         + ", ".join(changed))
    print("Prepared Chromium inputs match the checkout.")


if __name__ == "__main__":
    main()
