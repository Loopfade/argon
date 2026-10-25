#!/usr/bin/env python3
"""Apply the bundled homepage patch to the exact pinned Chromium inputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

from upstream_sources import chromium_url, download

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('homepage', ROOT / 'scripts/apply_homepage.py')
homepage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homepage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', type=Path)
    args = parser.parse_args()
    revision = json.loads((ROOT / 'build-lock.json').read_text())['chromium_commit']
    with tempfile.TemporaryDirectory() as directory:
        src = Path(directory)
        for path in homepage.INPUT_PATHS:
            value = ((args.chromium_src / path).read_text() if args.chromium_src
                     else download(chromium_url(revision, path)))
            target = src / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
        homepage.apply(src)
        before = {path: path.read_bytes() for path in src.rglob('*') if path.is_file()}
        homepage.apply(src)
        after = {path: path.read_bytes() for path in src.rglob('*') if path.is_file()}
        if before != after:
            raise AssertionError('Homepage patch must be idempotent on pinned Chromium')
    print(f'Bundled homepage patch applies cleanly to Chromium {revision}.')


if __name__ == '__main__':
    main()
