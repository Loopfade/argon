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
    def test_native_pair_updates_without_rewriting_startup_arguments(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/apply_user_agent.py").read_text()
        self.assertIn("PASS:", verify_native_state(script))

    def test_chrome_ua_update_uses_public_content_api(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/apply_user_agent.py").read_text()
        self.assertNotIn('"//content/common:mojo_bindings",', script)
        self.assertIn("renderer->UpdateArgonUserAgent(", script)
        self.assertIn("GetRendererInterface()->UpdateUserAgent(", script)
        self.assertIn('content/common/renderer.mojom.h', script)
        self.assertIn("RENDER_PROCESS_HOST_HEADER", script)
        self.assertIn("RENDER_PROCESS_HOST_IMPL", script)

    def test_update_keeps_security_initialization_out_of_the_runtime_handler(self):
        script = (Path(__file__).resolve().parents[1] / "scripts/apply_user_agent.py").read_text()
        match = re.search(r"void RenderThreadImpl::UpdateUserAgent\([\s\S]*?\n}\n", script)
        self.assertIsNotNone(match)
        self.assertIn("PASS:", verify_renderer_update(match[0]))


def verify_native_state(source):
    match = re.search(r"namespace \{\nstruct ArgonUserAgentState[\s\S]*?"
                      r"bool SetArgonUserAgentOverrides\([\s\S]*?\n}\n", source)
    if not match:
        raise ValueError("Missing native UA state")
    program = r'''
#include <cassert>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#define GUARDED_BY(x)
namespace base {
struct Lock { std::mutex mutex; void lock(){mutex.lock();} void unlock(){mutex.unlock();} };
using AutoLock=std::lock_guard<Lock>;
template<typename T> struct NoDestructor { T value; T& operator*(){return value;} };
struct CommandLine {
  static CommandLine* ForCurrentProcess() { static CommandLine value; return &value; }
  std::string GetSwitchValueASCII(const std::string& key) {
    return key=="argon-user-agent" ? "mobile/start" : "desktop/start";
  }
};
}
namespace net { struct HttpUtil {
  static bool IsValidHeaderValue(const std::string& value) {
    return value.find('\r')==std::string::npos && value.find('\n')==std::string::npos;
  }
}; }
'''
    program += match[0]
    program += r'''
int main() {
  assert(GetArgonUserAgentOverride(false)=="mobile/start");
  assert(GetArgonUserAgentOverride(true)=="desktop/start");
  assert(SetArgonUserAgentOverrides("mobile/new","desktop/new"));
  assert(GetArgonUserAgentOverride(false)=="mobile/new");
  assert(GetArgonUserAgentOverride(true)=="desktop/new");
  assert(!SetArgonUserAgentOverrides("bad\r\nheader","desktop/bad"));
  assert(!SetArgonUserAgentOverrides("mobile/bad",""));
  assert(GetArgonUserAgentOverride(false)=="mobile/new");
  std::thread writer([] {
    for(int i=0;i<10000;i++)
      assert(SetArgonUserAgentOverrides("mobile/"+std::to_string(i),"desktop/"+std::to_string(i)));
  });
  for(int i=0;i<10000;i++) {
    assert(GetArgonUserAgentOverride(false)->starts_with("mobile/"));
    assert(GetArgonUserAgentOverride(true)->starts_with("desktop/"));
  }
  writer.join();
  assert(GetArgonUserAgentOverride(false)=="mobile/9999");
  assert(GetArgonUserAgentOverride(true)=="desktop/9999");
  assert(base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII("argon-user-agent")=="mobile/start");
}
'''
    with tempfile.TemporaryDirectory(prefix="argon-ua-state-") as directory:
        root = Path(directory)
        path = root / "state.cc"
        path.write_text(program)
        result = subprocess.run(["c++", "-std=c++20", "-pthread", "-Wall", "-Wextra", "-Werror",
                                 str(path), "-o", str(root / "state")], text=True, capture_output=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        subprocess.run([str(root / "state")], check=True)
    return "PASS: locked native UA pair, invalid headers and unchanged startup arguments"
