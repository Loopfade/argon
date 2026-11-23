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
WEB_CONTENTS_IMPL = Path("content/browser/web_contents/web_contents_impl.cc")
RENDER_PROCESS_HOST_IMPL = Path("content/browser/renderer_host/render_process_host_impl.cc")
NAVIGATION_CONTROLLER = Path("content/browser/renderer_host/navigation_controller_android.cc")
RENDER_FRAME = Path("content/renderer/render_frame_impl.cc")
TAB_UTILS = Path("chrome/android/java/src/org/chromium/chrome/browser/tab/TabUtils.java")
TAB_IMPL = Path("chrome/android/java/src/org/chromium/chrome/browser/tab/TabImpl.java")
TAB_CREATOR = Path("chrome/android/java/src/org/chromium/chrome/browser/tabmodel/ChromeTabCreator.java")
TABBED_MENU = Path("chrome/android/java/src/org/chromium/chrome/browser/tabbed_mode/TabbedAppMenuPropertiesDelegate.java")
CUSTOM_TAB_MENU = Path("chrome/android/java/src/org/chromium/chrome/browser/customtabs/CustomTabAppMenuPropertiesDelegate.java")
NAVIGATION_REQUEST = Path("content/browser/renderer_host/navigation_request.cc")
NAVIGATION_PARAMS = Path("third_party/blink/public/mojom/navigation/navigation_params.mojom")
CHROME_ACTIVITY = Path("chrome/android/java/src/org/chromium/chrome/browser/app/ChromeActivity.java")
RDS_OBSERVER = Path("chrome/browser/content_settings/request_desktop_site_web_contents_observer_android.cc")
DOCUMENT_STATE = Path("content/renderer/document_state.h")

CLIENT_HINTS = Path("content/browser/client_hints/client_hints.cc")
FRAME_FETCH = Path("third_party/blink/renderer/core/loader/frame_fetch_context.cc")

INPUT_PATHS = (
    ANDROID_GN, STARTUP, PRIVACY_HOOKS, UA_UTILS, UA_HEADER, DESKTOP_UA,
    WEB_CONTENTS_IMPL,
    TITANIUM_CHROME_JAVA_SOURCES, TITANIUM_CHROME_RESOURCES,
    TITANIUM_PRIVACY_PREFERENCES, TITANIUM_SEARCH_INDEX_PROVIDER_HOOKS,
    RENDER_PROCESS_HOST_IMPL, CLIENT_HINTS, FRAME_FETCH, NAVIGATION_CONTROLLER,
    RENDER_FRAME, DOCUMENT_STATE, TAB_UTILS, TAB_IMPL, CHROME_ACTIVITY, RDS_OBSERVER,
    TAB_CREATOR, TABBED_MENU, CUSTOM_TAB_MENU, NAVIGATION_REQUEST, NAVIGATION_PARAMS,
)


def patch_navigation_controller(source: str) -> str:
    anchor = '#include "content/browser/renderer_host/navigation_request.h"'
    source = replace_once(source, anchor, anchor + '\n'
                          '#include "content/browser/renderer_host/page_impl.h"\n'
                          '#include "content/browser/renderer_host/render_frame_host_impl.h"')
    source = replace_once(source, """\
  if (GetUseDesktopUserAgent(env) == enabled) {
    return;
  }
""", """\
  // A newer selection supersedes any UA change queued during navigation.
  // Evaluate the no-op condition when that selection is actually applied.
  weak_factory_.InvalidateWeakPtrs();
""")
    source = replace_once(source, "  // Set the flag in the NavigationEntry.", """\
  // The entry records the requested mode before its reload commits. A cancelled
  // reload (or a previous call with reload_on_state_change=false) can leave the
  // current document using a different UA. In that case a repeated request must
  // reload, even though the entry already contains the requested flag.
  //
  // A pending navigation may also commit the opposite mode after this call.
  // Even when the committed entry and visible document agree, an explicit
  // request for their mode must replace a pending entry using the other UA.
  // The earlier skip_on_initial_navigation guard already protects initial
  // entries; if that flag is false, do not ignore a mismatching pending mode.
  NavigationEntry* pending_entry = navigation_controller_->GetPendingEntry();
  const bool pending_mode_matches =
      !pending_entry || pending_entry->GetIsOverridingUserAgent() == enabled;
  if (entry->GetIsOverridingUserAgent() == enabled &&
      (!reload_on_state_change ||
       (navigation_controller_->frame_tree()
                    .root()
                    ->current_frame_host()
                    ->GetPage()
                    .is_overriding_user_agent() == enabled &&
        pending_mode_matches))) {
    return;
  }

  // Set the flag in the NavigationEntry.""")
    return source



def patch_web_contents_inheritance(source: str) -> str:
    """Keep new-tab UA selection synchronized when the override string is unchanged."""
    return replace_once(source, """\
  if (GetUserAgentOverride() == ua_override) {
    return;
  }
""", """\
  if (GetUserAgentOverride() == ua_override) {
    // Desktop/mobile may change new-tab inheritance while the effective
    // browser User-Agent string stays the same (notably for custom UA).
    // Refresh delayed-load policy without an unnecessary renderer reload.
    should_override_user_agent_in_new_tabs_ = override_in_new_tabs;
    if (delayed_load_url_params_.get()) {
      delayed_load_url_params_->override_user_agent =
          override_in_new_tabs ? NavigationController::UA_OVERRIDE_TRUE
                               : NavigationController::UA_OVERRIDE_FALSE;
    }
    return;
  }
""")


def patch_tab_user_agent_utils(source: str) -> str:
    """Use the same tab-scoped mode for WebContents, reloads and future loads."""
    anchor = "import org.chromium.chrome.browser.media.MediaCaptureDevicesDispatcherAndroid;"
    source = replace_once(source, anchor, anchor + """
import org.chromium.chrome.browser.content.ContentUtils;""")
    source = replace_once(source,
                          "import org.chromium.chrome.browser.content.ContentUtils;",
                          "import org.chromium.chrome.browser.content.ContentUtils;\n"
                          "import org.chromium.chrome.browser.desktop_site.DesktopSiteUtils;")
    return replace_once(source, """\
    public static void switchUserAgent(Tab tab, boolean switchToDesktop) {
        final boolean reloadOnChange = !tab.isNativePage();
        assumeNonNull(tab.getWebContents())
                .getNavigationController()
                .setUseDesktopUserAgent(
                        switchToDesktop, reloadOnChange, /* skipOnInitialNavigation= */ true);
    }
""", """\
    public static void switchUserAgent(Tab tab, boolean switchToDesktop) {
        switchUserAgent(tab, switchToDesktop, /* explicitSelection= */ true);
    }

    public static void resetUserAgent(Tab tab) {
        tab.setUserAgent(TabUserAgent.DEFAULT);
        boolean desktop = DesktopSiteUtils.shouldOverrideDesktopSite(
                tab.getProfile(), tab.getUrl(), tab.getContext());
        switchUserAgent(tab, desktop, /* explicitSelection= */ false);
    }

    public static void switchUserAgent(
            Tab tab, boolean switchToDesktop, boolean explicitSelection) {
        WebContents webContents = assumeNonNull(tab.getWebContents());
        if (explicitSelection) {
            // Keep a manual choice scoped to this tab, not the site's rule.
            tab.setUserAgent(switchToDesktop ? TabUserAgent.DESKTOP : TabUserAgent.MOBILE);
        } else if (tab.getUserAgent() == TabUserAgent.DESKTOP) {
            // An automatic switch during a forum navigation must not invert
            // the manually pinned desktop selection.
            switchToDesktop = true;
        } else if (tab.getUserAgent() == TabUserAgent.MOBILE) {
            // Reconcile both the renderer override and navigation flag with
            // the pinned mobile choice, even when an automatic caller supplies
            // the opposite mode after a redirect/reload.
            switchToDesktop = false;
        }

        // The navigation flag alone does not refresh an existing WebContents.
        // This boolean controls new-tab UA inheritance, not the UA string
        // ContentUtils constructs; keep it aligned with the selected mode.
        ContentUtils.setUserAgentOverride(webContents, switchToDesktop);
        webContents.getNavigationController()
                .setUseDesktopUserAgent(
                        switchToDesktop, !tab.isNativePage(),
                        /* skipOnInitialNavigation= */ true);
    }
""")


def patch_menu_user_agent_selection(source: str) -> str:
    """Keep the menu's manual choice out of persistent domain settings."""
    before = """\
        if (id == R.id.request_desktop_site_id || id == R.id.request_desktop_site_check_id) {
            boolean usingDesktopUserAgent =
                    webContents.getNavigationController().getUseDesktopUserAgent();
            usingDesktopUserAgent = !usingDesktopUserAgent;
            DesktopSiteUtils.setRequestDesktopSiteContentSettingsForUrl(
                    currentProfile, currentTab.getUrl(), usingDesktopUserAgent);
            // Use TabUtils.switchUserAgent() instead of Tab.reload(). Because we need to reload
            // with LoadOriginalRequestURL. See http://crbug.com/40894824 for details.
            TabUtils.switchUserAgent(currentTab, usingDesktopUserAgent);
            TrackerFactory.getTrackerForProfile(currentProfile)
                    .notifyEvent(EventConstants.APP_MENU_DESKTOP_SITE_EXCEPTION_ADDED);
            RequestDesktopUtils.recordUserChangeUserAgent(usingDesktopUserAgent, getActivityTab());
            return true;
        }
"""
    after = """\
        if (id == R.id.request_desktop_site_id || id == R.id.request_desktop_site_check_id) {
            boolean usingDesktopUserAgent =
                    webContents.getNavigationController().getUseDesktopUserAgent();
            usingDesktopUserAgent = !usingDesktopUserAgent;
            // This choice belongs to the current tab. Domain exceptions are
            // managed separately in site settings and must survive menu toggles.
            // Use TabUtils.switchUserAgent() instead of Tab.reload(). Because we need to reload
            // with LoadOriginalRequestURL. See http://crbug.com/40894824 for details.
            TabUtils.switchUserAgent(currentTab, usingDesktopUserAgent);
            RequestDesktopUtils.recordUserChangeUserAgent(usingDesktopUserAgent, getActivityTab());
            return true;
        }
"""
    source = replace_once(source, before, after)
    source = replace_once(source, after, """\
        if (id == R.id.argon_reset_user_agent_id) {
            TabUtils.resetUserAgent(currentTab);
            return true;
        }
""" + after)
    # This was ChromeActivity's only DesktopSiteUtils call. Reject new uses so
    # future upstream changes cannot silently keep a menu-side settings write.
    unused_import = "import org.chromium.chrome.browser.desktop_site.DesktopSiteUtils;\n"
    if unused_import in source:
        if source.count("DesktopSiteUtils") != 1:
            raise ValueError("ChromeActivity DesktopSiteUtils uses changed; review menu isolation")
        source = source.replace(unused_import, "", 1)
    return source


def patch_tab_user_agent_state(source: str) -> str:
    source = replace_once(source, """\
    public void setUserAgent(@TabUserAgent int userAgent) {
        mUserAgent = userAgent;
    }
""", """\
    public void setUserAgent(@TabUserAgent int userAgent) {
        if (mUserAgent == userAgent) return;
        mUserAgent = userAgent;
        // Returning to DEFAULT can leave the effective UA unchanged. Persist
        // the choice even when no navigation marks the tab dirty for us.
        TabStateAttributes.setDirty(this);
    }
""")
    source = replace_once(source, """\
        boolean shouldRequestDesktopSite =
                DesktopSiteUtils.shouldOverrideDesktopSite(mProfile, url, getContext());""", """\
        // Explicit per-tab choices take precedence over the domain default
        // when loading another URL or restoring the tab's persisted state.
        boolean shouldRequestDesktopSite =
                mUserAgent == TabUserAgent.DESKTOP
                || (mUserAgent != TabUserAgent.MOBILE
                        && DesktopSiteUtils.shouldOverrideDesktopSite(
                                mProfile, url, getContext()));""")
    return replace_once(source, """\
        TabUtils.switchUserAgent(this, /* switchToDesktop= */ !usingDesktopUserAgent);""", """\
        TabUtils.switchUserAgent(
                this, /* switchToDesktop= */ !usingDesktopUserAgent,
                /* explicitSelection= */ false);""")


def patch_tab_creator(source: str) -> str:
    """Transfer a duplicate's manual mode before WebContents initialization."""
    for builder in ("createLazyTabWithWebContents(tabProfile)",
                    "createLiveTab(tabProfile, !openInForeground)"):
        anchor = f"""TabBuilder.{builder}
                                .setParent(parent)
                                .setWindow(mNativeWindow)
                                .setLaunchType(type)"""
        source = replace_once(source, anchor, anchor + """
                                .setPreInitializeAction(created -> {
                                    if (parent != null
                                            && type == TabLaunchType.FROM_TAB_LIST_INTERFACE) {
                                        created.setUserAgent(parent.getUserAgent());
                                    }
                                })""")
    return source


def patch_automatic_mode_menu(source: str, count: int) -> str:
    """Offer an explicit reset without changing the existing one-tap toggle."""
    anchor = "import org.chromium.chrome.browser.tab.Tab;"
    source = replace_once(source, anchor, anchor + "\n"
                          "import org.chromium.chrome.browser.tab.TabUserAgent;")
    before = "if (rdsListItem != null) modelList.add(rdsListItem);"
    after = before + """
        if (rdsListItem != null && currentTab != null
                && (currentTab.getUserAgent() == TabUserAgent.MOBILE
                        || currentTab.getUserAgent() == TabUserAgent.DESKTOP)) {
            modelList.add(new org.chromium.ui.modelutil.MVCListAdapter.ListItem(
                    AppMenuHandler.AppMenuItemType.STANDARD,
                    AppMenuItemUtils.buildModelForStandardMenuItem(
                            mContext, getAppMenuItemTheme(), R.id.argon_reset_user_agent_id,
                            R.string.argon_user_agent_auto, 0, false)));
        }"""
    if source.count(after) == count and before not in source.replace(after, ""):
        return source
    if source.count(before) != count or "R.id.argon_reset_user_agent_id" in source:
        raise ValueError("Desktop-site menu layout changed; review the automatic-mode action")
    return source.replace(before, after)


def patch_navigation_params(source: str) -> str:
    anchor = "  bool can_use_device_bound_session_js_api = false;"
    # A new Mojo version preserves the existing positional constructors.
    return replace_once(source, anchor + "\n};", anchor + """

  // The browser discarded the target's history viewport after changing UA.
  [MinVersion=1] bool reset_history_view_state_for_user_agent_change = false;
};""")


def patch_history_view_state(source: str) -> str:
    """Clear only layout state when the target history entry changes UA mode."""
    anchor = '#include "third_party/blink/public/common/user_agent/user_agent_metadata.h"'
    source = replace_once(source, anchor, anchor + '\n'
                          '#include "third_party/blink/public/common/page_state/page_state_serialization.h"')
    anchor = """// Computes the value that should be set for the User-Agent header, if
// `user_agent_override` is non-empty, `user_agent_override` is returned as the
// header value.
std::string ComputeUserAgentValue(const std::string& user_agent_override) {"""
    source = replace_once(source, anchor, """\
#if BUILDFLAG(IS_ANDROID)
// A history document loaded with another UA has another layout. Keep its
// URL, form/POST data and history identity, but discard saved viewport state.
void ResetUserAgentHistoryViewState(blink::ExplodedFrameState* frame) {
  frame->did_save_scroll_or_scale_state = false;
  frame->page_scale_factor = 0;
  frame->scroll_offset = {};
  frame->visual_viewport_scroll_offset = {};
  frame->scroll_anchor_selector.reset();
  frame->scroll_anchor_offset = {};
  frame->scroll_anchor_simhash = 0;
  for (auto& child : frame->children) {
    ResetUserAgentHistoryViewState(&child);
  }
}
#endif

""" + anchor)
    anchor = "  commit_params_->is_overriding_user_agent = override_ua;"
    return replace_once(source, anchor, """\
#if BUILDFLAG(IS_ANDROID)
  // Compare against the target entry before overriding it, not the currently
  // displayed document: both new/current documents may already be mobile.
  const bool restores_history =
      NavigationTypeUtils::IsHistory(common_params_->navigation_type) ||
      common_params_->navigation_type == blink::mojom::NavigationType::RESTORE ||
      common_params_->navigation_type ==
          blink::mojom::NavigationType::RESTORE_WITH_POST;
  if (IsInOutermostMainFrame() && !IsSameDocument() && restores_history &&
      commit_params_->is_overriding_user_agent != override_ua) {
    blink::ExplodedPageState state;
    if (blink::DecodePageState(commit_params_->page_state, &state)) {
      ResetUserAgentHistoryViewState(&state.top);
      blink::EncodePageState(state, &commit_params_->page_state);
      commit_params_->reset_history_view_state_for_user_agent_change = true;
    }
  }
#endif

""" + anchor)


def patch_request_desktop_observer(source: str) -> str:
    anchor = '#include "chrome/browser/content_settings/host_content_settings_map_factory.h"'
    source = replace_once(source, anchor,
                          '#include "chrome/browser/android/tab_android.h"\n' + anchor)
    anchor = '#include "chrome/browser/profiles/profile.h"'
    source = replace_once(source, anchor,
                          anchor + '\n#include "chrome/browser/ui/android/tab_model/tab_model.h"')
    anchor = """\
  if (!navigation_handle->IsInOutermostMainFrame()) {
    return;
  }
"""
    return replace_once(source, anchor, anchor + """\

  // Apply the tab's explicit choice even when a history entry carries the
  // opposite UA. Merely skipping the site rule leaves that stale flag intact.
  // This also covers browser-initiated history; default tabs retain the
  // ordinary renderer-only site-setting path below.
  auto* tab = TabAndroid::FromWebContents(web_contents());
  if (tab) {
    const auto mode = static_cast<TabModel::TabUserAgent>(tab->GetUserAgent());
    // UNSET is a legacy, uninitialized tab: it follows the site policy.
    if (mode == TabModel::TabUserAgent::MOBILE ||
        mode == TabModel::TabUserAgent::DESKTOP) {
      navigation_handle->SetIsOverridingUserAgent(
          mode == TabModel::TabUserAgent::DESKTOP);
      return;
    }
  }
""")


def patch_document_scale_state(source: str) -> str:
    source = replace_once(source, "  // This is a fake navigation request id,", """\
  // One-shot action for a committing UA-mode change. Clone() deliberately does
  // not copy it into document replacements (javascript:, XSLT or discard).
  void set_reset_scroll_and_scale_state(bool value) {
    reset_scroll_and_scale_state_ = value;
  }
  bool TakeResetScrollAndScaleState() {
    const bool value = reset_scroll_and_scale_state_;
    reset_scroll_and_scale_state_ = false;
    return value;
  }

  // This is a fake navigation request id,""")
    return replace_once(source, "  bool is_overriding_user_agent_ = false;",
                        "  bool is_overriding_user_agent_ = false;\n"
                        "  bool reset_scroll_and_scale_state_ = false;")


def patch_renderer_scale_state(source: str) -> str:
    source = replace_once(source, "  navigation_params->frame_load_type = load_type;", """\
#if BUILDFLAG(IS_ANDROID)
  // A mode switch is a reload, but its old desktop/mobile scale must not be
  // restored into the new layout. Read the still-committed main document before
  // CommitNavigation swaps it out. Ordinary reloads/history keep their zoom.
  const bool reset_history_view_state =
      (load_type == WebFrameLoadType::kBackForward ||
       load_type == WebFrameLoadType::kRestore) &&
      commit_params->reset_history_view_state_for_user_agent_change;
  const bool reset_reload_view_state =
      (load_type == WebFrameLoadType::kReload ||
       load_type == WebFrameLoadType::kReloadBypassingCache ||
       load_type == WebFrameLoadType::kReplaceCurrentItem) &&
      ShouldUseUserAgentOverride() != document_state->is_overriding_user_agent();
  if (frame_->IsOutermostMainFrame() &&
      (reset_history_view_state || reset_reload_view_state)) {
    document_state->set_reset_scroll_and_scale_state(true);
  }
#endif

  navigation_params->frame_load_type = load_type;""")
    return replace_once(source, "  DCHECK(!navigation_state->WasWithinSameDocument());", """\
  DCHECK(!navigation_state->WasWithinSameDocument());

#if BUILDFLAG(IS_ANDROID)
  if (document_state->TakeResetScrollAndScaleState()) {
    // The new HistoryItem is installed now. Clear its copied view state before
    // FrameLoader restores it, and let this document calculate its initial scale.
    GetWebView()->ResetScrollAndScaleState();
  }
#endif""")


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
            '  "java/res/values-ru/argon_user_agent_strings.xml",\n'
            '  "java/res/values/argon_user_agent_ids.xml",\n' + anchor)
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
    // WebContents uses metadata.mobile to select the desktop viewport. Keep
    // only that local layout signal; UA-CH headers stay suppressed below.
    ua_override.ua_metadata_override.emplace();
    ua_override.ua_metadata_override->mobile = false;
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

    sources[WEB_CONTENTS_IMPL] = patch_web_contents_inheritance(sources[WEB_CONTENTS_IMPL])
    sources[NAVIGATION_CONTROLLER] = patch_navigation_controller(sources[NAVIGATION_CONTROLLER])
    sources[DOCUMENT_STATE] = patch_document_scale_state(sources[DOCUMENT_STATE])
    sources[RENDER_FRAME] = patch_renderer_scale_state(sources[RENDER_FRAME])
    sources[TAB_UTILS] = patch_tab_user_agent_utils(sources[TAB_UTILS])
    sources[TAB_IMPL] = patch_tab_user_agent_state(sources[TAB_IMPL])
    sources[TAB_CREATOR] = patch_tab_creator(sources[TAB_CREATOR])
    sources[TABBED_MENU] = patch_automatic_mode_menu(sources[TABBED_MENU], 2)
    sources[CUSTOM_TAB_MENU] = patch_automatic_mode_menu(sources[CUSTOM_TAB_MENU], 1)
    sources[NAVIGATION_PARAMS] = patch_navigation_params(sources[NAVIGATION_PARAMS])
    sources[NAVIGATION_REQUEST] = patch_history_view_state(sources[NAVIGATION_REQUEST])
    sources[CHROME_ACTIVITY] = patch_menu_user_agent_selection(sources[CHROME_ACTIVITY])
    sources[RDS_OBSERVER] = patch_request_desktop_observer(sources[RDS_OBSERVER])

    for path, content in sources.items():
        (src / path).write_text(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src)
