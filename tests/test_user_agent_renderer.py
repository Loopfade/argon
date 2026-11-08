"""Compile the renderer UA update while retaining initialized security state."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


def verify_renderer_update(source):
    match = re.search(r"void RenderThreadImpl::UpdateUserAgent\([\s\S]*?\n}\n", source)
    if not match:
        raise ValueError("Missing production renderer UA update")
    program = r'''
#include <cassert>
#include <functional>
#include <string>
#include <utility>
#include <vector>
namespace blink { struct UserAgentMetadata { bool mobile; }; }
struct WebString {
  std::string value;
  static WebString FromUtf8(const std::string& value) { return {value}; }
};
struct RendererClient {
  std::string notified;
  void DidSetUserAgent(const std::string& value) { notified=value; }
};
struct Client {
  RendererClient renderer_client;
  RendererClient* renderer() { return &renderer_client; }
} client;
Client* GetContentClient() { return &client; }
struct RenderThreadImpl {
  struct UpdateUserAgentCallback {
    std::function<void()> function;
    template<typename F> UpdateUserAgentCallback(F f) : function(std::move(f)) {}
    void Run() { function(); }
  };
  WebString user_agent_={"old"};
  blink::UserAgentMetadata user_agent_metadata_={false};
  std::vector<std::string> cors_exempt_header_list_={"do-not-reset"};
  int cpu_performance_tier_=7;
  bool origin_trials_initialized=true;
  void UpdateUserAgent(const std::string&, const blink::UserAgentMetadata&,
                       UpdateUserAgentCallback);
};
'''
    program += match[0]
    program += r'''
int main() {
  RenderThreadImpl renderer;
  int replies=0;
  renderer.UpdateUserAgent("new mobile UA", {true}, [&] {
    ++replies;
    assert(renderer.user_agent_.value=="new mobile UA");
    assert(renderer.user_agent_metadata_.mobile);
  });
  assert(replies==1);
  assert(client.renderer_client.notified=="new mobile UA");
  assert(renderer.cors_exempt_header_list_==std::vector<std::string>{"do-not-reset"});
  assert(renderer.cpu_performance_tier_==7 && renderer.origin_trials_initialized);
  renderer.UpdateUserAgent("second UA", {false}, [&] { ++replies; });
  assert(replies==2 && renderer.user_agent_.value=="second UA");
  assert(!renderer.user_agent_metadata_.mobile);
}
'''
    with tempfile.TemporaryDirectory(prefix="argon-ua-renderer-") as directory:
        root = Path(directory)
        path = root / "flow.cc"
        path.write_text(program)
        result = subprocess.run(["c++", "-std=c++20", "-Wall", "-Wextra", "-Werror", str(path),
                                 "-o", str(root / "flow")], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        subprocess.run([str(root / "flow")], check=True)
    return "PASS: production UA cache update, acknowledgement and preserved renderer security state"


class UserAgentRendererTests(unittest.TestCase):
    def test_update_keeps_security_initialization_out_of_the_runtime_handler(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/apply_user_agent.py").read_text()
        match = re.search(r"void RenderThreadImpl::UpdateUserAgent\([\s\S]*?\n}\n", script)
        self.assertIsNotNone(match)
        self.assertIn("PASS:", verify_renderer_update(match[0]))
