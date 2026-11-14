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
RENDER_PROCESS_HOST_IMPL = Path("content/browser/renderer_host/render_process_host_impl.cc")

CLIENT_HINTS = Path("content/browser/client_hints/client_hints.cc")
FRAME_FETCH = Path("third_party/blink/renderer/core/loader/frame_fetch_context.cc")

INPUT_PATHS = (
    ANDROID_GN, STARTUP, PRIVACY_HOOKS, UA_UTILS, UA_HEADER, DESKTOP_UA,
    TITANIUM_CHROME_JAVA_SOURCES, TITANIUM_CHROME_RESOURCES,
    TITANIUM_PRIVACY_PREFERENCES, TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS,
    RENDER_PROCESS_HOST_IMPL, CLIENT_HINTS, FRAME_FETCH,
)


def apply(src: Path) -> None:
    sources = {path: (src / path).read_text() for path in INPUT_PATHS}
    if "SetArgonUserAgentOverrides" in sources[UA_UTILS]:
        raise ValueError("Stale live-UA patch: prepare a fresh pinned Chromium checkout")

    def replace(path, anchor, replacement):
        try:
            sources[path] = replace_once(sources[path], anchor, replacement)
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from error

    anchor = f'      "{BASE}/ApplicationInitHook.java",'
    # Keep the startup model independent of Tab/Profile and the isolated Chrome
    # feature. UA selection stays immutable until the next browser process.
    replace(ANDROID_GN, anchor, anchor + f'\n      "{BASE}/ArgonUserAgent.java",')
    anchor = "            PathUtils.setPrivateDataDirectorySuffix(PRIVATE_DATA_DIRECTORY_SUFFIX);"
    replace(STARTUP, anchor, "            ArgonUserAgent.applySavedOverride();\n" + anchor)

    # The theme patch prepends to PrivacySettingsExt. Use a separate entry so
    # both registrations retain their anchors during repeated preparation.
    anchor = '  "java/src/org/chromium/chrome/browser/settings/search/SearchIndexProviderHooks.java",'
    replace(TITANIUM_CHROME_JAVA_SOURCES, anchor,
            anchor + f'\n  "{PRIVACY}/ArgonUserAgentPreference.java",')
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
    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_user_agent_restart"
        android:persistent="false"
        android:title="@string/argon_user_agent_restart" />
    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_user_agent_help"
        android:selectable="false"
        android:summary="@string/argon_user_agent_help" />
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
            "// Process-lifetime override. Changes in settings require a browser restart.\n"
            "std::optional<std::string> GetArgonUserAgentOverride(bool desktop = false);")
    anchor = "std::string GetUserAgent() {"
    replace(UA_UTILS, anchor, """\
std::optional<std::string> GetArgonUserAgentOverride(bool desktop) {
  const std::string ua =
      base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII(
          desktop ? "argon-user-agent-desktop" : "argon-user-agent");
  if (ua.empty() || !net::HttpUtil::IsValidHeaderValue(ua)) {
    return std::nullopt;
  }
  return ua;
}

""" + anchor + """
  if (auto ua = GetArgonUserAgentOverride()) {
    return *ua;
  }
""")
    anchor = "blink::UserAgentMetadata GetUserAgentMetadata(bool only_low_entropy_ch) {"
    replace(UA_UTILS, anchor, anchor + """
  if (auto ua = GetArgonUserAgentOverride()) {
    // UA-only compatibility presets do not claim Chromium's default identity.
    // Their HTTP UA client hints are suppressed in navigation and subresources.
    blink::UserAgentMetadata metadata;
    metadata.mobile = ua->find("Mobile") != std::string::npos;
    return metadata;
  }
""")
    anchor = "  constexpr char kLinuxInfoStr[]"
    replace(DESKTOP_UA, anchor, """\
  if (auto ua = embedder_support::GetArgonUserAgentOverride(/*desktop=*/true)) {
    blink::UserAgentOverride ua_override = blink::UserAgentOverride::UserAgentOnly(*ua);
    ua_override.ua_metadata_override.reset();
    content::WebContents::FromJavaWebContents(jweb_contents)
        ->SetUserAgentOverride(ua_override, j_override_in_new_tabs);
    return;
  }

""" + anchor)

    # Keep the default browser's native metadata. For UA-only compatibility
    # presets omit UA-CH, rather than sending empty or contradictory headers.
    # Forward only the boolean flag; renderer UA strings arrive via Chromium's
    # ordinary InitializeRenderer message, as do worker startup parameters.
    anchor = "      embedder_support::kUseMobileUserAgent,"
    replace(RENDER_PROCESS_HOST_IMPL, anchor,
            '      "argon-user-agent-hide-client-hints",\n' + anchor)
    anchor = "  if (!disable_due_to_custom_ua) {"
    replace(CLIENT_HINTS, anchor, """\
  disable_due_to_custom_ua |=
      base::CommandLine::ForCurrentProcess()->HasSwitch(
          "argon-user-agent-hide-client-hints");

""" + anchor)
    anchor = '#include "base/feature_list.h"'
    replace(FRAME_FETCH, anchor, '#include "base/command_line.h"\n' + anchor)
    anchor = "  std::optional<UserAgentMetadata> ua = GetUserAgentMetadata();"
    replace(FRAME_FETCH, anchor, anchor + """
  if (base::CommandLine::ForCurrentProcess()->HasSwitch(
          "argon-user-agent-hide-client-hints")) {
    ua.reset();
  }
""")

    for path, content in sources.items():
        (src / path).write_text(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src)
