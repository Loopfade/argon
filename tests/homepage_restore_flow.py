"""Run the pinned session sanitizer and navigation builder at the PageState boundary."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from apply_homepage import RESTORE_LOAD_SIGNATURE, method_source

FIXTURES = ROOT / 'tests/fixtures/homepage'
CONTROLLER = FIXTURES / 'restored_navigation_controller.cc.in'
DRIVER = Path('chrome/browser/sessions/chrome_serialized_navigation_driver.cc')
RESTORE_SIGNATURE = 'ContentSerializedNavigationBuilder::ToNavigationEntry('
SANITIZE_SIGNATURE = 'void ChromeSerializedNavigationDriver::Sanitize('
CHANGE_SIGNATURE = 'void ChangeDestination('


def run_restore(source, driver=None):
    if driver is None:
        driver = (FIXTURES / 'chrome_serialized_navigation_driver.cc.in').read_text()
    program = (FIXTURES / 'RestoreNavigationFlow.cc.in').read_text()
    program = program.replace('@RESTORE_METHOD@',
                              'std::unique_ptr<content::NavigationEntry>\n'
                              + method_source(source, RESTORE_SIGNATURE))
    program = program.replace('@CHANGE_DESTINATION@', method_source(driver, CHANGE_SIGNATURE))
    program = program.replace('@SANITIZE_METHOD@', method_source(driver, SANITIZE_SIGNATURE))
    return _run_program(program)


def run_restored_home_load(source):
    fixture = CONTROLLER.read_text()
    program = fixture.replace(method_source(fixture, RESTORE_LOAD_SIGNATURE),
                              method_source(source, RESTORE_LOAD_SIGNATURE), 1)
    return _run_program(program)


def _run_program(program):
    with tempfile.TemporaryDirectory(prefix='argon-home-restore-') as directory:
        root = Path(directory)
        path = root / 'restore.cc'
        path.write_text(program)
        binary = root / 'restore'
        compiled = subprocess.run(['c++', '-std=c++20', '-DIS_ANDROID=1', '-Wall',
                                   '-Wextra', '-Werror', str(path), '-o', str(binary)],
                                  capture_output=True, text=True)
        if compiled.returncode:
            raise ValueError('Restore regression compilation failed:\n' + compiled.stderr)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return result.stdout


def verify_upstream(builder, driver):
    import apply_homepage as homepage

    expected = (FIXTURES / 'chrome_serialized_navigation_driver.cc.in').read_text()
    for signature in (SANITIZE_SIGNATURE, CHANGE_SIGNATURE):
        if method_source(driver, signature) != method_source(expected, signature):
            raise ValueError('Pinned Android session sanitization changed: ' + signature)
    return run_restore(homepage.patch_session_restore(builder), driver).strip()
