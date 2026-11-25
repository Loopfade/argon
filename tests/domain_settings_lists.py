"""Replay real domain-list builders against Chromium's positional card styling.

Android layout scheduling, JNI storage and theme dimensions use host adapters.
The three list builders, visibility traversal, style calculation and notification
bridge are production code. This does not render an APK.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from external_navigation_flow import ADAPTERS, resources_java
from test_background_playback_settings import ADAPTERS as BACKGROUND_ADAPTERS

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java/src"
FIXTURES = ROOT / "tests/fixtures/settings"
PREFIX = "org/chromium/chrome/browser/"
CERTIFICATE = PREFIX + "privacy/settings/ArgonCertificateDomainsSettings.java"
BACKGROUND = PREFIX + "media/ArgonBackgroundPlaybackSettings.java"
EXTERNAL = PREFIX + "externalnav/ArgonExternalNavigationSettings.java"
BASE = PREFIX + "settings/ChromeBaseSettingsFragment.java"
UPSTREAM_BASE = Path("chrome/browser/settings/android/java/src") / BASE
CONTROLLER = Path("components/browser_ui/widget/android/java/src/org/chromium/"
                  "components/browser_ui/widget/containment/ContainmentItemController.java")
UTILS = Path("components/browser_ui/settings/android/java/src/org/chromium/"
             "components/browser_ui/settings/SettingsUtils.java")
UPSTREAM_PATHS = (UPSTREAM_BASE, CONTROLLER, UTILS)
CONTROLLER_METHODS = (
    "public ArrayList<ContainerStyle> generatePreferenceStyles(",
    "private ContainerStyle getPreferenceStyleForPosition(",
    "private boolean isCustomStyledPreference(",
    "private void addStandardStyling(",
    "public void addCustomStyling(",
    "public void addPositionBasedStyling(",
)
UTIL_METHODS = ("public static ArrayList<Preference> getVisiblePreferences(",
                "private static void addVisiblePreferences(")
NOTIFY = "protected void notifyPreferencesUpdated()"


def method(source, signature, indent=4):
    marker = " " * indent + signature
    if source.count(marker) != 1:
        raise ValueError(f"Expected one production method: {signature}")
    start = source.index(marker)
    end = source.index("\n" + " " * indent + "}", start) + indent + 2
    return source[start:end] + "\n"


def certificate_list_source(source):
    """Keep the actual list-building code; certificate/dialog services are adapters."""
    production = "\n".join(method(source, name) for name in (
        "public void onCreatePreferences(", "public void onResume()",
        "private void addBuiltInZone(", "private void refreshDomains()",
        "private static String displayDomain(", "private String displayRule("))
    constructor = method(source, "DomainPreference(String domain, List<String> rules)", 8)
    return '''package org.chromium.chrome.browser.privacy.settings;
import android.os.Bundle;
import androidx.preference.*;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.settings.ChromeBaseSettingsFragment;
import org.chromium.base.supplier.*;
import org.chromium.components.url_formatter.*;
import org.chromium.url.GURL;
import java.util.*;
public final class ArgonCertificateDomainsSettings extends ChromeBaseSettingsFragment {
  private static final String[] ZONE_LABELS = {".ru", ".рф", ".su"};
  private final SettableMonotonicObservableSupplier<String> mPageTitle = ObservableSuppliers.createMonotonic();
  private final List<SwitchPreferenceCompat> mBuiltInZones = new ArrayList<>();
  private Preference mExplanation;
  private PreferenceCategory mAdditionalDomainsCategory;
  public Object getProfile() { return this; }
  public MonotonicObservableSupplier<String> getPageTitle() { return mPageTitle; }
  public int getAnimationType() { return 1; }
  private void showAddDomainDialog() {}
  private void showDomainDetails(String domain) {}
  private final class ExplanationPreference extends Preference {
    ExplanationPreference() { super(requireContext()); }
  }
  private final class DomainPreference extends Preference {
    private final String mDomain;
    private final boolean mCovered;
''' + constructor + "\n  }\n" + production + "\n}\n"


def compile_lists(directory, *, legacy=False, upstream=None):
    fixture = (FIXTURES / "containment_methods.java.in").read_text()
    upstream = upstream or {path: fixture for path in UPSTREAM_PATHS}
    adapters = dict(ADAPTERS)
    # Reuse the existing editor services. Only JNI storage is substituted for CA.
    store = PREFIX + "media/ArgonBackgroundPlaybackStore.java"
    adapters[store] = BACKGROUND_ADAPTERS[store]
    adapters["androidx/preference/PreferenceGroup.java"] = '''package androidx.preference;
public class PreferenceGroup extends Preference {
  public final java.util.List<Preference> rows = new java.util.ArrayList<>();
  public PreferenceGroup(android.content.Context c) { super(c); }
  public void addPreference(Preference p) { rows.add(p); }
  public void removeAll() { rows.clear(); }
  public int getPreferenceCount() { return rows.size(); }
  public Preference getPreference(int index) { return rows.get(index); }
}'''
    adapters["androidx/preference/PreferenceScreen.java"] = '''package androidx.preference;
public class PreferenceScreen extends PreferenceGroup {
  public PreferenceScreen(android.content.Context c) { super(c); }
}'''
    preference = "androidx/preference/Preference.java"
    adapters[preference] = adapters[preference].replace(
        "  public void bind()", "  public boolean isVisible() { return true; }\n  public void bind()")
    bridge = method(upstream[UPSTREAM_BASE], NOTIFY)
    adapters[BASE] = adapters[BASE].replace(
        "  protected void notifyPreferencesUpdated() {}", bridge + '''
  private DomainListFlow.UpdateObserver mPreferenceUpdateObserver;
  public void setPreferenceUpdateObserver(DomainListFlow.UpdateObserver observer) {
    mPreferenceUpdateObserver = observer;
  }
''')
    cert_jni = (FIXTURES / "CertificateDomainsJni.java.in").read_text()
    adapters[PREFIX + "privacy/settings/ArgonCertificateDomainsSettingsJni.java"] = cert_jni
    production = {name: (OVERLAY / name).read_text() for name in (BACKGROUND, EXTERNAL)}
    production[CERTIFICATE] = certificate_list_source((OVERLAY / CERTIFICATE).read_text())
    for name in (PREFIX + "media/ArgonBackgroundPlaybackPolicy.java",
                 PREFIX + "externalnav/ArgonExternalNavigationPolicy.java"):
        production[name] = (OVERLAY / name).read_text()
    if legacy:
        production = {name: source.replace("notifyPreferencesUpdated();", "")
                      for name, source in production.items()}
    flow = (FIXTURES / "DomainListFlow.java.in").read_text()
    flow = flow.replace("@CONTROLLER_METHODS@", "\n".join(
        method(upstream[CONTROLLER], name) for name in CONTROLLER_METHODS))
    flow = flow.replace("@VISIBLE_METHODS@", "\n".join(
        method(upstream[UTILS], name) for name in UTIL_METHODS))
    adapters[PREFIX + "settings/DomainListFlow.java"] = flow
    adapters.update(production)
    adapters["org/chromium/chrome/R.java"] = resources_java(list(adapters.values()))
    for name, source in adapters.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
    result = subprocess.run([*compiler, "-d", str(directory),
        *map(str, directory.rglob("*.java"))], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stdout + result.stderr)
    return directory


def run_lists(directory, kind):
    return subprocess.run(["java", "-ea", "-cp", str(directory),
        "org.chromium.chrome.browser.settings.DomainListFlow", kind],
        capture_output=True, text=True)


def verify_upstream(src):
    fixture = (FIXTURES / "containment_methods.java.in").read_text()
    upstream = {path: (src / path).read_text() for path in UPSTREAM_PATHS}
    for path, names in ((UPSTREAM_BASE, (NOTIFY,)), (CONTROLLER, CONTROLLER_METHODS),
                        (UTILS, UTIL_METHODS)):
        for name in names:
            if method(upstream[path], name) != method(fixture, name):
                raise ValueError(f"Review the pinned domain-list styling contract: {path}: {name}")
    with tempfile.TemporaryDirectory(prefix="argon-domain-lists-") as temporary:
        directory = compile_lists(Path(temporary), upstream=upstream)
        for kind in ("certificates", "background", "external"):
            result = run_lists(directory, kind)
            if result.returncode:
                raise ValueError(result.stdout + result.stderr)
    return "PASS: all three dynamic domain lists refresh Chromium card styling after growth and removal"
