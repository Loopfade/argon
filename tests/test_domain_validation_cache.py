"""Run the production validation driver with local compiler/tool fixtures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_build_recovery import executable

ROOT = Path(__file__).resolve().parents[1]


class DomainValidationCacheTests(unittest.TestCase):
    def run_validation(self, cached, fail=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            src, depot = root / "src", root / "depot"
            src.mkdir()
            depot.mkdir()
            executable(depot / "python3", '''
printf 'python:%s\n' "$*" >> "$COMMAND_LOG"
case "$1" in */check-domain-test-inputs.py|*/check-exit-dex.py|*/check-exit-integration.py) exit 0;; esac
exec "$REAL_PYTHON" "$@"
''')
            executable(depot / "git", "exit 0\n")
            executable(depot / "ccache", 'printf "ccache:%s\\n" "$*" >> "$COMMAND_LOG"\n')
            executable(depot / "ninja", 'printf "ninja:%s\\n" "$*" >> "$COMMAND_LOG"\n')
            executable(depot / "gn", '''
if [[ "$*" == *--root-target=* ]]; then
  mkdir -p "$2"
  printf '#!/bin/sh\nexit 0\n' > "$2/argon_domain_policy_tests"
  chmod +x "$2/argon_domain_policy_tests"
fi
''')
            executable(depot / "autoninja", '''
printf 'compile:%s\n' "$*" >> "$COMMAND_LOG"
if [[ "$*" == *Argon-graph-arm* && "$FAIL_ANDROID" == 1 ]]; then exit 42; fi
''')
            env = {**os.environ, "REAL_PYTHON": sys.executable,
                   "COMMAND_LOG": str(root / "commands"), "FAIL_ANDROID": "1" if fail else "0"}
            env.pop("CCACHE_DIR", None)
            if cached:
                env["CCACHE_DIR"] = str(root / "cache")
            result = subprocess.run(["bash", str(ROOT / ".github/ci/test-domain-policy.sh"),
                                     str(src), str(depot)], env=env, capture_output=True, text=True)
            args = [(src / f"out/{name}/args.gn").read_text()
                    for name in ("Argon-graph-arm64", "Argon-graph-arm")]
            return result, args, (root / "commands").read_text()

    def test_cached_and_uncached_drivers_keep_both_architecture_graphs(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                result, args, commands = self.run_validation(cached)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for text, arch in zip(args, ("arm64", "arm")):
                    self.assertIn(f'target_cpu = "{arch}"', text)
                    self.assertEqual('cc_wrapper = "ccache"' in text, cached)
                self.assertEqual("ccache:--show-stats" in commands, cached)

    def test_compiler_failure_is_not_hidden_by_cache_statistics(self):
        result, _args, commands = self.run_validation(True, fail=True)
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn("ccache:--show-stats", commands)


if __name__ == "__main__":
    unittest.main()
