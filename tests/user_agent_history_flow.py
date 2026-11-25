"""Exercise the real UA history conversion without replacing stored form data."""
from pathlib import Path
import subprocess
import tempfile

from user_agent_navigation_flow import methods

FIXTURES = Path(__file__).resolve().parent / 'fixtures/user_agent'
METHOD = 'void NavigationRequest::SetIsOverridingUserAgent('
HELPER = 'void ResetUserAgentHistoryViewState('
API = Path('third_party/blink/public/common/page_state/page_state_serialization.h')


def run_history(source):
    # With the old method there is no reset helper: the behavioural assertions
    # below must fail instead of pretending a missing symbol proves the bug.
    helper = methods(source, (HELPER,)) if HELPER in source else ''
    program = (FIXTURES / 'HistoryViewStateFlow.cc.in').read_text().replace(
        '@RESET_HELPER@', helper).replace('@NAVIGATION_METHOD@', methods(source, (METHOD,)))
    with tempfile.TemporaryDirectory(prefix='argon-ua-history-') as directory:
        root = Path(directory)
        path = root / 'history.cc'
        path.write_text(program)
        result = subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                 str(path), '-o', str(root / 'history')],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        result = subprocess.run([str(root / 'history')], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
    return 'PASS: target history UA clears only viewport; forms, POST, files, identity and unchanged zoom survive'


def verify_upstream(src):
    import apply_user_agent as patch
    source = (src / patch.NAVIGATION_REQUEST).read_text()
    expected = patch.patch_history_view_state((FIXTURES / 'navigation_request_ua.cc.in').read_text())
    if methods(source, (METHOD, HELPER)) != methods(expected, (METHOD, HELPER)):
        raise ValueError('Pinned navigation UA/history contract changed')
    api = (src / API).read_text()
    for field in ('did_save_scroll_or_scale_state', 'page_scale_factor', 'scroll_offset',
                  'visual_viewport_scroll_offset', 'scroll_anchor_selector',
                  'scroll_anchor_offset', 'scroll_anchor_simhash', 'children'):
        if field not in api:
            raise ValueError('Pinned history serialization field changed: ' + field)
    return run_history(source)
