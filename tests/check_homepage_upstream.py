#!/usr/bin/env python3
"""Apply the bundled homepage patch to the exact pinned Chromium inputs."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

from upstream_sources import chromium_url, download
from test_homepage_bookmarks import WEBUI_HANDLER, verify_bookmark_webui_api
from test_homepage_bookmark_manager import BOOKMARK_MANAGER_APIS, verify_bookmark_manager_api
from test_homepage_bookmark_edit import BOOKMARK_EDIT_APIS, verify_bookmark_edit_api
from test_homepage_shortcut import verify_shortcut_sources
from test_homepage_startup import run_startup_flow
from test_homepage_native import (
    DELEGATE_API, TOOLBAR_PHONE, PHONE_TOKEN, verify_delegate_api, verify_toolbar_scroll_source,
    URL_OVERRIDES, verify_url_override_api,
    verify_toolbar_startup_source,
    verify_native_toolbar_fallback_source,
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

    verify_bookmark_webui_api(read(WEBUI_HANDLER))
    print('PASS: production bookmark snapshot compiles with the pinned WebUI accessor')
    verify_bookmark_manager_api({path: read(path) for path in BOOKMARK_MANAGER_APIS})
    print('PASS: Android bookmark launcher uses the pinned profile and folder API')
    verify_bookmark_edit_api({path: read(path) for path in BOOKMARK_EDIT_APIS})
    print('PASS: native bookmark editing uses the pinned user-edit and undo-group APIs')
    delegate = read(DELEGATE_API)
    verify_delegate_api(delegate)
    observer = read('chrome/browser/tab/java/src/org/chromium/chrome/browser/tab/TabObserver.java')
    print(run_startup_flow(observer).strip())
    print('PASS: production homepage startup compiles with the complete pinned TabObserver interface')
    service=' '.join(read('components/search_engines/template_url_service.h').split())
    if 'void ResetTemplateURL(TemplateURL* url, const std::u16string& title, const std::u16string& keyword, const std::string& search_url);' not in service:
        raise ValueError('Review the pinned custom search provider edit API')
    if 'bool CreatedByPolicy() const;' not in read('components/search_engines/template_url.h'):
        raise ValueError('Review the pinned search provider policy guard')
    verify_url_override_api(read(URL_OVERRIDES))
    verify_shortcut_sources(read)
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
    verify_native_toolbar_fallback_source(extra[homepage.TOOLBAR_MANAGER])
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
