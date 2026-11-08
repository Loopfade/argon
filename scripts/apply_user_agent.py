#!/usr/bin/env python3
"""Wire Argon's browser-wide User-Agent preference into Chromium for Android."""

import argparse
from pathlib import Path

from apply_scoped_ca import (
    TITANIUM_CHROME_JAVA_SOURCES,
    TITANIUM_CHROME_RESOURCES,
    TITANIUM_PRIVACY_PREFERENCES,
    TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS,
    replace_once,
)

ANDROID = Path("chrome/android")
ANDROID_GN = ANDROID / "BUILD.gn"
BASE = Path("java/src/org/chromium/chrome/browser/base")
STARTUP = ANDROID / BASE / "SplitCompatApplication.java"
PRIVACY = Path("java/src/org/chromium/chrome/browser/privacy/settings")
PRIVACY_HOOKS = TITANIUM_CHROME_JAVA_SOURCES.parent / PRIVACY / "PrivacySettingsExt.java"
UA_UTILS = Path("components/embedder_support/user_agent_utils.cc")
UA_HEADER = UA_UTILS.with_suffix(".h")
DESKTOP_UA = Path("chrome/browser/android/content/content_utils.cc")
ANDROID_NATIVE_GN = Path("chrome/browser/android/BUILD.gn")
CONTENT_JAVA = Path("chrome/browser/android/content/java/src/org/chromium/chrome/browser/content/ContentUtils.java")
TAB_IMPL = Path("chrome/android/java/src/org/chromium/chrome/browser/tab/TabImpl.java")
RENDERER_MOJOM = Path("content/common/renderer.mojom")
RENDER_THREAD_HEADER = Path("content/renderer/render_thread_impl.h")
RENDER_THREAD = RENDER_THREAD_HEADER.with_suffix(".cc")
RENDER_PROCESS_HOST_HEADER = Path("content/public/browser/render_process_host.h")
RENDER_PROCESS_HOST_IMPL = Path("content/browser/renderer_host/render_process_host_impl.cc")

INPUT_PATHS = (
    ANDROID_GN, STARTUP, PRIVACY_HOOKS, UA_UTILS, UA_HEADER, DESKTOP_UA,
    TITANIUM_CHROME_JAVA_SOURCES, TITANIUM_CHROME_RESOURCES,
    TITANIUM_PRIVACY_PREFERENCES, TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS,
    CONTENT_JAVA, TAB_IMPL, RENDERER_MOJOM, RENDER_THREAD_HEADER, RENDER_THREAD,
    ANDROID_NATIVE_GN, RENDER_PROCESS_HOST_HEADER, RENDER_PROCESS_HOST_IMPL,
)


def apply(src: Path) -> None:
    sources = {path: (src / path).read_text() for path in INPUT_PATHS}

    def replace(path, anchor, replacement):
        try:
            sources[path] = replace_once(sources[path], anchor, replacement)
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from error

    anchor = f'      "{BASE}/ApplicationInitHook.java",'
    # Keep the startup model independent of Tab/Profile and the isolated Chrome
    # feature. The live controller belongs only to the full-browser source set.
    replace(ANDROID_GN, anchor, anchor + f'\n      "{BASE}/ArgonUserAgent.java",')
    anchor = "            PathUtils.setPrivateDataDirectorySuffix(PRIVATE_DATA_DIRECTORY_SUFFIX);"
    replace(STARTUP, anchor, "            ArgonUserAgent.applySavedOverride();\n" + anchor)

    # The theme patch prepends to PrivacySettingsExt. Use a separate entry so
    # both registrations retain their anchors during repeated preparation.
    anchor = '  "java/src/org/chromium/chrome/browser/settings/search/SearchIndexProviderHooks.java",'
    replace(TITANIUM_CHROME_JAVA_SOURCES, anchor,
            anchor + f'\n  "{PRIVACY}/ArgonUserAgentPreference.java",'
            + f'\n  "{BASE}/ArgonUserAgentController.java",')
    anchor = '  "java/res/xml/privacy_preferences_ext.xml",'
    replace(TITANIUM_CHROME_RESOURCES, anchor,
            '  "java/res/values/argon_user_agent_strings.xml",\n'
            '  "java/res/values-ru/argon_user_agent_strings.xml",\n' + anchor)
    anchor = '        android:key="argon_certificates"'
    anchor = "    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n" + anchor
    replace(TITANIUM_PRIVACY_PREFERENCES, anchor, """\
    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_user_agent"
        android:persistent="false"
        android:title="@string/argon_user_agent_title" />
""" + anchor)
    anchor = "        SettingsUtils.addPreferencesFromResource(prefFragment, R.xml.privacy_preferences_ext);"
    replace(PRIVACY_HOOKS, anchor, anchor + "\n        ArgonUserAgentPreference.bind(prefFragment);")
    anchor = '            indexData.removeEntryForKey(fragmentName, "preload_pages");'
    replace(TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS, anchor, """\
            indexData.addEntryForKey(
                    fragmentName,
                    "argon_user_agent",
                    R.string.argon_user_agent_title,
                    R.string.argon_user_agent_search_summary);
""" + anchor)

    anchor = "std::optional<std::string> GetUserAgentFromCommandLine();"
    replace(UA_HEADER, anchor, anchor + "\n\n"
            "// Returns the current mobile or desktop override from Argon's settings.\n"
            "std::optional<std::string> GetArgonUserAgentOverride(bool desktop = false);\n"
            "// Atomically update the pair without mutating the process command line.\n"
            "bool SetArgonUserAgentOverrides(const std::string& mobile,\n"
            "                               const std::string& desktop);")
    include_anchor = '#include "base/command_line.h"'
    for header in ("base/no_destructor.h", "base/synchronization/lock.h", "base/thread_annotations.h"):
        include = f'#include "{header}"'
        if include not in sources[UA_UTILS]:
            replace(UA_UTILS, include_anchor, include_anchor + "\n" + include)
    anchor = "std::string GetUserAgent() {"
    replace(UA_UTILS, anchor, """\
namespace {
struct ArgonUserAgentState {
  ArgonUserAgentState()
      : mobile(base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII(
            "argon-user-agent")),
        desktop(base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII(
            "argon-user-agent-desktop")) {}
  base::Lock lock;
  std::string mobile GUARDED_BY(lock);
  std::string desktop GUARDED_BY(lock);
};

ArgonUserAgentState& GetArgonUserAgentState() {
  static base::NoDestructor<ArgonUserAgentState> state;
  return *state;
}
}  // namespace

std::optional<std::string> GetArgonUserAgentOverride(bool desktop) {
  auto& state = GetArgonUserAgentState();
  base::AutoLock guard(state.lock);
  const std::string& ua = desktop ? state.desktop : state.mobile;
  if (ua.empty() || !net::HttpUtil::IsValidHeaderValue(ua)) {
    return std::nullopt;
  }
  return ua;
}

bool SetArgonUserAgentOverrides(const std::string& mobile,
                               const std::string& desktop) {
  if (mobile.empty() || desktop.empty() ||
      !net::HttpUtil::IsValidHeaderValue(mobile) ||
      !net::HttpUtil::IsValidHeaderValue(desktop)) {
    return false;
  }
  auto& state = GetArgonUserAgentState();
  base::AutoLock guard(state.lock);
  state.mobile = mobile;
  state.desktop = desktop;
  return true;
}

""" + anchor + """
  if (auto ua = GetArgonUserAgentOverride()) {
    return *ua;
  }
""")
    anchor = "blink::UserAgentMetadata GetUserAgentMetadata(bool only_low_entropy_ch) {"
    replace(UA_UTILS, anchor, anchor + """
  if (auto ua = GetArgonUserAgentOverride()) {
    // Do not expose the default Chromium brand or platform for a custom UA.
    blink::UserAgentMetadata metadata;
    metadata.mobile = ua->find("Mobile") != std::string::npos;
    return metadata;
  }
""")
    anchor = "  constexpr char kLinuxInfoStr[]"
    replace(DESKTOP_UA, anchor, """\
  // The normal navigation flag still controls desktop layout and menu state.
  // Only the desktop counterpart belongs in WebContents' desktop override.
  if (auto ua = embedder_support::GetArgonUserAgentOverride(/*desktop=*/true)) {
    auto* contents = content::WebContents::FromJavaWebContents(jweb_contents);
    if (contents->GetUserAgentOverride().ua_string_override != *ua) {
      contents->GetController().GetBackForwardCache().Flush();
    }
    contents->SetUserAgentOverride(blink::UserAgentOverride::UserAgentOnly(*ua),
                                   j_override_in_new_tabs);
    return;
  }

""" + anchor)

    anchor = '#include "base/android/jni_string.h"'
    replace(DESKTOP_UA, anchor, """\
#include <vector>

#include "base/android/callback_android.h"
#include "base/android/scoped_java_ref.h"
#include "base/barrier_closure.h"
#include "base/functional/bind.h"
#include "content/public/browser/back_forward_cache.h"
#include "content/public/browser/navigation_controller.h"
#include "content/public/browser/render_process_host.h"
#include "mojo/public/cpp/bindings/callback_helpers.h"
""" + anchor)
    anchor = 'static std::string JNI_ContentUtils_GetBrowserUserAgent(JNIEnv* env) {'
    replace(DESKTOP_UA, anchor, """\
// Refresh the cached default UA without reinitializing renderer security state.
static void JNI_ContentUtils_UpdateUserAgent(
    JNIEnv* env,
    const std::string& mobile,
    const std::string& desktop,
    const base::android::JavaRef<jobject>& j_ready) {
  if (!embedder_support::SetArgonUserAgentOverrides(mobile, desktop)) {
    base::android::RunRunnableAndroid(j_ready);
    return;
  }
  std::vector<content::RenderProcessHost*> renderers;
  for (auto it = content::RenderProcessHost::AllHostsIterator(); !it.IsAtEnd();
       it.Advance()) {
    auto* renderer = it.GetCurrentValue();
    if (renderer->IsInitializedAndNotDead()) {
      renderers.push_back(renderer);
    }
  }
  auto done = base::BarrierClosure(
      renderers.size(),
      base::BindOnce(
          [](base::android::ScopedJavaGlobalRef<jobject> ready) {
            base::android::RunRunnableAndroid(ready);
          },
          base::android::ScopedJavaGlobalRef<jobject>(env, j_ready)));
  const auto user_agent = embedder_support::GetUserAgent();
  const auto metadata = embedder_support::GetUserAgentMetadata();
  for (auto* renderer : renderers) {
    renderer->UpdateArgonUserAgent(
        user_agent, metadata,
        mojo::WrapCallbackWithDefaultInvokeIfNotRun(
            base::BindOnce([](base::RepeatingClosure done) { done.Run(); }, done)));
  }
}

""" + anchor)

    anchor = "    @NativeMethods\n    interface Natives {"
    replace(CONTENT_JAVA, anchor, """\
    /** Update live renderer caches before reloading pages with the new UA. */
    public static void updateUserAgent(String mobile, String desktop, Runnable ready) {
        ContentUtilsJni.get().updateUserAgent(mobile, desktop, ready);
    }

""" + anchor + "\n        void updateUserAgent(@JniType(\"std::string\") String mobile,\n"
            "                @JniType(\"std::string\") String desktop, Runnable ready);\n")
    anchor = "            mWebContents = webContents;"
    replace(TAB_IMPL, anchor, anchor + "\n"
            "            org.chromium.chrome.browser.base.ArgonUserAgentController"
            ".onTabInitialized(this);")

    # Keep Chromium's private renderer Mojo interface inside //content.
    # Android Chrome communicates through the public RenderProcessHost API.
    anchor = "class StorageKey;"
    replace(RENDER_PROCESS_HOST_HEADER, anchor,
            anchor + "\nstruct UserAgentMetadata;")
    anchor = "  virtual mojom::Renderer* GetRendererInterface() = 0;"
    replace(RENDER_PROCESS_HOST_HEADER, anchor, """\
  // Update only the renderer UA cache; security state stays initialized.
  void UpdateArgonUserAgent(const std::string& user_agent,
                            const blink::UserAgentMetadata& metadata,
                            base::OnceClosure callback);

""" + anchor)
    anchor = '#include "content/common/child_process.mojom.h"'
    replace(RENDER_PROCESS_HOST_IMPL, anchor,
            anchor + '\n#include "content/common/renderer.mojom.h"')
    anchor = "// static\nvoid RenderProcessHost::SetMaxRendererProcessCount(size_t count) {"
    replace(RENDER_PROCESS_HOST_IMPL, anchor, """\
void RenderProcessHost::UpdateArgonUserAgent(
    const std::string& user_agent,
    const blink::UserAgentMetadata& metadata,
    base::OnceClosure callback) {
  GetRendererInterface()->UpdateUserAgent(user_agent, metadata,
                                          std::move(callback));
}

""" + anchor)

    anchor = "  // Initialize renderer user agent string, user agent metadata, CORS exempt"
    replace(RENDERER_MOJOM, anchor, """\
  // Update only the UA caches; security and origin-trial initialization stays intact.
  UpdateUserAgent(string user_agent, blink.mojom.UserAgentMetadata metadata) => ();

""" + anchor)
    anchor = "  void InitializeRenderer("
    replace(RENDER_THREAD_HEADER, anchor, """\
  void UpdateUserAgent(const std::string& user_agent,
                       const blink::UserAgentMetadata& user_agent_metadata,
                       UpdateUserAgentCallback callback) override;
""" + anchor)
    anchor = "void RenderThreadImpl::InitializeRenderer("
    replace(RENDER_THREAD, anchor, """\
void RenderThreadImpl::UpdateUserAgent(
    const std::string& user_agent,
    const blink::UserAgentMetadata& user_agent_metadata,
    UpdateUserAgentCallback callback) {
  user_agent_ = WebString::FromUtf8(user_agent);
  GetContentClient()->renderer()->DidSetUserAgent(user_agent);
  user_agent_metadata_ = user_agent_metadata;
  std::move(callback).Run();
}

""" + anchor)

    for path, content in sources.items():
        (src / path).write_text(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src)
