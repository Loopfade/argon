#!/usr/bin/env python3
"""Keep Chromium's Android system module independent of NDK directory order."""
import argparse
from pathlib import Path

GENERATOR = Path("build/modules/unified/generate_system_modulemap.py")
CONFIG = GENERATOR.with_name("modulemap_config.py")
MARKER = "# ARGON_ANDROID_SYSROOT: use only the target architecture."
ANCHOR = """    # Sort in reverse order to make sure that foo/bar comes before foo, thus
    # ensuring we try resolving foo/bar/baz => baz instead of bar/baz.
"""
ANDROID_FILTER = """    # ARGON_ANDROID_SYSROOT: use only the target architecture.
    # Forced asm/* headers must not come from the first unrelated NDK triple.
    if target_os == 'android':
        android_triples = {
            'arm': 'arm-linux-androideabi',
            'arm64': 'aarch64-linux-android',
        }
        if target_cpu not in android_triples:
            raise ValueError(f'Unsupported Android CPU: {target_cpu}')
        triple = android_triples[target_cpu]
        sysroot_dirs = [
            directory for directory in sysroot_dirs
            if not _TRIPLE.match(directory.name) or directory.name == triple
        ]
        if not any(d.name == triple and d.is_dir() for d in sysroot_dirs):
            raise ValueError(f'Missing Android sysroot include directory: {triple}')

"""


def patch_generator(text):
    replacement = ANDROID_FILTER + ANCHOR
    if text.count(replacement) == 1 and text.count(ANCHOR) == 1 and text.count(MARKER) == 1:
        return text
    if MARKER in text or text.count(ANCHOR) != 1:
        raise ValueError("Chromium system module generator changed; review the Android sysroot patch")
    return text.replace(ANCHOR, replacement, 1)


def apply(src):
    path = src / GENERATOR
    original = path.read_text()
    patched = patch_generator(original)
    if patched != original:
        path.write_text(patched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", required=True, type=Path)
    args = parser.parse_args()
    apply(args.chromium_src)


if __name__ == "__main__":
    main()
