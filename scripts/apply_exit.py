#!/usr/bin/env python3
"""Wire explicit exit after the CA and background-playback overlays, atomically and idempotently."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
JAVA = 'chrome/android/java/src/org/chromium/chrome/browser/'
EXT = 'titanium/chromium_src/chrome/android/'
PACKAGE = 'org.chromium.chrome.browser.exit.ArgonExitCoordinator'
INPUT_PATHS = tuple(map(Path, (
    JAVA + 'app/ChromeActivity.java', JAVA + 'ConfirmQuitHelper.java',
    JAVA + 'ChromeTabbedActivity.java',
    JAVA + 'init/ChromeLifetimeController.java', JAVA + 'tab/TabImpl.java',
    JAVA + 'LaunchIntentDispatcher.java',
    JAVA + 'background_task_scheduler/ChromeBackgroundTaskFactory.java',
    JAVA + 'tabbed_mode/TabbedAppMenuPropertiesDelegate.java',
    'chrome/browser/lifetime/android/java/src/org/chromium/chrome/browser/lifetime/ApplicationLifetime.java',
    'chrome/browser/android/tab_android.cc', 'chrome/browser/android/BUILD.gn',
    'content/public/browser/content_browser_client.h',
    'content/public/browser/content_browser_client.cc',
    'chrome/browser/chrome_content_browser_client.h',
    'chrome/browser/chrome_content_browser_client.cc',
    'content/browser/renderer_host/render_process_host_impl.cc',
    'content/browser/renderer_host/render_frame_host_manager.h',
    'content/browser/renderer_host/render_frame_host_manager.cc',
    'content/browser/renderer_host/navigation_request.cc',
    'chrome/android/BUILD.gn', EXT + 'chrome_java_ext_sources.gni',
    EXT + 'chrome_app_java_resources_ext_sources.gni',
    'titanium/chromium_src/chrome/browser/android/android_cc_ext_sources.gni',
    'titanium/chromium_src/chrome/browser/android/android_cc_ext_deps.gni',
    EXT + 'java/res/xml/privacy_preferences_ext.xml',
)))


def replace(text, before, after):
    if text.count(after) == 1:
        if before in text.replace(after, '', 1):
            raise ValueError(f'Ambiguous exit patch anchor: {before[:90]}')
        return text
    if text.count(after) or text.count(before) != 1:
        raise ValueError(f'Exit patch anchor changed: {before[:90]}')
    return text.replace(before, after, 1)


def insert(text, anchor, addition):
    return replace(text, anchor, anchor + addition)


def patch_activity(text):
    before = '        if (id == R.id.quit_chrome && !fromMenu) {'
    after = f'''        if (id == R.id.quit_chrome && fromMenu) {{
            {PACKAGE}.requestExit();
            return true;
        }}
{before}'''
    text = replace(text, before, after)
    text = insert(text, '    public void onResumeWithNative() {',
                  f'\n        {PACKAGE}.onActivityReady(this);')
    text = insert(text, '    public void onPauseWithNative() {',
                  f'\n        {PACKAGE}.onActivityHidden(this);')
    return insert(text, '    public void onNewIntentWithNative(Intent intent) {',
                  f'\n        if ({PACKAGE}.isClosing()) return;')


def patch_quit(text):
    return replace(text, '        ApplicationLifetime.terminate(/* restart= */ false);',
                   f'        {PACKAGE}.requestExit();')


def patch_tabbed_activity(text):
    before = '            noRestoreState |= TabPreferencesUtils.shouldCloseTabsOnExit();'
    return insert(text, before, f'\n            noRestoreState |= {PACKAGE}.shouldDiscardRestoredTabs();')


def patch_lifetime(text):
    text = insert(text, '    public static void terminate(boolean restart) {',
                  '\n        if (restart && !sRestartAllowed.getAsBoolean()) return;')
    return insert(text, '    private static boolean sRestartForLocaleSwitch;', '''
    private static java.util.function.BooleanSupplier sRestartAllowed = () -> true;

    /** The embedding browser supplies policy without a dependency on its UI module. */
    public static void setRestartAllowedSupplier(java.util.function.BooleanSupplier allowed) {
        sRestartAllowed = allowed;
    }
''')


def patch_controller(text):
    text = insert(text, '    public static void initialize() {',
                  f'\n        {PACKAGE}.installRestartGuard();')
    before = '''        TabbedStartupWindowPolicyDelegate.getInstance()
                .maybeSaveSessionStateOnTermination(SessionStartupPolicy.RESTORE_ALL);'''
    text = replace(text, before, f'''        if (!{PACKAGE}.isClosing()
                || {PACKAGE}.shouldPreserveTabs()) {{
    {before}
        }}''')
    before = '''                MultiWindowUtils.isNewStartupWindowPolicyEnabled()
                        || MultiWindowUtils.isRestoreOnStartupPrefSyncEnabled();'''
    return replace(text, before, f'''                {PACKAGE}.isClosing()
                        || MultiWindowUtils.isNewStartupWindowPolicyEnabled()
                        || MultiWindowUtils.isRestoreOnStartupPrefSyncEnabled();''')


def patch_tab(text):
    text = insert(text, '    public boolean loadIfNeeded(boolean forceBackingSize) {',
                  f'\n        if ({PACKAGE}.isBlockingPages()) return false;')
    text = insert(text, '    public LoadUrlResult loadUrl(LoadUrlParams params) {', f'''
        if ({PACKAGE}.isBlockingPages()) {{
            // A frozen tab must not hold both serialized state and pending params.
            // Preserve a new launch/VIEW navigation while recovery blocks renderers.
            if (mWebContentsState != null) {{
                if (mWebContentsState.appendPendingNavigation(
                        getProfile(), getTitle(), params, false)) {{
                    mPendingLoadParams = null;
                }} else {{
                    mWebContentsState.destroy();
                    mWebContentsState = null;
                    mPendingLoadParams = params;
                }}
            }} else {{
                mPendingLoadParams = params;
            }}
            mUrl = new GURL(params.getUrl());
            return new LoadUrlResult(TabLoadStatus.DEFAULT_PAGE_LOAD, null);
        }}''')
    text = insert(text, '        boolean eligibleToCreateWebContents = webContents == null && !isDormant();',
                  f'\n        initializeRenderer &= !{PACKAGE}.isBlockingPages();')
    before = '    private @TabDestroyStatus int destroyWebContents(boolean deleteNativeWebContents) {'
    after = '''    /** Stop loads while keeping WebContents alive for tab-model observer notifications. */
    public boolean prepareForArgonExit(boolean preserveNavigation) {
        if (isDestroyed() || mWebContents == null) return true;
        if (preserveNavigation) {
            mUrl = getUrl();
            mTitle = getTitle();
        }
        // Normal closure must notify observers before destroying WebContents.
        // Preserved tabs keep browser-side navigation until saveState(); the
        // native renderer barrier stops all page execution before data removal.
        mWebContents.stop();
        return true;
    }

''' + before
    return replace(text, before, after)


def patch_tab_native(text):
    text = insert(text, '#include "chrome/browser/android/tab_android.h"',
                  '\n#include "chrome/browser/android/argon_exit.h"')
    before = '''  bool enable_graceful_shutdown = base::FeatureList::IsEnabled(
      chrome::android::kTabAndroidGracefulShutdown);'''
    return replace(text, before, '''  bool enable_graceful_shutdown = !argon::IsExitInProgress() &&
      base::FeatureList::IsEnabled(chrome::android::kTabAndroidGracefulShutdown);''')


def patch_android_gn(text):
    return insert(text, 'source_set("tabs_public") {\n  public = [', '\n    "argon_exit.h",')


def patch_content_client(text):
    return insert(text, '  virtual ~ContentBrowserClient() = default;', '''

  // Allows an embedder to quiesce every renderer (including background workers)
  // before deleting data during explicit shutdown. Other embedders keep normal
  // startup.
  virtual bool IsRendererProcessLaunchAllowed();
''')


def patch_content_client_source(text):
    return insert(text, 'namespace content {', '''

bool ContentBrowserClient::IsRendererProcessLaunchAllowed() {
  return true;
}''')


def patch_chrome_client(text):
    return insert(text, '  ~ChromeContentBrowserClient() override;', '''
#if BUILDFLAG(IS_ANDROID)
  bool IsRendererProcessLaunchAllowed() override;
#endif''')


def patch_chrome_client_source(text):
    text = insert(text, '#include "build/build_config.h"', '''
#if BUILDFLAG(IS_ANDROID)
#include "chrome/browser/android/argon_exit.h"
#endif''')
    before = 'ChromeContentBrowserClient::ChromeContentBrowserClient() {'
    return replace(text, before, '''#if BUILDFLAG(IS_ANDROID)
bool ChromeContentBrowserClient::IsRendererProcessLaunchAllowed() {
  return !argon::IsExitInProgress();
}
#endif

''' + before)


def patch_renderer(text):
    # Init is idempotent; an existing host does not launch another renderer.
    return insert(text, '''bool RenderProcessHostImpl::Init() {
  // calling Init() more than once does nothing, this makes it more convenient
  // for the view host which may not be sure in some cases
  if (IsInitializedAndNotDead())
    return true;''', '''
  if (!GetContentClient()->browser()->IsRendererProcessLaunchAllowed())
    return false;
''')


def patch_frame_manager_header(text):
    return insert(text, '  kBlockedByPendingCommit,', '''
  // The embedder is quiescing pages; cancel without allocating a renderer.
  kBlockedByEmbedder,''')


def patch_frame_manager(text):
    # Init failure alone is unsafe: Chromium CHECKs speculative RFH creation.
    return insert(text, '''RenderFrameHostManager::GetFrameHostForNavigation(
    NavigationRequest* request,
    BrowsingContextGroupSwap* browsing_context_group_swap,
    const ProcessAllocationContext& process_allocation_context,
    std::string* reason) {''', '''
  if (!GetContentClient()->browser()->IsRendererProcessLaunchAllowed()) {
    return base::unexpected(
        GetFrameHostForNavigationFailed::kBlockedByEmbedder);
  }
''')


def patch_navigation_request(text):
    # All three owning call sites return after ResetNavigationRequest, which
    # deletes this request. Neither an error page nor a retry may follow.
    for indent, count in (('      ', 2), ('        ', 1)):
        before = indent + 'case GetFrameHostForNavigationFailed::kCouldNotReinitializeMainFrame:'
        after = (indent + 'case GetFrameHostForNavigationFailed::kBlockedByEmbedder:\n'
                 + indent + '  frame_tree_node_->ResetNavigationRequest(\n'
                 + indent + '      NavigationDiscardReason::kInternalCancellation);\n'
                 + indent + '  return;\n' + before)
        if len(re.findall('^' + re.escape(after), text, re.M)) == count:
            continue
        if len(re.findall('^' + re.escape(before), text, re.M)) != count:
            raise ValueError('Navigation failure ownership changed')
        text = re.sub('^' + re.escape(before), lambda _: after, text, flags=re.M)
    return text


def patch_background_tasks(text):
    return insert(text, '    public BackgroundTask getBackgroundTaskFromTaskId(int taskId) {', f'''
        if ({PACKAGE}.isBackgroundWorkBlocked()) {{
            return new org.chromium.chrome.browser.exit.ArgonExitBackgroundTask();
        }}''')


def patch_menu(text):
    before = '''        } else if (menuGroup == MenuGroup.TABLET_EMPTY_MODE_MENU) {
            populateTabletEmptyModeMenu(modelList);
        }
        return modelList;'''
    after = before.replace('        return modelList;', '''        modelList.add(new ListItem(AppMenuHandler.AppMenuItemType.STANDARD,
                AppMenuItemUtils.buildModelForStandardMenuItem(mContext, getAppMenuItemTheme(),
                        R.id.quit_chrome, R.string.argon_exit_title,
                        shouldShowIconBeforeItem() ? R.drawable.argon_exit : Resources.ID_NULL,
                        isMenuIconAtStart())));
        return modelList;''')
    return replace(text, before, after)


def patch_launch(text):
    for signature in (
        '    public static @Action int dispatchToTabbedActivity(Activity currentActivity, Intent intent) {',
        '    public static @Action int dispatchToCustomTabActivity(Activity currentActivity, Intent intent) {',
    ):
        text = insert(text, signature, f'\n        if ({PACKAGE}.isClosing()) return Action.FINISH_ACTIVITY;')
    return text


def patch_java_sources(text):
    anchor = '  "java/src/org/chromium/chrome/browser/tab/ArgonBackgroundPlaybackVisibility.java",'
    return insert(text, anchor, ''.join('\n  "java/src/org/chromium/chrome/browser/exit/'
        + name + '.java",' for name in ('ArgonExitFlow', 'ArgonExitStore', 'ArgonExitBridge',
                                       'ArgonExitCoordinator', 'ArgonExitSettings', 'ArgonExitBackgroundTask'))
        + '\n  "java/src/org/chromium/chrome/browser/tab/ArgonExitTabHelper.java",')


def patch_resources(text):
    return insert(text, '  "java/res/layout/argon_background_domain_card.xml",', '''
  "java/res/values/argon_exit_strings.xml",
  "java/res/values-ru/argon_exit_strings.xml",
  "java/res/drawable/argon_exit.xml",''')


def patch_jni(text):
    return insert(text,
        '      "//titanium/chromium_src/chrome/android/java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",',
        '\n      "//titanium/chromium_src/chrome/android/java/src/org/chromium/chrome/browser/exit/ArgonExitBridge.java",')


def patch_native_sources(text):
    return insert(text, '  "//chrome/browser/android/argon_certificate_domains_settings.cc",',
                  '\n  "//chrome/browser/android/argon_exit.cc",')


def patch_native_deps(text):
    return insert(text, '  "//chrome/android:chrome_jni_headers",', '''
  "//chrome/browser/browsing_data",
  "//components/download/public/common:public",
  "//components/history/core/common",
  "//components/services/storage/public/mojom",
  "//extensions/browser",
  "//extensions/common",''')


def patch_preferences(text):
    roots = list(re.finditer(r'<PreferenceScreen\b[^>]*>', text))
    if len(roots) != 1:
        raise ValueError('Expected one privacy PreferenceScreen')
    before = roots[0][0]
    return insert(text, before, '''
    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_clear_on_exit"
        android:title="@string/argon_exit_settings_title"
        android:summary="@string/argon_exit_settings_summary"
        android:fragment="org.chromium.chrome.browser.exit.ArgonExitSettings" />
''')


TRANSFORMS = dict(zip(INPUT_PATHS, (
    patch_activity, patch_quit, patch_tabbed_activity, patch_controller, patch_tab, patch_launch,
    patch_background_tasks, patch_menu, patch_lifetime, patch_tab_native,
    patch_android_gn, patch_content_client, patch_content_client_source,
    patch_chrome_client, patch_chrome_client_source, patch_renderer,
    patch_frame_manager_header, patch_frame_manager, patch_navigation_request,
    patch_jni, patch_java_sources, patch_resources, patch_native_sources, patch_native_deps,
    patch_preferences,
)))


def apply(src):
    writes = {src / path: transform((src / path).read_text())
              for path, transform in TRANSFORMS.items()}
    for source in (ROOT / 'chromium_overlay').rglob('*'):
        if not source.is_file() or not ('/exit/' in source.as_posix()
                or source.name in ('argon_exit.cc', 'argon_exit.h', 'argon_exit.xml',
                                   'argon_exit_strings.xml', 'ArgonExitTabHelper.java')):
            continue
        target = src / source.relative_to(ROOT / 'chromium_overlay')
        value = source.read_text()
        if target.exists() and target.read_text() != value:
            raise ValueError(f'Unexpected exit overlay: {target}')
        writes[target] = value
    for target, value in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', required=True, type=Path)
    apply(parser.parse_args().chromium_src)
