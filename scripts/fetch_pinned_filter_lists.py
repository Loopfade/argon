#!/usr/bin/env python3
"""Fetch the filter lists pinned by URL and SHA-256 in build-lock.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def load_entries() -> list[dict[str, str]]:
    lock = json.loads((ROOT / "build-lock.json").read_text())
    entries = lock.get("filter_lists")
    if not isinstance(entries, list) or not entries:
        raise ValueError("build-lock.json must contain pinned filter_lists")
    return entries


def normalize(data: bytes, strip_volatile_headers: bool) -> bytes:
    if not strip_volatile_headers:
        return data
    volatile = (
        b"! Checksum:",
        b"! Version:",
        b"! Last modified:",
        b"! Commit:",
    )
    return b"".join(
        line for line in data.splitlines(keepends=True) if not line.startswith(volatile)
    )


def download(entry: dict[str, str], opener=urllib.request.urlopen) -> bytes:
    url = entry["url"]
    expected = entry["sha256"]
    if not url.startswith("https://"):
        raise ValueError(f"Filter-list URL must use HTTPS: {url}")
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with opener(request, context=context, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Filter-list download returned HTTP {response.status}: {url}")
        data = normalize(response.read(), entry.get("strip_volatile_headers", False))
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(
            f"Filter-list digest mismatch for {entry['name']}: "
            f"expected {expected}, got {actual}"
        )
    return data


def fetch(output: Path, entries: list[dict[str, str]], opener=urllib.request.urlopen) -> None:
    # Preserve Vanadium's deterministic URL ordering while refusing partial or
    # changed downloads. The destination is replaced only after every digest passes.
    payload = b"".join(download(entry, opener) for entry in sorted(entries, key=lambda e: e["url"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    fetch(args.output, load_entries())


if __name__ == "__main__":
    main()
