#!/usr/bin/env python3
"""Reject undersized build storage before downloading Chromium image layers."""
import argparse
from pathlib import Path
import shutil
import sys

GIB = 1024 ** 3
MINIMUM_FREE = 100 * GIB


def check(paths):
    ready = True
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
        except OSError as error:
            print(f"::error::Не удалось проверить место на диске {path}: {error}", file=sys.stderr)
            ready = False
            continue
        print(f"{path}: свободно {usage.free / GIB:.1f} ГиБ из {usage.total / GIB:.1f} ГиБ")
        if usage.free < MINIMUM_FREE:
            print(
                f"::error::На диске {path} свободно {usage.free / GIB:.1f} ГиБ; "
                "для подготовки Chromium и сборки Argon нужно не менее 100 ГиБ. "
                "Выберите подходящий Linux x64 исполнитель в параметре runner полного workflow. "
                "Метка ubuntu-24.04 сама по себе не гарантирует достаточный размер диска.",
                file=sys.stderr,
            )
            ready = False
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, action="append", required=True)
    args = parser.parse_args()
    return 0 if check(args.path) else 1


if __name__ == "__main__":
    sys.exit(main())
