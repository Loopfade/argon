#!/usr/bin/env python3
"""Run the production exit flow through Chromium's pinned incremental DEX compiler."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / ('chromium_overlay/titanium/chromium_src/chrome/android/java/src/'
               'org/chromium/chrome/browser/exit/ArgonExitFlow.java')


def invoke(command, src):
    return subprocess.run([str(arg) for arg in command], cwd=src,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=180)


def compile_jar(javac, source, work, name, src):
    classes = work / (name + '-classes')
    classes.mkdir()
    result = invoke([javac, '-encoding', 'UTF-8', '-d', classes, source], src)
    if result.returncode:
        raise ValueError('Pinned javac failed:\n' + result.stdout)
    jar = work / (name + '.jar')
    with zipfile.ZipFile(jar, 'w') as archive:
        for path in sorted(classes.rglob('*.class')):
            archive.write(path, path.relative_to(classes).as_posix())
    return jar


def dex(src, jar, work, name):
    output = work / (name + '.dex.jar')
    # Chromium's Ninja depfile writer rejects absolute outputs and inputs.
    # dex.py runs from src, including when the temporary files live in /tmp.
    relative_work = Path(os.path.relpath(work, src))
    relative_jar = os.path.relpath(jar, src)
    result = invoke([
        src / 'third_party/cpython3/host/bin/python3',
        src / 'build/android/gyp/dex.py',
        '--output', relative_work / (name + '.dex.jar'),
        '--depfile', relative_work / (name + '.d'),
        '--min-api=29',
        '--r8-jar-path', 'third_party/r8/d8/cipd/lib/r8.jar',
        '--custom-d8-jar-path', 'third_party/r8/custom_d8.jar',
        '--warnings-as-errors',
        '--incremental-dir', relative_work / (name + '-incremental'),
        '--intermediate',
        '--class-inputs=' + json.dumps([relative_jar]),
        '--desugar',
        '--classpath=' + json.dumps([relative_jar]),
    ], src)
    return result, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', type=Path, required=True)
    args = parser.parse_args()
    src = args.src.resolve()
    javac = src / 'third_party/jdk/current/bin/javac'
    for path in (javac, FLOW, src / 'third_party/cpython3/host/bin/python3',
                 src / 'build/android/gyp/dex.py',
                 src / 'third_party/r8/d8/cipd/lib/r8.jar',
                 src / 'third_party/r8/custom_d8.jar'):
        if not path.is_file():
            raise ValueError('Missing prepared DEX input: ' + str(path))

    with tempfile.TemporaryDirectory(prefix='argon-exit-dex-') as directory:
        work = Path(directory)
        # Prove the smoke check uses the same mode that rejected the APK, rather
        # than silently accepting records with different desugaring settings.
        probe = work / 'RecordProbe.java'
        probe.write_text('public record RecordProbe(int value) {}\n')
        jar = compile_jar(javac, probe, work, 'record-probe', src)
        result, _ = dex(src, jar, work, 'record-probe')
        if not result.returncode or 'Java records are not supported.' not in result.stdout:
            raise ValueError('DEX regression probe did not reproduce the record rejection:\n'
                             + result.stdout)
        print('PASS: pinned Chromium DEX rejects records in min-API 29 intermediate mode',
              flush=True)

        jar = compile_jar(javac, FLOW, work, 'exit-flow', src)
        result, output = dex(src, jar, work, 'exit-flow')
        if result.returncode:
            raise ValueError('Production exit flow failed Chromium DEX compilation:\n'
                             + result.stdout)
        with zipfile.ZipFile(output) as archive:
            payloads = [archive.read(name) for name in archive.namelist()
                        if name.endswith('.dex')]
        if not payloads or any(not payload.startswith(b'dex\n') for payload in payloads):
            raise ValueError('DEX compilation did not produce valid DEX payloads')
        for descriptor in (b'Lorg/chromium/chrome/browser/exit/ArgonExitFlow;',
                           b'Lorg/chromium/chrome/browser/exit/ArgonExitFlow$Selection;'):
            if not any(descriptor in payload for payload in payloads):
                raise ValueError('Production class missing from DEX: ' + descriptor.decode())
        print('PASS: production exit flow and immutable Selection compile into Chromium DEX',
              flush=True)


if __name__ == '__main__':
    main()
