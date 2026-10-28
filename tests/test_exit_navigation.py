"""Exercise production navigation cancellation before the fatal RFH allocation path."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_exit import exit_patch

MANAGER = Path('content/browser/renderer_host/render_frame_host_manager')
REQUEST = Path('content/browser/renderer_host/navigation_request.cc')
PROCESS = Path('content/browser/renderer_host/render_process_host_impl.cc')
SIGNATURE = '''RenderFrameHostManager::GetFrameHostForNavigation(
    NavigationRequest* request,
    BrowsingContextGroupSwap* browsing_context_group_swap,
    const ProcessAllocationContext& process_allocation_context,
    std::string* reason) {'''
INIT = '''bool RenderProcessHostImpl::Init() {
  // calling Init() more than once does nothing, this makes it more convenient
  // for the view host which may not be sure in some cases
  if (IsInitializedAndNotDead())
    return true;'''


def verify_navigation_gate(src):
    header = (src / MANAGER.with_suffix('.h')).read_text()
    manager = (src / MANAGER.with_suffix('.cc')).read_text()
    request = (src / REQUEST).read_text()
    process = (src / PROCESS).read_text()
    enum = re.search(r'enum class GetFrameHostForNavigationFailed \{[^}]+\};', header)[0]
    guard = re.search(r'  if \(!GetContentClient\(\)->browser\(\)->IsRendererProcessLaunchAllowed\(\)\) \{\n'
                      r'    return base::unexpected\(\n'
                      r'        GetFrameHostForNavigationFailed::kBlockedByEmbedder\);\n  \}', manager)
    if guard is None or not manager.split(SIGNATURE, 1)[1].lstrip().startswith(guard[0].lstrip()):
        raise ValueError('Navigation gate must precede speculative RFH allocation')
    cases = re.findall(r'case GetFrameHostForNavigationFailed::kBlockedByEmbedder:\n'
                       r' +frame_tree_node_->ResetNavigationRequest\(\n'
                       r' +NavigationDiscardReason::kInternalCancellation\);\n +return;', request)
    if len(cases) != 3 or request.count('case GetFrameHostForNavigationFailed::kBlockedByEmbedder:') != 3:
        raise ValueError('All three owning navigation paths must cancel and return')
    init = re.search(re.escape(INIT) + r'\n  if \(!GetContentClient\(\)->browser\(\)->'
                     r'IsRendererProcessLaunchAllowed\(\)\)\n    return false;', process)
    if init is None:
        raise ValueError('Renderer launch gate must preserve idempotent Init')

    fixture = '''#include <cassert>
struct Browser { bool allowed=true; bool IsRendererProcessLaunchAllowed() {return allowed;} } browser;
struct Client { Browser* browser() {return &::browser;} } client;
Client* GetContentClient() {return &client;}
''' + enum + '''
namespace base {
struct Unexpected { GetFrameHostForNavigationFailed error; };
Unexpected unexpected(GetFrameHostForNavigationFailed value) {return {value};}
}
struct Result {
  bool blocked; GetFrameHostForNavigationFailed error;
  Result(int) : blocked(false),error(GetFrameHostForNavigationFailed::kBlockedByPendingCommit) {}
  Result(base::Unexpected value) : blocked(true),error(value.error) {}
};
int allocations=0,launches=0,commits=0;
Result Select() {
''' + guard[0] + '''
  ++allocations; return Result(1);
}
class RenderProcessHostImpl {
 public:
  bool initialized=false;
  bool IsInitializedAndNotDead() {return initialized;}
  bool Init();
};
''' + init[0] + '''
  ++launches; initialized=true; return true;
}
enum class NavigationDiscardReason {kInternalCancellation};
struct Node {
  bool alive=true; int cancelled=0;
  void ResetNavigationRequest(NavigationDiscardReason) {alive=false;++cancelled;}
};
struct Request {
  Node* frame_tree_node_;
'''
    for i, case in enumerate(cases):
        fixture += f'  void Run{i}(Result result) {{\n' + '''
    if (result.blocked) {
      switch(result.error) {
''' + case + '''
        default: break;
      }
    }
    assert(frame_tree_node_->alive); ++commits;
  }
'''
    fixture += '''};
int main() {
  RenderProcessHostImpl live,fresh;
  live.initialized=true;
  browser.allowed=false;
  assert(live.Init()); assert(!fresh.Init()); assert(launches==0);
  Node node; Request request{&node};
'''
    for i in range(3):
        fixture += f'  node.alive=true;request.Run{i}(Select());\n'
    fixture += '''
  assert(node.cancelled==3 && !node.alive && allocations==0 && commits==0);
  browser.allowed=true;
  assert(fresh.Init() && fresh.Init() && launches==1);
  node.alive=true;
'''
    for i in range(3):
        fixture += f'  request.Run{i}(Select());\n'
    fixture += '''  assert(allocations==3 && commits==3 && node.cancelled==3);
}
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, binary = root / 'navigation.cc', root / 'navigation'
        source.write_text(fixture)
        result = subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                 str(source), '-o', str(binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    return 'PASS: navigation cancellation, request ownership and renderer Init idempotence'


class ExitNavigationTests(unittest.TestCase):
    def source_tree(self, src):
        sources = {
            MANAGER.with_suffix('.h'): '''enum class GetFrameHostForNavigationFailed {
  kCouldNotReinitializeMainFrame,
  kBlockedByPendingCommit,
};''',
            MANAGER.with_suffix('.cc'): SIGNATURE,
            REQUEST: ('      case GetFrameHostForNavigationFailed::kCouldNotReinitializeMainFrame:\n' * 2
                      + '        case GetFrameHostForNavigationFailed::kCouldNotReinitializeMainFrame:\n'),
            PROCESS: INIT,
        }
        for path, source in sources.items():
            transform = exit_patch.TRANSFORMS[path]
            value = transform(source)
            self.assertEqual(transform(value), value)
            target = src / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
    def test_blocked_navigation_cancels_all_owner_paths_before_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_tree(root)
            self.assertIn('PASS:', verify_navigation_gate(root))

    def test_missing_cancellation_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_tree(root)
            request = root / REQUEST
            request.write_text(request.read_text().replace('kBlockedByEmbedder:', 'kMissingCase:', 1))
            with self.assertRaisesRegex(ValueError, 'All three'):
                verify_navigation_gate(root)
