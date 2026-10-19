#!/usr/bin/env python3
"""Apply the experimental user-configured playback mode after the Argon overlay."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = Path("chrome/android/java/src/org/chromium/chrome/browser/ChromeTabbedActivity.java")
TAB_IMPL = Path("chrome/android/java/src/org/chromium/chrome/browser/tab/TabImpl.java")
MAIN_PREFERENCES = Path("chrome/android/java/res/xml/main_preferences.xml")
LOCALES = Path("build/config/locales.gni")
JAVA_SOURCES = Path("titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni")
RESOURCES = Path("titanium/chromium_src/chrome/android/chrome_app_java_resources_ext_sources.gni")
CHROME_ANDROID = Path("chrome/android/BUILD.gn")
CHROME_PAKS = Path("chrome/chrome_paks.gni")
APK_TEMPLATE = Path("chrome/android/chrome_public_apk_tmpl.gni")
BUNDLE_TEMPLATE = Path("chrome/android/modules/chrome_bundle_tmpl.gni")
UI_BASE = Path("ui/base/BUILD.gn")
INPUT_PATHS = (ACTIVITY, TAB_IMPL, MAIN_PREFERENCES, LOCALES, JAVA_SOURCES, RESOURCES,
               CHROME_ANDROID, CHROME_PAKS, APK_TEMPLATE, BUNDLE_TEMPLATE, UI_BASE)
PACKAGE = "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackController"

# Pinned TabImpl's decision, also compiled by the host regression with window
# services substituted. Upstream changes require review rather than a partial hook.
TAB_VISIBILITY_METHOD = '''    private void updateWebContentsVisibility() {
        var webContents = getWebContents();
        if (webContents == null) return;

        boolean isOffscreenRendering = mIsOffscreenRenderingSupplier.get();
        if (mIsHidden && !isOffscreenRendering) {
            webContents.updateWebContentsVisibility(Visibility.HIDDEN);
        } else if (!mIsDetachedFromActivity
                && !isOffscreenRendering
                && assumeNonNull(mWindowAndroid).getOcclusionSupplier().get()) {
            // If we are not attached to a window, occlusion does not make sense.
            webContents.updateWebContentsVisibility(Visibility.OCCLUDED);
        } else {
            webContents.updateWebContentsVisibility(Visibility.VISIBLE);
        }
    }'''

# Reviewed upstream mapping, restored when upgrading an older prepared overlay.
ANDROID_LOCALE_MAPPING = '''# List for Android locale names in .xml exports. Note: needs to stay in sync
# with |ToAndroidLocaleName| in build/android/gyp/util/resource_utils.py.
if (is_android) {
  #  - add r: (e.g. zh-HK -> zh-rHK )
  android_bundle_locales_as_resources = []
  foreach(_locale, platform_pak_locales) {
    android_bundle_locales_as_resources +=
        [ string_replace(_locale, "-", "-r") ]
  }

  #  - remove en-US
  #  - swap: (he, id, en-419, fil) -> (iw, in, es-rUS, tl)
  #  - sr-rLatn -> -b+sr+Latn
  android_bundle_locales_as_resources -= [
    "en-rUS",
    "es-r419",
    "fil",
    "he",
    "id",
    "sr-rLatn",
  ]
  android_bundle_locales_as_resources += [
    "b+sr+Latn",
    "es-rUS",
    "in",
    "iw",
    "tl",
  ]
}

'''


def replace_once(text, anchor, replacement):
    if replacement in text:
        return text
    if text.count(anchor) != 1:
        raise ValueError(f"Background playback patch anchor changed: {anchor[:100]}")
    return text.replace(anchor, replacement)


def patch_activity(text):
    old = f"{PACKAGE}.attach(this, assertNonNull(mTabModelSelector));"
    new = (f"{PACKAGE}.attach(this, assertNonNull(mTabModelSelector),\n"
           "                    mRootUiCoordinator.getTabObscuringHandler(),\n"
           "                    mRootUiCoordinator.getScrimManager().getScrimVisibilitySupplier());")
    if old in text:
        text = replace_once(text, old, new)
    text = replace_once(text, "            super.finishNativeInitialization();",
        "            super.finishNativeInitialization();\n"
        f"            {new}")
    return replace_once(text, "    public void onDestroyInternal() {",
        f"    public void onDestroyInternal() {{\n        {PACKAGE}.detach(this);")


def patch_menu(text):
    entry = '''    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_background_playback"
        android:order="24"
        android:title="@string/argon_background_title"
        android:fragment="org.chromium.chrome.browser.media.ArgonBackgroundPlaybackSettings" />
'''
    return replace_once(text, "</PreferenceScreen>", entry + "</PreferenceScreen>")


def patch_locales(text):
    start = "# List for Android locale names in .xml exports."
    end = "locales_without_pseudolocales = platform_pak_locales - pseudolocales"
    replacement = ANDROID_LOCALE_MAPPING + '''# ARGON_ANDROID_UI_LOCALES: package Russian and English only.
# Keep Chromium's source catalogs, extended locales and pseudolocales intact.
argon_android_ui_locales = [ "en-US", "ru" ]

'''
    if replacement in text:
        return text
    # Upgrade sources that already contain the earlier generated locale block.
    marker = "# ARGON_ANDROID_UI_LOCALES: package Russian and English only."
    if marker in text:
        if start in text:
            raise ValueError("Unexpected partially applied Android locale patch")
        start = marker
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("Android locale mapping changed upstream")
    before, rest = text.split(start)
    current, after = rest.split(end)
    if start != marker and start + current != ANDROID_LOCALE_MAPPING:
        raise ValueError("Upstream Android locale mapping changed; review the locale patch")
    return before + replacement + end + after


def patch_android_packaging(text):
    text = replace_once(text, 'import("//build/config/android/config.gni")',
                        'import("//build/config/android/config.gni")\n'
                        'import("//build/config/locales.gni")')
    text = replace_once(text, "    ui_locales = platform_pak_locales",
                        "    ui_locales = argon_android_ui_locales")
    return replace_once(text, "      foreach(_locale, platform_pak_locales) {",
                        "      foreach(_locale, argon_android_ui_locales) {")


def patch_paks(text):
    text = replace_once(text, "    input_locales = platform_pak_locales",
        "    if (is_android) {\n"
        "      input_locales = argon_android_ui_locales\n"
        "    } else {\n"
        "      input_locales = platform_pak_locales\n"
        "    }")
    return replace_once(text,
        "    if (is_mac) {\n      output_locales = locales_as_apple_outputs",
        "    if (is_android) {\n"
        "      output_locales = argon_android_ui_locales\n"
        "    } else if (is_mac) {\n      output_locales = locales_as_apple_outputs")


def patch_apk_template(text):
    return replace_once(text, "    aapt_locale_allowlist = platform_pak_locales",
                        "    aapt_locale_allowlist = argon_android_ui_locales")


def patch_bundle_template(text):
    text = replace_once(text, 'import("//build/config/android/config.gni")',
                        'import("//build/config/android/config.gni")\n'
                        'import("//build/config/locales.gni")')
    return replace_once(text,
        "    system_image_locale_allowlist = platform_pak_locales - extended_locales",
        "    system_image_locale_allowlist = argon_android_ui_locales")


def patch_native_locale_list(text):
    return replace_once(text, "locales_for_platform_list = platform_pak_locales",
        "if (is_android) {\n"
        "  locales_for_platform_list = argon_android_ui_locales\n"
        "} else {\n"
        "  locales_for_platform_list = platform_pak_locales\n"
        "}")


def patch_java_sources(text):
    anchor = '  "java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",'
    files = ["Policy", "Store", "Controller", "Settings"]
    block = "\n".join('  "java/src/org/chromium/chrome/browser/media/ArgonBackgroundPlayback'
                      + name + '.java",' for name in files)
    text = replace_once(text, anchor, anchor + "\n" + block)
    # Upgrade an already registered four-class overlay without duplicating it.
    return replace_once(text, block, block + '\n'
        '  "java/src/org/chromium/chrome/browser/tab/ArgonBackgroundPlaybackVisibility.java",')


def patch_resources(text):
    # Retire the old main-world JS shim, including on an already patched catalog.
    text = text.replace('  "java/res/raw/argon_background_playback.js",\n', '')
    anchor = '  "java/res/values-ru/argon_widget_strings.xml",'
    files = ["values/argon_background_playback_strings.xml",
             "values-ru/argon_background_playback_strings.xml",
             "layout/argon_background_rule_switch.xml"]
    block = "\n".join('  "java/res/' + path + '",' for path in files)
    text = replace_once(text, anchor, anchor + "\n" + block)
    # Upgrade the existing catalog without duplicating the previously registered resources.
    return replace_once(text, block, block + '\n  "java/res/layout/argon_background_overview.xml",')


def patch_tab_visibility(text):
    # Intercept before a hidden event reaches the page and pauses its player.
    # Keep the upstream hidden, occluded and offscreen-rendering decisions intact.
    method = TAB_VISIBILITY_METHOD
    for visibility in ("HIDDEN", "OCCLUDED", "VISIBLE"):
        call = f"webContents.updateWebContentsVisibility(Visibility.{visibility});"
        method = method.replace(call,
            "webContents.updateWebContentsVisibility(\n"
            f"                    {PACKAGE}.resolveVisibility(this, Visibility.{visibility}));")
    return replace_once(text, TAB_VISIBILITY_METHOD,
        "    /** Recompute native visibility after a background media permission changes. */\n"
        "    public void refreshBackgroundPlaybackVisibility() {\n"
        "        updateWebContentsVisibility();\n"
        "    }\n\n" + method)


def apply(src):
    transforms = {ACTIVITY: patch_activity, TAB_IMPL: patch_tab_visibility, MAIN_PREFERENCES: patch_menu,
                  LOCALES: patch_locales, JAVA_SOURCES: patch_java_sources,
                  RESOURCES: patch_resources, CHROME_ANDROID: patch_android_packaging,
                  CHROME_PAKS: patch_paks, APK_TEMPLATE: patch_apk_template,
                  BUNDLE_TEMPLATE: patch_bundle_template, UI_BASE: patch_native_locale_list}
    writes = {src / path: transform((src / path).read_text())
              for path, transform in transforms.items()}
    overlay = ROOT / "chromium_overlay"
    for source in overlay.rglob("*"):
        if source.is_file() and ("ArgonBackgroundPlayback" in source.name
                                 or "argon_background" in source.name):
            target = src / source.relative_to(overlay)
            value = source.read_text()
            if target.exists() and target.read_text() != value:
                raise ValueError(f"Unexpected background playback overlay: {target}")
            writes[target] = value
    for target, value in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path, required=True)
    apply(parser.parse_args().chromium_src.resolve())
