#!/usr/bin/env python3
"""Apply the whole-site policy before Chromium can dispatch a navigation to Android."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = Path("components/external_intents/android/java/src/org/chromium/components/external_intents")
DELEGATE = COMPONENT / "ExternalNavigationDelegate.java"
HANDLER = COMPONENT / "ExternalNavigationHandler.java"
CHROME_DELEGATE = Path("chrome/android/java/src/org/chromium/chrome/browser/externalnav/ExternalNavigationDelegateImpl.java")
MAIN_PREFERENCES = Path("chrome/android/java/res/xml/main_preferences.xml")
JAVA_SOURCES = Path("titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni")
RESOURCES = Path("titanium/chromium_src/chrome/android/chrome_app_java_resources_ext_sources.gni")
INPUT_PATHS = (DELEGATE, HANDLER, CHROME_DELEGATE, MAIN_PREFERENCES, JAVA_SOURCES, RESOURCES)
CUSTOM_TAB = Path("chrome/android/java/src/org/chromium/chrome/browser/customtabs/CustomTabDelegateFactory.java")
API_PATHS = (COMPONENT / "ExternalNavigationParams.java", CUSTOM_TAB)

USER_GUARD = '''        // ARGON: apply the user's destination-site policy before any dispatch or fallback.
        if (mDelegate.shouldBlockExternalNavigationForUser(params.getUrl(), targetIntent)) {
            if (UrlUtilities.isHttpOrHttps(params.getUrl())) {
                return OverrideUrlLoadingResult.forNoOverride();
            }
            // A fallback is only a web navigation here, never a WebAPK or market launch.
            if (!browserFallbackUrl.isEmpty()) {
                return OverrideUrlLoadingResult.forNavigateTab(browserFallbackUrl, params);
            }
            if (params.isMainFrame() && params.hasUserGesture()
                    && !params.isBackgroundTabNavigation() && mDelegate.isApplicationInForeground()) {
                mDelegate.onExternalNavigationBlocked();
            }
            // Consume an app-only link without replacing the current page with an error.
            // Post the completion so the interception client first observes the async result.
            params.onAsyncActionStarted();
            org.chromium.base.task.PostTask.postTask(
                    org.chromium.base.task.TaskTraits.UI_DEFAULT, () -> {
                        var callback = params.getRequiredAsyncActionTakenCallback();
                        if (callback != null) {
                            callback.onResult(AsyncActionTakenParams.forNoAction());
                        }
                    });
            return OverrideUrlLoadingResult.forAsyncAction();
        }
'''


def replace_once(text, anchor, replacement):
    if replacement in text:
        return text
    if text.count(anchor) != 1:
        raise ValueError(f"External navigation patch anchor changed: {anchor[:100]}")
    return text.replace(anchor, replacement)


def patch_delegate(text):
    anchor = "    /** Adds a window id to the intent, if necessary. */"
    return replace_once(text, anchor, '''    /** Additional user policy, shared by regular tabs, private tabs and custom tabs. */
    default boolean shouldBlockExternalNavigationForUser(GURL url, Intent intent) {
        return false;
    }

    /** Report a blocked app-only link that has no web fallback. */
    default void onExternalNavigationBlocked() {}

''' + anchor)


def patch_handler(text):
    anchor = "        targetIntent.removeExtra(EXTRA_BROWSER_FALLBACK_URL);\n"
    return replace_once(text, anchor, anchor + "\n" + USER_GUARD)


def patch_chrome_delegate(text):
    anchor = "    @Override\n    public boolean isApplicationInForeground() {"
    return replace_once(text, anchor, '''    @Override
    public boolean shouldBlockExternalNavigationForUser(GURL url, Intent intent) {
        return ArgonExternalNavigationPolicy.get().shouldBlock(url, intent);
    }

    @Override
    public void onExternalNavigationBlocked() {
        ArgonExternalNavigationPolicy.showBlockedMessage();
    }

''' + anchor)


def patch_menu(text):
    anchor = "</PreferenceScreen>"
    return replace_once(text, anchor, '''    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_external_navigation"
        android:order="25"
        android:title="@string/argon_external_title"
        android:summary="@string/argon_external_menu_summary"
        android:fragment="org.chromium.chrome.browser.externalnav.ArgonExternalNavigationSettings" />
''' + anchor)


def patch_java_sources(text):
    anchor = '  "java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",'
    block = "\n".join('  "java/src/org/chromium/chrome/browser/externalnav/ArgonExternalNavigation'
                      + name + '.java",' for name in ("Policy", "Settings"))
    return replace_once(text, anchor, anchor + "\n" + block)


def patch_resources(text):
    anchor = '  "java/res/values-ru/argon_widget_strings.xml",'
    block = "\n".join('  "java/res/' + folder + '/argon_external_navigation_strings.xml",'
                      for folder in ("values", "values-ru"))
    return replace_once(text, anchor, anchor + "\n" + block)


TRANSFORMS = dict(zip(INPUT_PATHS, (patch_delegate, patch_handler, patch_chrome_delegate,
                                  patch_menu, patch_java_sources, patch_resources)))


def apply(src):
    # Validate every anchor before writing any file.
    updates = {path: transform((src / path).read_text()) for path, transform in TRANSFORMS.items()}
    for path, text in updates.items():
        (src / path).write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src.resolve())
