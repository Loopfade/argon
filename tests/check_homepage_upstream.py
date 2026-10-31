#!/usr/bin/env python3
"""Apply the bundled homepage patch to the exact pinned Chromium inputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

from upstream_sources import chromium_url, download
from test_homepage_native import (
    DELEGATE_API, TOOLBAR_PHONE, PHONE_TOKEN, verify_delegate_api, verify_toolbar_scroll_source,
    verify_toolbar_startup_source,
    verify_toolbar_capture_source,
    verify_phone_capture_source,
    RESOURCE_COORDINATOR, VIEW_RESOURCE_ADAPTER, verify_activation_sources,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('homepage', ROOT / 'scripts/apply_homepage.py')
homepage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homepage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', type=Path)
    args = parser.parse_args()
    revision = json.loads((ROOT / 'build-lock.json').read_text())['chromium_commit']

    def read(path):
        if args.chromium_src:
            return (args.chromium_src / path).read_text()
        return download(chromium_url(revision, path))

    delegate = read(DELEGATE_API)
    verify_delegate_api(delegate)
    toolbar_phone = read(TOOLBAR_PHONE)
    verify_toolbar_scroll_source(toolbar_phone)
    token = read(PHONE_TOKEN)
    verify_phone_capture_source(toolbar_phone, token)
    extra = {}
    for path in (homepage.TOOLBAR_MANAGER, homepage.TOOLBAR_CONTAINER,
                 RESOURCE_COORDINATOR, VIEW_RESOURCE_ADAPTER):
        extra[path] = read(path)
    verify_activation_sources(extra[homepage.TOOLBAR_MANAGER], extra[homepage.TOOLBAR_CONTAINER],
                              extra[RESOURCE_COORDINATOR], extra[VIEW_RESOURCE_ADAPTER])
    with tempfile.TemporaryDirectory() as directory:
        src = Path(directory)
        for path in homepage.INPUT_PATHS:
            value = extra[path] if path in extra else read(path)
            if path == homepage.TOOLBAR_MANAGER:
                reviewed = value.replace(homepage.TOOLBAR_INIT_AFTER, homepage.TOOLBAR_INIT_BEFORE, 1)
                reviewed = reviewed.replace(homepage.TOOLBAR_PROVIDER_AFTER, homepage.TOOLBAR_PROVIDER_BEFORE, 1)
                verify_toolbar_startup_source(reviewed)
            if path == homepage.TOOLBAR_CONTAINER:
                verify_toolbar_capture_source(value)
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
