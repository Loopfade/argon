#!/usr/bin/env python3
"""Apply the reviewed Bare theme port before localization and the other Argon overlays.

Validate every hunk and source catalog before writing. Repeated preparation is
safe; changed Chromium contexts require a reviewed rebase instead of patch fuzz.
"""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'patches/bare-dark-themes.patch'
JAVA = Path('titanium/chromium_src/chrome/android/java/src/org/chromium/chrome/browser/night_mode/ArgonThemeBridge.java')
JAVA_CATALOG = Path('titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni')
JNI_CATALOG = Path('chrome/android/BUILD.gn')


def sections(data):
    result = {}
    for section in re.split(r'(?=^diff --git )', data, flags=re.M):
        if not section.strip():
            continue
        match = re.match(r'diff --git a/(\S+) b/(\S+)\n', section)
        if not match or match[1] != match[2]:
            raise ValueError('Expected an in-place dark-theme source patch')
        path = Path(match[1])
        if path.is_absolute() or '..' in path.parts or path in result:
            raise ValueError('Invalid or duplicate dark-theme source path')
        hunks = []
        for hunk in re.split(r'^@@ .*? @@[^\n]*\n', section, flags=re.M)[1:]:
            lines = hunk.splitlines(keepends=True)
            if not lines or any(line[:1] not in (' ', '+', '-') for line in lines):
                raise ValueError(f'Invalid dark-theme hunk: {path}')
            before = ''.join(line[1:] for line in lines if line[0] in ' -')
            after = ''.join(line[1:] for line in lines if line[0] in ' +')
            if not before or not after or before == after:
                raise ValueError(f'Expected a bounded dark-theme edit: {path}')
            hunks.append((before, after))
        if not hunks:
            raise ValueError(f'No dark-theme edits: {path}')
        result[path] = hunks
    return result


def replace_once(text, before, after):
    if text.count(after) == 1 and before not in text.replace(after, '', 1):
        return text
    if text.count(after) or text.count(before) != 1:
        raise ValueError('Dark-theme source changed; review the Chromium/Argon integration')
    return text.replace(before, after, 1)


INPUT_PATHS = tuple(sections(PATCH.read_text()))


def planned_writes(src, *, wire=True):
    writes = {}
    for path, hunks in sections(PATCH.read_text()).items():
        text = (src / path).read_text()
        for before, after in hunks:
            try:
                text = replace_once(text, before, after)
            except ValueError as error:
                raise ValueError(f'{path}: {error}') from error
        writes[src / path] = text
    if wire:
        java_entry = '  "java/src/org/chromium/chrome/browser/night_mode/ArgonThemeBridge.java",'
        anchor = '  "java/src/org/chromium/chrome/browser/privacy/settings/PrivacySettingsExt.java",'
        writes[src / JAVA_CATALOG] = replace_once(
            (src / JAVA_CATALOG).read_text(), anchor, java_entry + '\n' + anchor)
        anchor = '  generate_jni("chrome_jni_headers") {\n    sources = ['
        entry = '\n      "//' + JAVA.as_posix() + '",'
        # Other overlays prepend their JNI entries to this list. Test presence
        # independently so a later idempotent run does not depend on adjacency.
        text = (src / JNI_CATALOG).read_text()
        if text.count(entry) > 1:
            raise ValueError('Duplicate Argon theme JNI registration')
        if entry not in text:
            text = replace_once(text, anchor, anchor + entry)
        writes[src / JNI_CATALOG] = text
        bridge = (ROOT / 'chromium_overlay' / JAVA).read_text()
        if (src / JAVA).exists() and (src / JAVA).read_text() != bridge:
            raise ValueError('Unexpected Argon theme bridge source')
        writes[src / JAVA] = bridge
    return writes


def apply(src, *, wire=True):
    writes = planned_writes(src, wire=wire)
    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != text:
            path.write_text(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', type=Path, required=True)
    apply(parser.parse_args().chromium_src.resolve())
