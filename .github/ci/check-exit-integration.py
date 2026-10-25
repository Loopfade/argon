#!/usr/bin/env python3
"""Verify production exit targets in GN without starting Android compilation."""
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
    # An object target can still pull tens of thousands of transitive Chromium
    # actions. Keep ordinary validation read-only; build.sh compiles these targets
    # in the existing resumable manual build with its architecture-specific cache.
    query = subprocess.check_output(
        ['ninja', '-C', str(args.out), '-t', 'query', *targets], text=True)
    (args.out / 'argon-exit-targets.txt').write_text(query)
    print('PASS: production exit and renderer guard targets are present in GN')


if __name__ == '__main__':
    main()
