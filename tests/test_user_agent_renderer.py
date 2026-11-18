"""Compile production UA parsing and UA-CH gates; the process state is immutable."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import apply_user_agent as patch
from user_agent_navigation_flow import methods

VIEWPORT_IMPL=Path('content/browser/web_contents/web_contents_impl.cc')
VIEWPORT_FIXTURE=ROOT/'tests/fixtures/user_agent/web_contents_viewport.cc.in'


def compile_and_run(program):
    with tempfile.TemporaryDirectory(prefix='argon-ua-native-') as directory:
        root=Path(directory)
        source=root/'flow.cc'
        source.write_text(program)
        result=subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror',str(source),'-o',str(root/'flow')],capture_output=True,text=True)
        if result.returncode:
            raise ValueError(result.stdout+result.stderr)
        subprocess.run([str(root/'flow')],check=True)


PRELUDE=r'''
#include <cassert>
#include <map>
#include <optional>
#include <string>
namespace base {
struct CommandLine {
  std::map<std::string,std::string> values;
  static CommandLine* ForCurrentProcess() { static CommandLine c; return &c; }
  std::string GetSwitchValueASCII(const std::string& key) const {
    auto it=values.find(key);return it==values.end() ? "" : it->second;
  }
  bool HasSwitch(const std::string& key) const { return values.contains(key); }
};
}
namespace net { struct HttpUtil {
  static bool IsValidHeaderValue(const std::string& value) {
    return value.find_first_of(std::string("\r\n\0",3))==std::string::npos;
  }
}; }
'''


def verify_native_state(source):
    match=re.search(r'std::optional<std::string> GetArgonUserAgentOverride\(bool desktop\) \{[\s\S]*?\n}\n',source)
    if not match:
        raise ValueError('Missing production startup UA override')
    compile_and_run(PRELUDE+match[0]+r'''
int main() {
  auto& args=base::CommandLine::ForCurrentProcess()->values;
  // Absence of overrides preserves native UA and metadata for both layouts.
  assert(!GetArgonUserAgentOverride(false) && !GetArgonUserAgentOverride(true));
  args["argon-user-agent"]="Firefox Mobile";
  args["argon-user-agent-desktop"]="Firefox Desktop";
  assert(GetArgonUserAgentOverride(false)=="Firefox Mobile");
  assert(GetArgonUserAgentOverride(true)=="Firefox Desktop");
  args["argon-user-agent"]="Custom/1.0";
  args["argon-user-agent-desktop"]="Custom/1.0";
  assert(GetArgonUserAgentOverride(false)==GetArgonUserAgentOverride(true));
  args["argon-user-agent"]="bad\r\nInjected: value";
  assert(!GetArgonUserAgentOverride(false));
  args["argon-user-agent"]="";
  assert(!GetArgonUserAgentOverride(false));
}
''')
    return 'PASS: native UA defaults, mobile/desktop counterparts, custom UA and invalid headers'


def verify_client_hints(navigation, frame, host):
    nav=re.search(r'  disable_due_to_custom_ua \|=\s*base::CommandLine::ForCurrentProcess\(\)->HasSwitch\(\s*"argon-user-agent-hide-client-hints"\);',navigation)
    renderer=re.search(r'  std::optional<UserAgentMetadata> ua = GetUserAgentMetadata\(\);\s*if \(base::CommandLine::ForCurrentProcess\(\)->HasSwitch\(\s*"argon-user-agent-hide-client-hints"\)\) \{\s*ua.reset\(\);\s*}',frame)
    if not nav or not renderer:
        raise ValueError('Missing navigation or subresource UA-CH gate')
    if not re.search(r'"argon-user-agent-hide-client-hints",[\s\\n\'"+]*.*embedder_support::kUseMobileUserAgent',host):
        raise ValueError('The browser must forward the UA-CH flag to every renderer')
    compile_and_run(PRELUDE+'''
struct UserAgentMetadata { std::string platform="Android"; };
std::optional<UserAgentMetadata> GetUserAgentMetadata() { return UserAgentMetadata(); }
bool NavigationSendsUAHints(bool disable_due_to_custom_ua) {
'''+nav[0]+'''
  return !disable_due_to_custom_ua;
}
std::optional<UserAgentMetadata> SubresourceUAHints() {
'''+renderer[0]+'''
  return ua;
}
int main() {
  auto& args=base::CommandLine::ForCurrentProcess()->values;
  assert(NavigationSendsUAHints(false));
  assert(!NavigationSendsUAHints(true));
  assert(SubresourceUAHints()->platform=="Android");
  args["argon-user-agent-hide-client-hints"]="";
  assert(!NavigationSendsUAHints(false));
  assert(!NavigationSendsUAHints(true));
  assert(!SubresourceUAHints());
}
''')
    return 'PASS: native UA-CH defaults and suppression for navigation and subresources'


def viewport_policy(source):
    marker='#if BUILDFLAG(IS_ANDROID)\n  bool is_request_android_desktop_site = false;\n#endif\n'
    if source.count(marker)!=1:
        raise ValueError('Review the pinned Android desktop viewport policy')
    start=source.index(marker)
    end=source.index('\n  }\n',start)+5
    return source[start:end]


def verify_viewport(desktop, ua_utils, web_contents):
    policy=viewport_policy(web_contents)
    if policy!=viewport_policy(VIEWPORT_FIXTURE.read_text()):
        raise ValueError('Chromium desktop viewport policy changed; review the fixture')
    native=re.search(r'std::optional<std::string> GetArgonUserAgentOverride\(bool desktop\) \{[\s\S]*?\n}\n',ua_utils)
    override=re.search(r'  if \(auto ua = embedder_support::GetArgonUserAgentOverride\(/\*desktop=\*/true\)\) \{[\s\S]*?    return;\n  }\n',desktop)
    if not native or not override:
        raise ValueError('Missing production desktop preset override')
    # Compile the actual preset branch against Chromium's actual viewport
    # decision. The adapters capture JNI transport, not a browser layout engine.
    compile_and_run(PRELUDE+r'''
#include <iostream>
#include <vector>
#define BUILDFLAG(name) name
#define IS_ANDROID 1
#define CHECK_VIEWPORT(value) do { if (!(value)) { std::cerr << #value << "\n"; return 1; } } while (false)
namespace embedder_support {
'''+native[0]+r'''
}
namespace blink {
struct UserAgentMetadata {
  bool mobile=false;
  std::vector<std::string> brand_version_list, brand_full_version_list;
  std::string platform, platform_version, model, architecture, bitness, full_version;
};
struct UserAgentOverride {
  std::string ua_string_override;
  std::optional<UserAgentMetadata> ua_metadata_override;
  static UserAgentOverride UserAgentOnly(const std::string& ua) {
    UserAgentOverride result; result.ua_string_override=ua; return result;
  }
};
}
namespace content {
struct WebContents {
  struct { blink::UserAgentOverride user_agent_override; } renderer_preferences_;
  bool overriding=false, has_entry=true, override_in_new_tabs=false;
  static WebContents* FromJavaWebContents(WebContents* value) { return value; }
  void SetUserAgentOverride(const blink::UserAgentOverride& ua, bool new_tabs) {
    renderer_preferences_.user_agent_override=ua; override_in_new_tabs=new_tabs;
  }
  WebContents& GetController() { return *this; }
  WebContents* GetVisibleEntry() { return has_entry ? this : nullptr; }
  bool GetIsOverridingUserAgent() const { return overriding; }
  bool ViewportMetaEnabled() {
    struct { bool viewport_meta_enabled=true; } prefs;
'''+policy+r'''
    return prefs.viewport_meta_enabled;
  }
};
}
void SetDesktopPreset(content::WebContents* jweb_contents, bool j_override_in_new_tabs) {
'''+override[0]+r'''
}
int main() {
  auto& args=base::CommandLine::ForCurrentProcess()->values;
  content::WebContents native;
  native.renderer_preferences_.user_agent_override.ua_string_override="native";
  SetDesktopPreset(&native,true);
  CHECK_VIEWPORT(native.renderer_preferences_.user_agent_override.ua_string_override=="native");
  for (const std::string ua : {"Firefox/154.0", "Chrome/152 YaBrowser/26.10",
                               "Custom/1.0 Mobile", "Custom/1.0"}) {
    args["argon-user-agent-desktop"]=ua;
    content::WebContents page;
    SetDesktopPreset(&page,true);
    const auto& selected=page.renderer_preferences_.user_agent_override;
    CHECK_VIEWPORT(selected.ua_string_override==ua && page.override_in_new_tabs);
    for (bool desktop : {false,true,false,true,false}) {
      page.overriding=desktop;
      CHECK_VIEWPORT(page.ViewportMetaEnabled()==!desktop);
    }
    CHECK_VIEWPORT(selected.ua_metadata_override.has_value());
    const auto& metadata=*selected.ua_metadata_override;
    CHECK_VIEWPORT(!metadata.mobile);
    CHECK_VIEWPORT(metadata.brand_version_list.empty() && metadata.brand_full_version_list.empty());
    CHECK_VIEWPORT(metadata.platform.empty() && metadata.full_version.empty() && metadata.model.empty());
    page.has_entry=false;page.overriding=true;
    CHECK_VIEWPORT(page.ViewportMetaEnabled());
  }
}
''')
    return 'PASS: preset desktop viewport, mobile restore, custom UA and native fallback'


def patched_fixture():
    sources = {
        patch.ANDROID_GN: f'      "{patch.BASE}/ApplicationInitHook.java",',
        patch.STARTUP: '            PathUtils.setPrivateDataDirectorySuffix(PRIVATE_DATA_DIRECTORY_SUFFIX);',
        patch.PRIVACY_HOOKS: '        SettingsUtils.addPreferencesFromResource(prefFragment, R.xml.privacy_preferences_ext);',
        patch.UA_HEADER: 'std::optional<std::string> GetUserAgentFromCommandLine();',
        patch.UA_UTILS: 'std::string GetUserAgent() {\n}\nblink::UserAgentMetadata GetUserAgentMetadata(bool only_low_entropy_ch) {\n}',
        patch.DESKTOP_UA: '  constexpr char kLinuxInfoStr[]',
        patch.TITANIUM_CHROME_JAVA_SOURCES: '  "java/src/org/chromium/chrome/browser/settings/search/SearchIndexProviderHooks.java",',
        patch.TITANIUM_CHROME_RESOURCES: '  "java/res/xml/privacy_preferences_ext.xml",',
        patch.TITANIUM_PRIVACY_PREFERENCES: '<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
            '        android:key="argon_certificates" />\n</PreferenceScreen>',
        patch.TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS: '            indexData.removeEntryForKey(fragmentName, "preload_pages");',
        patch.RENDER_PROCESS_HOST_IMPL: '      embedder_support::kUseMobileUserAgent,',
        patch.CLIENT_HINTS: '  if (!disable_due_to_custom_ua) {\n}',
        patch.FRAME_FETCH: '#include "base/feature_list.h"\n  std::optional<UserAgentMetadata> ua = GetUserAgentMetadata();',
        patch.NAVIGATION_CONTROLLER: (ROOT/'tests/fixtures/user_agent/navigation_controller_android.cc.in').read_text(),
        patch.RENDER_FRAME: 'void RenderFrameImpl::CommitNavigationWithParams() {\n'
            '  navigation_params->frame_load_type = load_type;\n}\n'
            'void RenderFrameImpl::DidCommitNavigation() {\n'
            '  DCHECK(!navigation_state->WasWithinSameDocument());\n}\n' + methods(
                (ROOT/'tests/fixtures/user_agent/page_scale.cc.in').read_text(),
                ('bool RenderFrameImpl::ShouldUseUserAgentOverride() const',)),
        patch.DOCUMENT_STATE: 'class DocumentState {\n'
            '  bool is_overriding_user_agent() const { return is_overriding_user_agent_; }\n'
            '  void set_is_overriding_user_agent(bool state) {\n'
            '    is_overriding_user_agent_ = state;\n  }\n'
            '  // This is a fake navigation request id,\n'
            '  bool is_overriding_user_agent_ = false;\n};\n',
    }
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        for path,content in sources.items():
            target=root/path
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content)
        patch.apply(root)
        result={p:(root/p).read_text() for p in sources}
        patch.apply(root)
        assert result=={p:(root/p).read_text() for p in sources}, 'UA patch must be idempotent'
        return result


class UserAgentNativeTests(unittest.TestCase):
    def test_native_startup_values(self):
        self.assertIn('PASS:',verify_native_state((ROOT/'scripts/apply_user_agent.py').read_text()))

    def test_native_client_hints(self):
        sources=patched_fixture()
        self.assertIn('PASS:',verify_client_hints(sources[patch.CLIENT_HINTS],sources[patch.FRAME_FETCH],sources[patch.RENDER_PROCESS_HOST_IMPL]))

    def test_presets_switch_viewport_and_restore_mobile_layout(self):
        sources=patched_fixture()
        self.assertIn('PASS:',verify_viewport(sources[patch.DESKTOP_UA],sources[patch.UA_UTILS],VIEWPORT_FIXTURE.read_text()))

    def test_no_live_mutation_or_site_preference_rewrite(self):
        script=(ROOT/'scripts/apply_user_agent.py').read_text()
        self.assertNotIn('UpdateArgonUserAgent(',script)
        self.assertNotIn('BarrierClosure(',script)
        self.assertFalse(any(p.name=='TabImpl.java' for p in patch.INPUT_PATHS))
        self.assertFalse((ROOT/'chromium_overlay/titanium/chromium_src/chrome/android/java/src/org/chromium/chrome/browser/base/ArgonUserAgentController.java').exists())

    def test_reject_stale_prepared_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for path in patch.INPUT_PATHS:
                target=root/path
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text('')
            (root/patch.UA_UTILS).write_text('bool SetArgonUserAgentOverrides();')
            with self.assertRaisesRegex(ValueError,'fresh pinned Chromium'):
                patch.apply(root)
