"""Run the patched PWA, window selection and extension recovery methods on the host."""
from pathlib import Path
import subprocess
import tempfile

from apply_browsing_data_period import method_source

FIXTURES = Path(__file__).resolve().parent / "fixtures/android"
CHROME_JAVA = Path("chrome/android/java/src/org/chromium/chrome/browser")
STATUS_BAR_PROVIDER = CHROME_JAVA / "customtabs/CustomTabStatusBarColorProvider.java"
APPEARANCE_SETTINGS = CHROME_JAVA / "appearance/settings/AppearanceSettingsFragment.java"
MULTI_WINDOW_UTILS = CHROME_JAVA / "multiwindow/MultiWindowUtils.java"
EXTENSION_SERVICE = Path("chrome/browser/extensions/extension_service.cc")
EXTENSION_REGISTRAR = Path("extensions/browser/extension_registrar.cc")
UNPACKED_INSTALLER = Path("extensions/browser/unpacked_installer.cc")
PAGE_IMPL = Path("content/browser/renderer_host/page_impl.cc")
WEB_CONTENTS_ANDROID = Path("content/browser/web_contents/web_contents_android.cc")


def methods(src, path, *signatures):
    source = (src / path).read_text()
    return "\n\n".join(method_source(source, signature) for signature in signatures)


def verify_upstream(src):
    window_methods = methods(
        src, MULTI_WINDOW_UTILS,
        "    public static Set<Integer> getUsableInstanceIds(",
        "    public static int getInstanceIdForViewIntent()",
        "    public static int getLastAccessedWindowId()",
        "static int getLastAccessedWindowIdExcludingSelf(",
        "static int getLastRegularWindowForIncognitoSwitch(",
        "    private static Set<Integer> getUsableRegularWindows(",
        "    static boolean isRestorableInstance(",
        "    private static int getLastAccessedWindowIdInternal(",
    )
    status_bar_method = methods(src, STATUS_BAR_PROVIDER, "    int getBaseStatusBarColor(")
    swipe_method = methods(src, APPEARANCE_SETTINGS, "    private void initTabSwitcherSwipePref(")
    host_method = methods(
        src, EXTENSION_SERVICE, "void ExtensionService::OnExtensionHostRenderProcessGone(",
    )
    registrar_methods = methods(
        src, EXTENSION_REGISTRAR,
        "void ExtensionRegistrar::ReloadExtension(",
        "void ExtensionRegistrar::ScheduleCrashedExtensionReload(",
        "bool ExtensionRegistrar::IsCurrentCrashRecovery(",
        "void ExtensionRegistrar::ClearCrashRecovery(",
        "void ExtensionRegistrar::ReloadCrashedExtension(",
    )
    install = methods(src, UNPACKED_INSTALLER, "void UnpackedInstaller::InstallExtension(")
    guard, authorization, _ = install.partition("  // Force file access")
    if not authorization:
        raise ValueError("Unpacked installation no longer has the reviewed authorization gate")
    page_method = methods(src, PAGE_IMPL, "void PageImpl::SetAutoDarkenedBackgroundColor(")
    android_method = methods(
        src, WEB_CONTENTS_ANDROID,
        "int32_t WebContentsAndroid::GetAutoDarkenedPageBackgroundColor(",
    )

    with tempfile.TemporaryDirectory(prefix="argon-android-regressions-") as directory:
        directory = Path(directory)
        java = directory / "UiRegressionFlow.java"
        java.write_text(
            (FIXTURES / "UiRegressionFlow.java.in").read_text()
            .replace("/* STATUS_BAR_METHOD */", status_bar_method)
            .replace("/* WINDOW_METHODS */", window_methods)
            .replace("/* SWIPE_METHOD */", swipe_method)
        )
        subprocess.run(["java", "-ea", str(java)], check=True)

        fixtures = {
            "extension_recovery": {
                "HOST_METHOD": host_method,
                "REGISTRAR_METHODS": registrar_methods,
                "INSTALL_GUARD": guard.replace(
                    "void UnpackedInstaller::InstallExtension()", "void InstallExtension()",
                ),
            },
            "pwa_color": {
                "PAGE_METHOD": page_method,
                "ANDROID_METHOD": android_method,
            },
        }
        for name, replacements in fixtures.items():
            cpp = directory / f"{name}.cc"
            source = (FIXTURES / f"{name}.cc.in").read_text()
            for token, method in replacements.items():
                source = source.replace(f"/* {token} */", method)
            cpp.write_text(source)
            binary = directory / name
            subprocess.run(["c++", "-std=c++20", str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    return "PASS: PWA status bar, legacy windows, toolbar swipe preference and extension recovery"
