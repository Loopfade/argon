"""Run extracted Chromium navigation methods with host platform adapters."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/user_agent"
ANDROID = "navigation_controller_android.cc.in"
CONTROLLER = "navigation_controller_impl.cc.in"
CONTROLLER_PATH = Path("content/browser/renderer_host/navigation_controller_impl.cc")
ANDROID_METHODS = (
    "bool NavigationControllerAndroid::GetUseDesktopUserAgent(",
    "void NavigationControllerAndroid::SetUseDesktopUserAgent(",
    "void NavigationControllerAndroid::SetUseDesktopUserAgentInternal(",
)
CONTROLLER_METHODS = (
    "void NavigationControllerImpl::LoadOriginalRequestURL(",
    "void NavigationControllerImpl::DiscardPendingEntry(",
)
SCENARIOS = ("cycles", "guards", "retry", "no_reload", "deferred", "superseded")


def methods(source, names):
    result = []
    for name in names:
        if source.count(name) != 1:
            raise ValueError(f"Expected one production definition: {name}")
        start = source.index(name)
        end = source.index("\n}\n", start) + 3
        result.append(source[start:end])
    return "\n".join(result)


def compile_flow(directory, android_source, controller_source=None):
    if controller_source is None:
        controller_source = (FIXTURES / CONTROLLER).read_text()
    program = (FIXTURES / "NavigationFlow.cc.in").read_text()
    program = program.replace("@ANDROID_METHODS@", methods(android_source, ANDROID_METHODS))
    program = program.replace("@CONTROLLER_METHODS@", methods(controller_source, CONTROLLER_METHODS))
    source = directory / "flow.cc"
    source.write_text(program)
    binary = directory / "flow"
    result = subprocess.run([
        "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        str(source), "-o", str(binary),
    ], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stdout + result.stderr)
    return binary


def run_flow(binary, scenario):
    return subprocess.run([str(binary), scenario], capture_output=True, text=True)


def verify_upstream(android_source, controller_source):
    """Called by pinned-source CI after applying the Argon patch."""
    import apply_user_agent as patch
    expected = patch.patch_navigation_controller((FIXTURES / ANDROID).read_text())
    if methods(android_source, ANDROID_METHODS) != methods(expected, ANDROID_METHODS):
        raise ValueError("Chromium UA switching changed; review the navigation fixture")
    expected_controller = (FIXTURES / CONTROLLER).read_text()
    if methods(controller_source, CONTROLLER_METHODS) != methods(expected_controller, CONTROLLER_METHODS):
        raise ValueError("Chromium reload/cancellation changed; review the navigation fixture")
    with tempfile.TemporaryDirectory(prefix="argon-ua-navigation-") as temporary:
        binary = compile_flow(Path(temporary), android_source, controller_source)
        for scenario in SCENARIOS:
            result = run_flow(binary, scenario)
            if result.returncode:
                raise ValueError(f"{scenario}: {result.stdout}{result.stderr}")
    return "PASS: production UA navigation cycles, cancellation retries and deferred selections"
