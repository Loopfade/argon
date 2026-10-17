#!/usr/bin/env python3
"""Apply the experimental user-configured playback mode after the Argon overlay."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVITY = Path("chrome/android/java/src/org/chromium/chrome/browser/ChromeTabbedActivity.java")
MAIN_PREFERENCES = Path("chrome/android/java/res/xml/main_preferences.xml")
LOCALES = Path("build/config/locales.gni")
JAVA_SOURCES = Path("titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni")
RESOURCES = Path("titanium/chromium_src/chrome/android/chrome_app_java_resources_ext_sources.gni")
INPUT_PATHS = (ACTIVITY, MAIN_PREFERENCES, LOCALES, JAVA_SOURCES, RESOURCES)
PACKAGE = "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackController"


def replace_once(text, anchor, replacement):
    if replacement in text:
        return text
    if text.count(anchor) != 1:
        raise ValueError(f"Background playback patch anchor changed: {anchor[:100]}")
    return text.replace(anchor, replacement)


def patch_activity(text):
    text = replace_once(text, "            super.finishNativeInitialization();",
        "            super.finishNativeInitialization();\n"
        f"            {PACKAGE}.attach(this, assertNonNull(mTabModelSelector));")
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
    replacement = '''# ARGON_ANDROID_UI_LOCALES: package Russian and English only.
# Keep source catalogs and website/translation language support intact.
if (is_android) {
  platform_pak_locales = [ "en-US", "ru" ]
  extended_locales = []
  # XTB catalogs must still exclude automatically generated pseudolocales.
  all_chrome_locales -= pseudolocales
  pseudolocales = []
  # en-US uses Android's unqualified values/ resources.
  android_bundle_locales_as_resources = [ "ru" ]
}

'''
    if replacement in text:
        return text
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("Android locale mapping changed upstream")
    before, rest = text.split(start)
    _, after = rest.split(end)
    return before + replacement + end + after


def patch_java_sources(text):
    anchor = '  "java/src/org/chromium/chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",'
    files = ["Policy", "Store", "Controller", "Settings"]
    block = "\n".join('  "java/src/org/chromium/chrome/browser/media/ArgonBackgroundPlayback'
                      + name + '.java",' for name in files)
    return replace_once(text, anchor, anchor + "\n" + block)


def patch_resources(text):
    anchor = '  "java/res/values-ru/argon_widget_strings.xml",'
    files = ["values/argon_background_playback_strings.xml",
             "values-ru/argon_background_playback_strings.xml",
             "layout/argon_background_rule_switch.xml", "raw/argon_background_playback.js"]
    block = "\n".join('  "java/res/' + path + '",' for path in files)
    return replace_once(text, anchor, anchor + "\n" + block)


def apply(src):
    transforms = {ACTIVITY: patch_activity, MAIN_PREFERENCES: patch_menu,
                  LOCALES: patch_locales, JAVA_SOURCES: patch_java_sources,
                  RESOURCES: patch_resources}
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
