#!/usr/bin/env python3
"""Compile the changed production C++ objects and Android Java without building an APK."""
import argparse
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--java', action='store_true')
    args = parser.parse_args()
    output = subprocess.check_output(['ninja', '-C', str(args.out), '-t', 'targets', 'all'], text=True)
    objects = [line.split(':', 1)[0] for line in output.splitlines() if line.startswith('obj/')]
    targets = []
    for name in ('argon_exit', 'render_process_host_impl', 'chrome_content_browser_client'):
        matches = [target for target in objects if re.search(r'[/\.]' + name + r'\.o$', target)]
        if len(matches) != 1:
            raise ValueError(f'Expected one production {name} object, found {matches}')
        targets.extend(matches)
    if args.java:
        targets.append('chrome_java')
    subprocess.run(['autoninja', '-C', str(args.out), '-j', '2', *targets], check=True)
    print('PASS: production exit, renderer launch guard and browser integration compile')


if __name__ == '__main__':
    main()
