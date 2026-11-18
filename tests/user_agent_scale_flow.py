"""Compile real viewport reset/restoration functions with host layout adapters."""
from pathlib import Path
import re
import subprocess
import tempfile

from user_agent_navigation_flow import methods

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/user_agent"
WEB_VIEW = Path("third_party/blink/renderer/core/exported/web_view_impl.cc")
FRAME_LOADER = Path("third_party/blink/renderer/core/loader/frame_loader.cc")
LOCAL_FRAME_CLIENT = Path("third_party/blink/renderer/core/frame/local_frame_client_impl.cc")
DOCUMENT_LOADER = Path("third_party/blink/renderer/core/loader/document_loader.cc")
DOCUMENT_STATE_IMPL = Path("content/renderer/document_state.cc")
SCALE_API_PATHS = (WEB_VIEW, FRAME_LOADER, LOCAL_FRAME_CLIENT, DOCUMENT_LOADER,
                   DOCUMENT_STATE_IMPL)
PINNED_METHODS = {
    WEB_VIEW: ("void WebViewImpl::ResetScrollAndScaleState(",
               "void WebViewImpl::RefreshPageScaleFactor("),
    FRAME_LOADER: ("void FrameLoader::RestoreScrollPositionAndViewState()",
                   "void FrameLoader::RestoreScrollPositionAndViewState(\n",
                   "bool FrameLoader::NeedsHistoryItemRestore("),
    DOCUMENT_STATE_IMPL: (
        "std::unique_ptr<blink::WebDocumentLoader::ExtraData> DocumentState::Clone()",),
}
SCENARIOS = ("switch", "redirect", "normal_reload", "history", "cancel",
             "subframe", "clone", "remote", "one_shot")


def scale_blocks(source):
    """Extract inserted code at the two production commit boundaries."""
    prepare = re.search(
        r'#if BUILDFLAG\(IS_ANDROID\)\n  // A mode switch.*?\n#endif', source, re.S)
    commit = re.search(
        r'#if BUILDFLAG\(IS_ANDROID\)\n  if \(document_state->TakeResetScrollAndScaleState\(\)\).*?\n#endif',
        source, re.S)
    if not prepare or not commit:
        raise ValueError("Missing UA-mode scale reset at the navigation commit boundaries")
    return prepare[0], commit[0]


def document_members(source):
    public = []
    for name in ("bool is_overriding_user_agent() const",
                 "void set_is_overriding_user_agent(bool state)",
                 "void set_reset_scroll_and_scale_state(bool value)",
                 "bool TakeResetScrollAndScaleState()"):
        match = re.search(r'  ' + re.escape(name) + r' \{.*?\}', source, re.S)
        if not match:
            raise ValueError(f"Missing production DocumentState method: {name}")
        public.append(match[0])
    private = []
    for name in ("is_overriding_user_agent_", "reset_scroll_and_scale_state_"):
        match = re.search(r'  bool ' + name + r' = false;', source)
        if not match:
            raise ValueError(f"Missing default-false DocumentState member: {name}")
        private.append(match[0])
    return "\n".join(public), "\n".join(private)


def compile_scale(directory, renderer, state, *, legacy=False, pinned=None):
    if pinned is None:
        fixture = (FIXTURES / "page_scale.cc.in").read_text()
        pinned = {path: fixture for path in PINNED_METHODS}
    functions = "\n".join(methods(pinned[path], names)
                          for path, names in PINNED_METHODS.items())
    functions += methods(renderer, ("bool RenderFrameImpl::ShouldUseUserAgentOverride() const",))
    prepare, commit = scale_blocks(renderer)
    public, private = document_members(state)
    program = (FIXTURES / "ScaleFlow.cc.in").read_text()
    substitutions = {
        "@PRODUCTION_METHODS@": functions,
        "@PREPARE_SCALE@": "" if legacy else prepare,
        "@COMMIT_SCALE@": "" if legacy else commit,
        "@STATE_PUBLIC@": public,
        "@STATE_PRIVATE@": private,
    }
    for token, value in substitutions.items():
        program = program.replace(token, value)
    source = directory / "scale.cc"
    source.write_text(program)
    binary = directory / "scale"
    result = subprocess.run([
        "c++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", str(source), "-o", str(binary),
    ], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stdout + result.stderr)
    return binary


def run_scale(binary, scenario):
    return subprocess.run([str(binary), scenario], capture_output=True, text=True)


def verify_upstream(src):
    """Check real pinned call order and execute the real reset/restore methods."""
    import apply_user_agent as patch
    renderer = (src / patch.RENDER_FRAME).read_text()
    state = (src / patch.DOCUMENT_STATE).read_text()
    fixture = (FIXTURES / "page_scale.cc.in").read_text()
    pinned = {path: (src / path).read_text() for path in SCALE_API_PATHS}
    for path, names in PINNED_METHODS.items():
        if methods(pinned[path], names) != methods(fixture, names):
            raise ValueError(f"Pinned scale reset/restoration changed: {path}")
    if methods(renderer, ("bool RenderFrameImpl::ShouldUseUserAgentOverride() const",)) != methods(
            fixture, ("bool RenderFrameImpl::ShouldUseUserAgentOverride() const",)):
        raise ValueError("Review how the still-committed document's UA mode is read")
    prepare, commit = scale_blocks(renderer)
    prepare_method = methods(renderer, ("void RenderFrameImpl::CommitNavigationWithParams(",))
    commit_method = methods(renderer, ("void RenderFrameImpl::DidCommitNavigation(",))
    if not (prepare_method.index(prepare) < prepare_method.index(
            "frame_->CommitNavigation(std::move(navigation_params)")):
        raise ValueError("UA mode must be read before the old document is swapped out")
    if not (commit_method.index("DocumentState::FromDocumentLoader(document_loader)")
            < commit_method.index(commit) < commit_method.index(
                "observer.ReadyToCommitNavigation(document_loader)")):
        raise ValueError("Scale reset must target the newly committed document")
    frame_commit = methods(pinned[FRAME_LOADER], ("void FrameLoader::CommitDocumentLoader(",))
    if frame_commit.index("SetHistoryItemStateForCommit(") > frame_commit.index(
            "document_loader_->CommitNavigation();"):
        raise ValueError("HistoryItem must be installed before the commit callback")
    navigation = methods(pinned[FRAME_LOADER], ("void FrameLoader::CommitNavigation(",))
    if navigation.index("  CommitDocumentLoader(") > navigation.index(
            "  RestoreScrollPositionAndViewState();"):
        raise ValueError("Scale reset must run before history restoration")
    if "web_frame_->Client()->DidCommitNavigation(" not in methods(
            pinned[LOCAL_FRAME_CLIENT], ("void LocalFrameClientImpl::DispatchDidCommitLoad(",)):
        raise ValueError("Blink no longer calls the renderer at document commit")
    if "GetLocalFrameClient().DispatchDidCommitLoad(" not in methods(
            pinned[DOCUMENT_LOADER], ("void DocumentLoader::CommitNavigation()",)):
        raise ValueError("Review the document commit callback before scale restoration")
    with tempfile.TemporaryDirectory(prefix="argon-ua-scale-") as temporary:
        binary = compile_scale(Path(temporary), renderer, state, pinned=pinned)
        for scenario in SCENARIOS:
            result = run_scale(binary, scenario)
            if result.returncode:
                raise ValueError(f"{scenario}: {result.stdout}{result.stderr}")
    return "PASS: UA-mode scale reset, unchanged reload/history zoom and one-shot commit state"
