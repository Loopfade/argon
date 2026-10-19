"""Exercise the production rule editor with the AndroidX row binding contract.

Platform adapters model Chromium's theme without the AndroidX seekbar and edit
dialog styles. A generic row has no seekbar_value, and a generic dialog lacks
android.R.id.edit. Resources are exposed through org.chromium.chrome.R,
as in chrome_java; no androidx.preference.R is provided on its compile classpath.
This is a host regression test, not an Android UI test.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java"
PACKAGE = "org/chromium/chrome/browser/media/"

ADAPTERS = {
    "android/content/Context.java": "package android.content; public class Context {}",
    "android/os/Bundle.java": '''package android.os;
public class Bundle extends java.util.HashMap<String, Object> {
  public String getString(String k, String d) { return (String) getOrDefault(k, d); }
  public int getInt(String k, int d) { return (Integer) getOrDefault(k, d); }
  public boolean getBoolean(String k, boolean d) { return (Boolean) getOrDefault(k, d); }
  public void putString(String k, String v) { put(k, v); }
  public void putInt(String k, int v) { put(k, v); }
  public void putBoolean(String k, boolean v) { put(k, v); }
}''',
    "android/text/InputType.java": '''package android.text;
public class InputType { public static final int TYPE_CLASS_TEXT = 1, TYPE_TEXT_VARIATION_URI = 16,
  TYPE_TEXT_FLAG_NO_SUGGESTIONS = 524288; }''',
    "android/view/inputmethod/EditorInfo.java": '''package android.view.inputmethod;
public class EditorInfo { public static final int IME_ACTION_DONE = 6, IME_FLAG_NO_PERSONALIZED_LEARNING = 16777216; }''',
    "android/view/View.java": '''package android.view;
public class View { public static final int VISIBLE = 0, GONE = 8;
  public interface OnClickListener { void onClick(View view); }
  private OnClickListener listener;
  public void setOnClickListener(OnClickListener value) { listener=value; }
  public boolean performClick() { if(listener==null) return false; listener.onClick(this); return true; }
  public void setVisibility(int value) {} }''',
    "android/widget/EditText.java": '''package android.widget;
public class EditText {
  private String text = ""; private int inputType, imeOptions; private boolean singleLine;
  public void setInputType(int value) { inputType = value; } public int getInputType() { return inputType; }
  public void setSingleLine(boolean v) { singleLine = v; } public boolean isSingleLine() { return singleLine; }
  public void setImeOptions(int v) { imeOptions=v; } public int getImeOptions() { return imeOptions; }
  public void setText(CharSequence value) { text = value.toString(); } public CharSequence getText() { return text; }
}''',
    "android/widget/Toast.java": '''package android.widget;
public class Toast { public static final int LENGTH_LONG = 1;
  public static Toast makeText(android.content.Context c, int message, int length) { return new Toast(); }
  public void show() {} }''',
    "android/R.java": "package android; public class R { public static class string { public static final int cancel = 1, ok = 2; } }",
    "android/app/AlertDialog.java": '''package android.app;
public class AlertDialog {
  public interface Click { void accept(Object dialog, int which); }
  public static class Builder {
    public Builder(android.content.Context c) {}
    public Builder setTitle(int v) { return this; }
    public Builder setTitle(String v) { return this; }
    public Builder setMessage(String v) { return this; }
    public Builder setNegativeButton(int v, Click c) { return this; }
    public Builder setPositiveButton(int v, Click c) { return this; }
    public void show() {}
  }
}''',
    "androidx/appcompat/widget/SwitchCompat.java": '''package androidx.appcompat.widget;
public class SwitchCompat extends android.view.View {
  public interface Change { void accept(Object button, boolean value); }
  public void setOnCheckedChangeListener(Change c) {}
  public void setChecked(boolean v) {} public void setContentDescription(String v) {}
}''',
    "androidx/preference/PreferenceViewHolder.java": '''package androidx.preference;
import org.chromium.chrome.R;
public class PreferenceViewHolder {
  private final java.util.Map<Integer, android.view.View> views = new java.util.HashMap<>();
  public PreferenceViewHolder(int layout) {
    if (layout == R.layout.preference_widget_seekbar_material) {
      views.put(R.id.seekbar, new android.view.View());
      views.put(R.id.seekbar_value, new android.view.View());
    }
    if (layout == R.layout.argon_background_overview) {
      views.put(R.id.switchWidget, new androidx.appcompat.widget.SwitchCompat());
      views.put(R.id.argon_background_help, new android.view.View());
    }
  }
  public android.view.View findViewById(int id) { return views.get(id); }
}''',
    "androidx/preference/Preference.java": '''package androidx.preference;
public class Preference {
  public interface Change { boolean accept(Preference p, Object value); }
  public interface Click { boolean accept(Preference p); }
  private Change change; private Click click; private int layout; private boolean enabled=true;
  private String key; private CharSequence summary;
  private int title; private String fragment; private final android.os.Bundle extras = new android.os.Bundle();
  public Preference(android.content.Context c) {}
  public void setKey(String v) { key=v; } public String getKey() { return key; } public void setPersistent(boolean v) {}
  public void setTitle(int v) { title = v; } public void setTitle(String v) {}
  public int getTitleId() { return title; }
  public void setSummary(int v) { summary=Integer.toString(v); } public void setSummary(String v) { summary=v; }
  public CharSequence getSummary() { return summary; } public void setIcon(int v) {}
  public void setSingleLineTitle(boolean v) {} public void setEnabled(boolean v) { enabled=v; }
  public boolean isEnabled() { return enabled; }
  public void setSelectable(boolean v) {} public void setWidgetLayoutResource(int v) {}
  public void setFragment(String v) { fragment = v; } public String getFragment() { return fragment; }
  public android.os.Bundle getExtras() { return extras; }
  public void setLayoutResource(int v) { layout = v; }
  public void setOnPreferenceChangeListener(Change c) { change = c; }
  public void setOnPreferenceClickListener(Click c) { click = c; }
  public boolean change(Object value) { return change.accept(this, value); }
  public boolean callChangeListener(Object value) { return change == null || change(value); }
  public void click() { click.accept(this); }
  public void onBindViewHolder(PreferenceViewHolder h) {}
  public void bind() { onBindViewHolder(new PreferenceViewHolder(layout)); }
}''',
    "androidx/preference/PreferenceScreen.java": '''package androidx.preference;
public class PreferenceScreen extends Preference {
  public final java.util.List<Preference> rows = new java.util.ArrayList<>();
  public PreferenceScreen(android.content.Context c) { super(c); }
  public void addPreference(Preference p) { rows.add(p); }
}''',
    "androidx/preference/PreferenceCategory.java": '''package androidx.preference;
public class PreferenceCategory extends PreferenceScreen {
  public PreferenceCategory(android.content.Context c) { super(c); }
}''',
    "androidx/preference/PreferenceManager.java": '''package androidx.preference;
public class PreferenceManager {
  public PreferenceScreen createPreferenceScreen(android.content.Context c) { return new PreferenceScreen(c); }
}''',
    "androidx/preference/EditTextPreference.java": '''package androidx.preference;
import org.chromium.chrome.R;
public class EditTextPreference extends Preference {
  public interface OnBindEditTextListener { void onBindEditText(android.widget.EditText input); }
  private String text = ""; private OnBindEditTextListener listener;
  private int dialogLayout, positive, negative = android.R.string.cancel;
  private android.widget.EditText input;
  public EditTextPreference(android.content.Context c) { super(c); }
  public void setDialogTitle(int v) {} public void setDialogMessage(int v) {}
  public void setDialogLayoutResource(int value) { dialogLayout = value; }
  public int getDialogLayoutResource() { return dialogLayout; }
  public void setPositiveButtonText(int value) { positive = value; }
  public void setNegativeButtonText(int value) { negative = value; }
  public void setText(String value) { text = value; } public String getText() { return text; }
  public void setOnBindEditTextListener(OnBindEditTextListener value) { listener = value; }
  public OnBindEditTextListener getOnBindEditTextListener() { return listener; }
  public android.widget.EditText openDialog() {
    input = dialogLayout == R.layout.preference_dialog_edittext ? new android.widget.EditText() : null;
    if (input == null) throw new IllegalStateException(
        "Dialog view must contain an EditText with id @android:id/edit");
    input.setText(text);
    if (listener != null) listener.onBindEditText(input);
    return input;
  }
  public void closeDialog(boolean accept) {
    if ((accept ? positive : negative) == 0) throw new AssertionError("Missing dialog button");
    if (accept && callChangeListener(input.getText().toString())) setText(input.getText().toString());
    input = null;
  }
}''',
    "androidx/preference/SwitchPreferenceCompat.java": '''package androidx.preference;
public class SwitchPreferenceCompat extends Preference {
  public SwitchPreferenceCompat(android.content.Context c) { super(c); }
  public void setChecked(boolean v) {}
}''',
    "androidx/preference/SeekBarPreference.java": '''package androidx.preference;
import org.chromium.chrome.R;
public class SeekBarPreference extends Preference {
  private int min, max, value; private boolean show;
  public SeekBarPreference(android.content.Context c) { super(c); }
  public void setMin(int v) { min = v; } public int getMin() { return min; }
  public void setMax(int v) { max = v; } public int getMax() { return max; }
  public void setValue(int v) { value = v; } public int getValue() { return value; }
  public void setAdjustable(boolean v) {} public void setShowSeekBarValue(boolean v) { show = v; }
  @Override public void onBindViewHolder(PreferenceViewHolder holder) {
    super.onBindViewHolder(holder);
    android.view.View valueView = (android.view.View) holder.findViewById(R.id.seekbar_value);
    valueView.setVisibility(show ? android.view.View.VISIBLE : android.view.View.GONE);
    if (holder.findViewById(R.id.seekbar) == null) throw new AssertionError("Missing seekbar");
  }
}''',
    "org/chromium/base/supplier/MonotonicObservableSupplier.java": '''package org.chromium.base.supplier;
public interface MonotonicObservableSupplier<T> {}''',
    "org/chromium/base/supplier/SettableMonotonicObservableSupplier.java": '''package org.chromium.base.supplier;
public class SettableMonotonicObservableSupplier<T> implements MonotonicObservableSupplier<T> {
  public void set(T v) {} }''',
    "org/chromium/base/supplier/ObservableSuppliers.java": '''package org.chromium.base.supplier;
public class ObservableSuppliers {
  public static <T> SettableMonotonicObservableSupplier<T> createMonotonic() {
    return new SettableMonotonicObservableSupplier<>();
  }
}''',
    "org/chromium/build/annotations/NullMarked.java": "package org.chromium.build.annotations; public @interface NullMarked {}",
    "org/chromium/build/annotations/Nullable.java": '''package org.chromium.build.annotations;
@java.lang.annotation.Target({java.lang.annotation.ElementType.TYPE_USE}) public @interface Nullable {}''',
    "org/chromium/components/browser_ui/settings/SettingsFragment.java": '''package org.chromium.components.browser_ui.settings;
public interface SettingsFragment { public @interface AnimationType { int PROPERTY = 1; } }''',
    "org/chromium/chrome/browser/settings/ChromeBaseSettingsFragment.java": '''package org.chromium.chrome.browser.settings;
public abstract class ChromeBaseSettingsFragment {
  private androidx.preference.PreferenceScreen screen; private android.os.Bundle args;
  public abstract void onCreatePreferences(android.os.Bundle saved, String key);
  public abstract org.chromium.base.supplier.MonotonicObservableSupplier<String> getPageTitle();
  public abstract int getAnimationType();
  public void onResume() {} public void onSaveInstanceState(android.os.Bundle out) {}
  public android.content.Context requireContext() { return new android.content.Context(); }
  public androidx.preference.PreferenceManager getPreferenceManager() { return new androidx.preference.PreferenceManager(); }
  public void setPreferenceScreen(androidx.preference.PreferenceScreen s) { screen = s; }
  public androidx.preference.PreferenceScreen getPreferenceScreen() { return screen; }
  public android.os.Bundle getArguments() { return args; } public void setArguments(android.os.Bundle a) { args = a; }
  public String getString(int id, Object... args) { return Integer.toString(id); }
  public Resources getResources() { return new Resources(); }
  public Activity requireActivity() { return new Activity(); }
  public static class Resources {
    public String getQuantityString(int id, int n, Object... args) { return Integer.toString(n); }
  }
  public static class Activity {
    public Activity getOnBackPressedDispatcher() { return this; } public void onBackPressed() {}
  }
}''',
    "org/chromium/url/GURL.java": '''package org.chromium.url;
public class GURL { public final String url; public GURL(String v) { url = v; } }''',
    "org/chromium/components/url_formatter/SchemeDisplay.java": '''package org.chromium.components.url_formatter;
public class SchemeDisplay { public static final int OMIT_HTTP_AND_HTTPS = 1; }''',
    "org/chromium/components/url_formatter/UrlFormatter.java": '''package org.chromium.components.url_formatter;
public class UrlFormatter {
  public static String formatUrlForSecurityDisplay(org.chromium.url.GURL u, int s) {
    return java.net.URI.create(u.url).getHost();
  }
}''',
    PACKAGE + "ArgonBackgroundPlaybackStore.java": '''package org.chromium.chrome.browser.media;
public class ArgonBackgroundPlaybackStore {
  private static final ArgonBackgroundPlaybackStore instance = new ArgonBackgroundPlaybackStore();
  public static final String ENABLED = "enabled";
  public final ArgonBackgroundPlaybackPolicy policy = new ArgonBackgroundPlaybackPolicy();
  public final Preferences preferences = new Preferences();
  public static ArgonBackgroundPlaybackStore get() { return instance; }
  public void reload() {}
  public void save(java.util.List<ArgonBackgroundPlaybackPolicy.Rule> rules) { policy.configure(policy.isEnabled(), rules); }
  public static String normalizeHost(String host) {
    host=host.trim().toLowerCase(java.util.Locale.ROOT); return host.isEmpty() ? null : host;
  }
  public static class Preferences {
    public Preferences edit() { return this; } public Preferences putBoolean(String k, boolean v) { return this; }
    public void apply() {}
  }
}''',
}


class BackgroundPlaybackSettingsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required for settings regression test")
    def test_add_and_edit_bind_and_save_with_chromium_preference_theme(self):
        resources = {"string": [], "plurals": [],
                     "layout": ["argon_background_rule_switch", "preference_widget_seekbar_material",
                                "preference_dialog_edittext", "argon_background_overview"],
                     "id": ["argon_background_rule_enabled", "seekbar", "seekbar_value",
                            "switchWidget", "argon_background_help"],
                     "drawable": ["argon_certificate_add"]}
        for node in ET.parse(OVERLAY / "res/values/argon_background_playback_strings.xml").getroot():
            resources[node.tag].append(node.attrib["name"])
        r_source = "package org.chromium.chrome; public class R {\n"
        for kind, names in resources.items():
            r_source += f"public static class {kind} {{\n"
            for number, name in enumerate(names, 100):
                r_source += f"public static final int {name} = {number};\n"
            r_source += "}\n"
        r_source += "}\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapters = dict(ADAPTERS, **{"org/chromium/chrome/R.java": r_source})
            for name, source in adapters.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            sources = list(root.rglob("*.java")) + [
                OVERLAY / "src" / PACKAGE / "ArgonBackgroundPlaybackSettings.java",
                OVERLAY / "src" / PACKAGE / "ArgonBackgroundPlaybackPolicy.java",
                ROOT / "tests/java/ArgonBackgroundPlaybackSettingsTest.java",
            ]
            compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
            result = subprocess.run([*compiler, "-d", directory, *map(str, sources)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(["java", "-cp", directory,
                                     "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackSettingsTest"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS: rule editor binding and saving", result.stdout)
