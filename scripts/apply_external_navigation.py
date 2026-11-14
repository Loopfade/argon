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
PRIVACY_PREFERENCES = Path("chrome/android/java/res/xml/privacy_preferences.xml")
JAVA_SOURCES = Path("titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni")
RESOURCES = Path("titanium/chromium_src/chrome/android/chrome_app_java_resources_ext_sources.gni")
INPUT_PATHS = (DELEGATE, HANDLER, CHROME_DELEGATE, MAIN_PREFERENCES, PRIVACY_PREFERENCES,
               JAVA_SOURCES, RESOURCES)
CUSTOM_TAB = Path("chrome/android/java/src/org/chromium/chrome/browser/customtabs/CustomTabDelegateFactory.java")
PRIVACY_SETTINGS = Path("chrome/android/java/src/org/chromium/chrome/browser/privacy/settings/PrivacySettings.java")
SEARCH_PACKAGE = Path("components/browser_ui/settings/android/java/src/org/chromium/components/browser_ui/settings/search")
PREFERENCE_PARSER = SEARCH_PACKAGE / "PreferenceParser.java"
SEARCH_INDEX = SEARCH_PACKAGE / "SettingsIndexData.java"
SEARCH_REGISTRY = Path("chrome/android/java/src/org/chromium/chrome/browser/settings/search/SearchIndexProviderRegistry.java")
API_PATHS = (COMPONENT / "ExternalNavigationParams.java", CUSTOM_TAB, PRIVACY_SETTINGS,
             PREFERENCE_PARSER, SEARCH_INDEX, SEARCH_REGISTRY)

LEGACY_MENU_ENTRY = '''    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_external_navigation"
        android:order="25"
        android:title="@string/argon_external_title"
        android:summary="@string/argon_external_menu_summary"
        android:fragment="org.chromium.chrome.browser.externalnav.ArgonExternalNavigationSettings" />
'''
SECURITY_SECTION = '''    <org.chromium.components.browser_ui.settings.ChromeBasePreferenceCategory
        android:key="security_section"
      android:title="@string/security_section_title" />'''
SECURITY_MENU_ENTRY = '''    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_external_navigation"
        android:title="@string/argon_external_title"
        android:summary="@string/argon_external_menu_summary"
        app:keywords="@string/argon_external_search_keywords"
        android:fragment="org.chromium.chrome.browser.externalnav.ArgonExternalNavigationSettings" />'''

USER_GUARD = '''        // ARGON: apply the user's destination-site policy before any dispatch or fallback.
        if (mDelegate.shouldBlockExternalNavigationForUser(params.getUrl(), targetIntent)) {
            if (UrlUtilities.isHttpOrHttps(params.getUrl())) {
                return OverrideUrlLoadingResult.forNoOverride();
            }
            // A fallback is only a web navigation here, never a WebAPK or market launch.
            if (!browserFallbackUrl.isEmpty()) {
                return OverrideUrlLoadingResult.forNavigateTab(browserFallbackUrl, params);
            }
            var data = targetIntent.getData();
            if (data != null) {
                GURL webUrl = new GURL(data.toString());
                if (webUrl.isValid() && UrlUtilities.isHttpOrHttps(webUrl)) {
                    return OverrideUrlLoadingResult.forNavigateTab(webUrl, params);
                }
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

FALLBACK_GUARD = '''        // ARGON: a fallback is a new destination, not an inherited permission.
        if (canLaunchExternalFallback
                && mDelegate.shouldBlockExternalNavigationForUser(browserFallbackUrl,
                        new Intent(Intent.ACTION_VIEW, Uri.parse(browserFallbackUrl.getSpec())))) {
            canLaunchExternalFallback = false;
        }
'''

MARKET_GUARD = '''        // ARGON: installing a missing app must also respect the store site's permission.
        GURL storeUrl = fallbackUrl.isEmpty()
                ? new GURL("https://play.google.com/store/apps/details?id="
                        + Uri.encode(packageName == null ? "" : packageName))
                : fallbackUrl;
        if (mDelegate.shouldBlockExternalNavigationForUser(storeUrl,
                new Intent(Intent.ACTION_VIEW, Uri.parse(storeUrl.getSpec())))) {
            return OverrideUrlLoadingResult.forNavigateTab(storeUrl, params);
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
    text = replace_once(text, anchor, anchor + "\n" + USER_GUARD)
    anchor = "        if (canLaunchExternalFallback) {\n"
    text = replace_once(text, anchor, FALLBACK_GUARD + "\n" + anchor)
    anchor = '        Uri.Builder builder = new Uri.Builder().scheme("market").authority("details");'
    return replace_once(text, anchor, MARKET_GUARD + "\n" + anchor)


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
    anchor = ('    <org.chromium.components.browser_ui.settings.ChromeBasePreference\n'
              '        android:fragment="org.chromium.chrome.browser.homepage.settings.HomepageSettings"')
    if text.count(anchor) != 1:
        raise ValueError("External navigation main preferences anchor changed")
    if 'android:key="argon_external_navigation"' in text:
        if text.count(LEGACY_MENU_ENTRY) != 1:
            raise ValueError("Review the previous external navigation menu entry")
        text = text.replace(LEGACY_MENU_ENTRY, "")
    return text


def patch_privacy_menu(text):
    return replace_once(text, SECURITY_SECTION, SECURITY_SECTION + "\n" + SECURITY_MENU_ENTRY)


def patch_java_sources(text):
    anchor = "chrome_java_ext_rel_path_sources = ["
    block = "\n".join('  "java/src/org/chromium/chrome/browser/externalnav/ArgonExternalNavigation'
                      + name + '.java",' for name in ("Policy", "Settings"))
    return replace_once(text, anchor, anchor + "\n" + block)


def patch_resources(text):
    anchor = "chrome_app_java_resources_ext_rel_path_sources = ["
    block = "\n".join('  "java/res/' + folder + '/argon_external_navigation_strings.xml",'
                      for folder in ("values", "values-ru"))
    return replace_once(text, anchor, anchor + "\n" + block)


TRANSFORMS = dict(zip(INPUT_PATHS, (patch_delegate, patch_handler, patch_chrome_delegate,
                                  patch_menu, patch_privacy_menu, patch_java_sources, patch_resources)))


def apply(src):
    # Validate every anchor before writing any file.
    updates = {path: transform((src / path).read_text()) for path, transform in TRANSFORMS.items()}
    for path, text in updates.items():
        (src / path).write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src.resolve())
