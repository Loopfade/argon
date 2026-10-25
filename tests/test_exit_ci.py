"""Keep graph validation from accidentally launching a cold Android build."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NINJA = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['ARGON_QUERY_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[2:] == ['-t', 'targets', 'all']:
    for name in ('argon_exit', 'render_process_host_impl', 'chrome_content_browser_client'):
        if name != os.environ.get('ARGON_OMIT_TARGET'):
            print('obj/chrome/browser/android/android/' + name + '.o: cxx')
elif args[2:4] == ['-t', 'query']:
    for name in args[4:]:
        print(name + ':\\n  input: cxx')
else:
    pathlib.Path(os.environ['ARGON_COMPILE_MARKER']).touch()
    sys.exit(99)
'''


class ExitCiTests(unittest.TestCase):
    def run_graph_check(self, omit=''):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            out = directory / 'out'
            out.mkdir()
            ninja = directory / 'ninja'
            ninja.write_text(NINJA)
            ninja.chmod(0o755)
            autoninja = directory / 'autoninja'
            autoninja.write_text('#!/usr/bin/env python3\nimport os, pathlib\n'
                'pathlib.Path(os.environ["ARGON_COMPILE_MARKER"]).touch()\nraise SystemExit(99)\n')
            autoninja.chmod(0o755)
            log, marker = directory / 'queries.jsonl', directory / 'compilation'
            env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'],
                       ARGON_QUERY_LOG=str(log), ARGON_COMPILE_MARKER=str(marker),
                       ARGON_OMIT_TARGET=omit)
            result = subprocess.run([sys.executable,
                str(ROOT / '.github/ci/check-exit-integration.py'),
                '--out', str(out), '--java'], env=env, capture_output=True, text=True)
            self.assertFalse(marker.exists(), 'graph check started Android compilation')
            queries = [json.loads(line) for line in log.read_text().splitlines()]
            report = out / 'argon-exit-targets.txt'
            return result, queries, report.read_text() if report.exists() else ''

    def test_only_ninja_tool_queries_are_executed(self):
        result, queries, report = self.run_graph_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([query[2:4] for query in queries],
                         [['-t', 'targets'], ['-t', 'query']])
        self.assertIn('argon_exit.o:', report)
        self.assertIn('chrome_java:', report)

    def test_missing_exit_object_fails_validation(self):
        result, queries, report = self.run_graph_check(omit='argon_exit')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Expected one production argon_exit object', result.stderr)
        self.assertEqual(len(queries), 1)
        self.assertEqual(report, '')
