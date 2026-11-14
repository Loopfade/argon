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
