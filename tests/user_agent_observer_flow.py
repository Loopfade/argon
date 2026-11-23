"""Run the pinned native RDS observer with navigation and Android adapters."""
from pathlib import Path
import subprocess
import tempfile

from user_agent_navigation_flow import methods

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/user_agent'
SIGNATURE = 'void RequestDesktopSiteWebContentsObserverAndroid::DidStartNavigation('
SCENARIOS = ('history', 'site_defaults', 'subframes')


def compile_observer(directory, source):
    program = (FIXTURES / 'ObserverFlow.cc.in').read_text()
    program = program.replace('@OBSERVER_METHOD@', methods(source, (SIGNATURE,)))
    path = directory / 'observer.cc'
    path.write_text(program)
    binary = directory / 'observer'
    compiled = subprocess.run(
        ['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(binary)],
        capture_output=True, text=True)
    if compiled.returncode:
        raise ValueError(compiled.stdout + compiled.stderr)
    return binary


def run_observer(binary, scenario):
    return subprocess.run([str(binary), scenario], capture_output=True, text=True)


def verify_upstream(source):
    import apply_user_agent as patch
    expected = patch.patch_request_desktop_observer(
        (FIXTURES / 'request_desktop_observer.cc.in').read_text())
    if methods(source, (SIGNATURE,)) != methods(expected, (SIGNATURE,)):
        raise ValueError('Pinned RDS observer changed; review the history regression')
    with tempfile.TemporaryDirectory(prefix='argon-ua-observer-') as directory:
        binary = compile_observer(Path(directory), source)
        for scenario in SCENARIOS:
            result = run_observer(binary, scenario)
            if result.returncode:
                raise ValueError(result.stdout + result.stderr)
    return 'PASS: pinned RDS observer preserves tab UA on history and retains site defaults'
