#!/usr/bin/env python3
"""Run the production playback classes and visibility bridge with pinned AndroidX callbacks.

Android/Chromium services are deterministic host adapters, not an Android device.
The store, JSON serialization, editor, policy, controller, edit dialog and seekbar
callbacks are real implementations. No APK or prepared Docker image is needed.
"""
import concurrent.futures
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org/chromium/chrome/browser/media/"
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java"
CACHE = ROOT / ".build/settings-upstream"
PREFERENCE_URL = ("https://androidx.dev/snapshots/builds/16323643/artifacts/repository/"
                  "androidx/preference/preference/1.3.0-SNAPSHOT/"
                  "preference-1.3.0-20260911.204457-1")
DEPENDENCIES = {
    "preference.aar": (PREFERENCE_URL + ".aar",
                       "e9f817b424d5ac473f4cded8d3ed00b5c619db48ca844d8d95da422ffefd0507"),
    "preference-sources.jar": (PREFERENCE_URL + "-sources.jar",
                               "e38f39fbb6ebdf5031d74c38539216e5ce1584c3cb5b7af3883d70ce790ac33f"),
    # The standalone Android org.json implementation, rather than a fake store.
    "android-json.jar": (
        "https://repo.maven.apache.org/maven2/com/vaadin/external/google/android-json/"
        "0.0.20131108.vaadin1/android-json-0.0.20131108.vaadin1.jar",
        "dfb7bae2f404cfe0b72b4d23944698cb716b7665171812a0a4d0f5926c0fac79"),
}


def dependency(name):
    url, expected = DEPENDENCIES[name]
    path = CACHE / name
    data = path.read_bytes() if path.exists() else None
    if data is None or hashlib.sha256(data).hexdigest() != expected:
        # AndroidX snapshot downloads occasionally stall on GitHub-hosted runners.
        # Retain the SHA-256 pin and retry transient transport errors only.
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    data = response.read()
                break
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Unexpected dependency contents: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def verify_preference_pin():
    revision = json.loads((ROOT / "build-lock.json").read_text())["chromium_commit"]
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Expected a pinned Chromium commit")
    path = CACHE / f"preference-{revision}.README.chromium"
    if not path.exists():
        url = (f"https://raw.githubusercontent.com/chromium/chromium/{revision}/"
               "third_party/androidx/committed/libs/androidx_preference_preference/README.chromium")
        with urllib.request.urlopen(url, timeout=30) as response:
            metadata = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(metadata)
    pinned = re.search(r"^URL: (.+)$", path.read_text(), re.MULTILINE)
    if not pinned or pinned[1] != DEPENDENCIES["preference.aar"][0]:
        raise ValueError("Chromium's AndroidX Preference changed; review the playback UI integration fixture")
    api = CACHE / f"UrlUtilities-{revision}.java"
    if not api.exists():
        url = (f"https://raw.githubusercontent.com/chromium/chromium/{revision}/"
               "components/embedder_support/android/java/src/org/chromium/components/embedder_support/util/UrlUtilities.java")
        with urllib.request.urlopen(url, timeout=30) as response:
            api.write_bytes(response.read())
    if not re.search(r"public static @Nullable String getDomainAndRegistry\(\s*"
                     r"@Nullable String uri, boolean includePrivateRegistries\)", api.read_text()):
        raise ValueError("Chromium's native domain-validation API changed; review the playback store")


def load_adapters(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ADAPTERS


def resources():
    names = {"string": [], "plurals": [],
             "layout": ["argon_background_rule_switch", "preference_widget_seekbar_material",
                        "preference_dialog_edittext", "argon_background_overview",
                        "argon_background_domain_card"],
             "id": ["argon_background_rule_enabled", "seekbar", "seekbar_value",
                    "switchWidget", "argon_background_help", "argon_background_scope_group",
                    "argon_background_scope_summary", "argon_background_scope_overlaps"],
             "drawable": ["argon_certificate_add"]}
    for node in ET.parse(OVERLAY / "res/values/argon_background_playback_strings.xml").getroot():
        names[node.tag].append(node.attrib["name"])
    source = "package org.chromium.chrome; public class R {"
    for kind, entries in names.items():
        source += f"public static class {kind} {{" + "".join(
            f"public static final int {name} = {i};" for i, name in enumerate(entries, 100)) + "}"
    return source + "}"


def localized_resources():
    catalogs = [{node.attrib["name"]: node for node in ET.parse(
        OVERLAY / "res" / folder / "argon_background_playback_strings.xml").getroot()}
        for folder in ("values", "values-ru")]

    def literal(value):
        return json.dumps(value.replace("\\n", "\n").replace("\\'", "'"), ensure_ascii=False)

    source = """package org.chromium.chrome;
public class PlaybackTestResources {
  public static boolean russian;
  public static String getString(int id,Object... args) {
    String text;
    switch(id) {
"""
    for name, node in catalogs[0].items():
        if node.tag == "string":
            source += (f"case R.string.{name}: text=russian ? {literal(catalogs[1][name].text)}"
                       f" : {literal(node.text)}; break;\n")
    source += """default: throw new AssertionError("Unknown string resource: "+id);
    }
    return String.format(java.util.Locale.ROOT,text,args);
  }
  public static String getQuantityString(int id,int n,Object... args) {
    String quantity=n==1 ? "one" : "other";
    if(russian) quantity=n%10==1 && n%100!=11 ? "one" :
      n%10>=2 && n%10<=4 && (n%100<12 || n%100>14) ? "few" : "many";
    String text;
    switch(id) {
"""
    for name, node in catalogs[0].items():
        if node.tag != "plurals":
            continue
        english = {item.attrib["quantity"]: item.text for item in node}
        russian = {item.attrib["quantity"]: item.text for item in catalogs[1][name]}
        source += f"case R.plurals.{name}:\n"
        source += (f'if(!russian) text=n==1 ? {literal(english["one"])} : {literal(english["other"])};\n'
                   f'else text=quantity.equals("one") ? {literal(russian["one"])} : '
                   f'quantity.equals("few") ? {literal(russian["few"])} : {literal(russian["many"])}; break;\n')
    return source + """default: throw new AssertionError("Unknown plural resource: "+id);
    }
    return String.format(java.util.Locale.ROOT,text,args);
  }
}"""


def adapters():
    result = {**load_adapters("test_background_playback_controller"),
              **load_adapters("test_background_playback_settings")}
    del result[PACKAGE + "ArgonBackgroundPlaybackStore.java"]
    result.update({
        "org/chromium/chrome/R.java": resources(),
        "org/chromium/chrome/PlaybackTestResources.java": localized_resources(),
        "android/content/Context.java": '''package android.content;
public class Context {
  public static final int MODE_PRIVATE = 0;
  public static final String INPUT_METHOD_SERVICE = "input_method";
  public static final String ACCESSIBILITY_SERVICE = "accessibility";
  public final java.util.List<BroadcastReceiver> receivers = new java.util.ArrayList<>();
  public final android.os.PowerManager power = new android.os.PowerManager();
  public final android.app.KeyguardManager keyguard = new android.app.KeyguardManager();
  public final MemoryPreferences preferences = new MemoryPreferences();
  public SharedPreferences getSharedPreferences(String name, int mode) { return preferences; }
  public <T> T getSystemService(Class<T> type) {
    if (type == android.os.PowerManager.class) return type.cast(power);
    if (type == android.app.KeyguardManager.class) return type.cast(keyguard);
    return null;
  }
  public Object getSystemService(String name) {
    return ACCESSIBILITY_SERVICE.equals(name) ? new android.view.accessibility.AccessibilityManager()
      : new android.view.inputmethod.InputMethodManager();
  }
  public String getString(int id) { return org.chromium.chrome.PlaybackTestResources.getString(id); }
  public android.content.res.Resources getResources() { return new android.content.res.Resources(); }
  public void unregisterReceiver(BroadcastReceiver receiver) { receivers.remove(receiver); }
  public android.content.res.TypedArray obtainStyledAttributes(android.util.AttributeSet a, int[] ids, int s, int r) {
    return new android.content.res.TypedArray();
  }
}''',
        "android/content/SharedPreferences.java": '''package android.content;
public interface SharedPreferences {
  interface OnSharedPreferenceChangeListener { void onSharedPreferenceChanged(SharedPreferences p, String key); }
  interface Editor { Editor putString(String key, String value); Editor putBoolean(String key, boolean value); void apply(); }
  String getString(String key, String fallback); boolean getBoolean(String key, boolean fallback); Editor edit();
  void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener);
  void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener);
}''',
        "android/content/MemoryPreferences.java": '''package android.content;
public class MemoryPreferences implements SharedPreferences {
  public final java.util.Map<String,Object> values = new java.util.HashMap<>();
  public final java.util.List<OnSharedPreferenceChangeListener> listeners = new java.util.ArrayList<>();
  public int writes;
  public String getString(String key, String fallback) { return (String) values.getOrDefault(key, fallback); }
  public boolean getBoolean(String key, boolean fallback) { return (Boolean) values.getOrDefault(key, fallback); }
  public void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l) { listeners.add(l); }
  public void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l) { listeners.remove(l); }
  public Editor edit() { return new Editor() {
    private final java.util.Map<String,Object> pending = new java.util.HashMap<>();
    public Editor putString(String key, String value) { pending.put(key,value); return this; }
    public Editor putBoolean(String key, boolean value) { pending.put(key,value); return this; }
    public void apply() {
      var changed = new java.util.ArrayList<String>();
      for (var entry : pending.entrySet()) {
        if (!java.util.Objects.equals(values.get(entry.getKey()),entry.getValue())) changed.add(entry.getKey());
      }
      values.putAll(pending); writes++;
      for (String key : changed) for (var listener : java.util.List.copyOf(listeners))
        listener.onSharedPreferenceChanged(MemoryPreferences.this,key);
    }
  }; }
}''',
        "android/os/PowerManager.java": '''package android.os;
public class PowerManager { public boolean interactive = true; public boolean isInteractive() { return interactive; } }''',
        "android/app/KeyguardManager.java": '''package android.app;
public class KeyguardManager { public boolean locked; public boolean isKeyguardLocked() { return locked; } }''',
        "android/os/SystemClock.java": '''package android.os;
public class SystemClock { public static long now;
  public static long elapsedRealtime() { return now; } public static long currentThreadTimeMillis() { return now; } }''',
        "android/app/Activity.java": '''package android.app;
public class Activity extends android.content.Context {
  public boolean focused = true; public int backs;
  public final org.chromium.chrome.browser.tab.TabObscuringHandler obscuring = new org.chromium.chrome.browser.tab.TabObscuringHandler();
  public final org.chromium.base.supplier.NonNullObservableSupplier<Boolean> scrim = new org.chromium.base.supplier.NonNullObservableSupplier<>(false);
  public boolean hasWindowFocus() { return focused; }
  public Activity getOnBackPressedDispatcher() { return this; } public void onBackPressed() { backs++; }
}''',
        "androidx/appcompat/widget/SwitchCompat.java": '''package androidx.appcompat.widget;
public class SwitchCompat extends android.widget.CompoundButton {
  private OnCheckedChangeListener listener; private boolean checked;
  public void setOnCheckedChangeListener(OnCheckedChangeListener c) { listener=c; }
  public void setChecked(boolean value) { boolean changed=value!=checked; checked=value;
    if(changed && listener!=null) listener.onCheckedChanged(this,value); }
  public void toggle() { setChecked(!checked); }
  public void setTextOn(CharSequence text) {} public void setTextOff(CharSequence text) {}
  public boolean isChecked() { return checked; } public void setContentDescription(String text) {}
}''',
        "android/widget/Toast.java": '''package android.widget;
public class Toast { public static final int LENGTH_LONG = 1; public static int lastMessage;
  public static Toast makeText(android.content.Context c, int message, int length) { lastMessage=message; return new Toast(); }
  public void show() {} }''',
        "android/app/AlertDialog.java": '''package android.app;
public class AlertDialog {
  public interface Click { void accept(Object dialog, int which); }
  public static Click positive, negative;
  public static String lastMessage, lastTitle;
  public static class Builder {
    public Builder(android.content.Context c) {}
    public Builder setTitle(int v) { lastTitle=Integer.toString(v); return this; }
    public Builder setTitle(String v) { lastTitle=v; return this; }
    public Builder setMessage(String v) { lastMessage=v; return this; }
    public Builder setNegativeButton(int v, Click c) { negative=c; return this; }
    public Builder setPositiveButton(int v, Click c) { positive=c; return this; }
    public void show() {}
  }
}''',
        "org/chromium/components/embedder_support/util/UrlUtilities.java": '''package org.chromium.components.embedder_support.util;
// PSL/IP fixture boundaries only; native GURL/PSL is checked separately in Chromium.
public class UrlUtilities {
  public static String getDomainAndRegistry(String value,boolean includePrivate) {
    String host=new org.chromium.url.GURL(value).getHost();
    if(host.matches("[0-9.]+") || java.util.Set.of("co.uk","com.au","github.io","blogspot.com").contains(host)) return "";
    return host.contains(".") ? host : "";
  }
}''',
        "org/chromium/url/GURL.java": '''package org.chromium.url;
// Host adapter for these fixtures only; it does not replace native GURL/UTS46 testing.
public class GURL {
  public final String url; private String host="", scheme=""; private boolean valid;
  public GURL(String value) {
    url=value;
    try {
      java.net.URI uri=java.net.URI.create(value);
      scheme=uri.getScheme();
      String authority=uri.getRawAuthority();
      if (authority != null) {
        host=java.net.IDN.toASCII(authority).toLowerCase(java.util.Locale.ROOT);
        valid=!host.isEmpty() && !host.contains(" ");
      }
    } catch (IllegalArgumentException invalid) { }
  }
  public String getHost() { return host; } public String getScheme() { return scheme; }
  public boolean isValid() { return valid; }
}''',
        "org/chromium/components/url_formatter/UrlFormatter.java": '''package org.chromium.components.url_formatter;
public class UrlFormatter {
  public static String formatUrlForSecurityDisplay(org.chromium.url.GURL u, int scheme) { return u.getHost(); }
}''',
        "android/content/res/TypedArray.java": '''package android.content.res;
public class TypedArray { public int getInt(int i,int d) { return d; }
  public boolean getBoolean(int i,boolean d) { return d; } public void recycle() {} }''',
        "android/util/AttributeSet.java": "package android.util; public interface AttributeSet {}",
        "android/util/Log.java": "package android.util; public class Log { public static int e(String t,String m) { return 0; } }",
        "android/view/KeyEvent.java": '''package android.view;
public class KeyEvent { public static final int ACTION_DOWN=0, KEYCODE_DPAD_LEFT=1, KEYCODE_DPAD_RIGHT=2,
  KEYCODE_DPAD_CENTER=3, KEYCODE_ENTER=4; public int getAction() { return ACTION_DOWN; } }''',
        "android/view/View.java": '''package android.view;
public class View {
  public static final int VISIBLE=0, GONE=8;
  public interface OnClickListener { void onClick(View view); }
  private OnClickListener click; private int visibility;
  public void setOnClickListener(OnClickListener listener) { click=listener; }
  public boolean performClick() { if(click==null) return false; click.onClick(this); return true; }
  public interface OnKeyListener { boolean onKey(View v,int code,KeyEvent e); }
  private final java.util.Map<Integer,View> children=new java.util.HashMap<>();
  public void addView(int id,View view) { children.put(id,view); }
  @SuppressWarnings("unchecked") public <T extends View> T findViewById(int id) { return (T) children.get(id); }
  public void setVisibility(int v) { visibility=v; } public int getVisibility() { return visibility; }
  public void setEnabled(boolean v) {} public void setOnKeyListener(OnKeyListener l) {}
  public boolean requestFocus() { return true; } public boolean isFocused() { return true; }
  public void setSelection(int v) {} public boolean removeCallbacks(Runnable r) { return true; }
  public boolean postDelayed(Runnable r,long delay) { return true; }
  public android.content.Context getContext() { return org.chromium.base.ContextUtils.context; }
}''',
        "android/widget/TextView.java": '''package android.widget;
public class TextView extends android.view.View {
  private CharSequence text="";
  public void setText(CharSequence value) { text=value; }
  public CharSequence getText() { return text; }
}''',
        "android/widget/Checkable.java": '''package android.widget;
public interface Checkable { void setChecked(boolean value); boolean isChecked(); void toggle(); }''',
        "android/widget/CompoundButton.java": '''package android.widget;
public abstract class CompoundButton extends android.view.View implements Checkable {
  public interface OnCheckedChangeListener { void onCheckedChanged(CompoundButton button,boolean value); }
}''',
        "android/view/accessibility/AccessibilityManager.java": '''package android.view.accessibility;
public class AccessibilityManager { public boolean isEnabled() { return true; } }''',
        "android/text/TextUtils.java": '''package android.text;
public class TextUtils { public static boolean isEmpty(CharSequence text) { return text==null || text.length()==0; } }''',
        "androidx/core/content/res/TypedArrayUtils.java": '''package androidx.core.content.res;
public class TypedArrayUtils {
  public static String getString(android.content.res.TypedArray a,int index,int fallback) { return null; }
  public static boolean getBoolean(android.content.res.TypedArray a,int index,int fallback,boolean value) { return value; }
}''',
        "android/widget/SeekBar.java": '''package android.widget;
public class SeekBar extends android.view.View {
  private int progress, increment=1; private OnSeekBarChangeListener listener;
  public interface OnSeekBarChangeListener {
    void onProgressChanged(SeekBar bar,int value,boolean fromUser);
    void onStartTrackingTouch(SeekBar bar); void onStopTrackingTouch(SeekBar bar);
  }
  public void setOnSeekBarChangeListener(OnSeekBarChangeListener l) { listener=l; }
  public void setMax(int v) {} public void setKeyProgressIncrement(int v) { increment=v; }
  public int getKeyProgressIncrement() { return increment; } public int getProgress() { return progress; }
  public void setProgress(int v) { progress=v; if(listener!=null) listener.onProgressChanged(this,v,false); }
  public boolean onKeyDown(int c,android.view.KeyEvent e) { return true; }
  public void userProgress(int v) { listener.onStartTrackingTouch(this); progress=v;
    listener.onProgressChanged(this,v,true); listener.onStopTrackingTouch(this); }
}''',
        "android/os/Parcelable.java": '''package android.os;
public interface Parcelable { interface Creator<T> { T createFromParcel(Parcel p); T[] newArray(int n); } }''',
        "android/os/Parcel.java": '''package android.os;
public class Parcel { public int readInt() { return 0; } public void writeInt(int v) {} }''',
        "android/view/inputmethod/InputMethodManager.java": '''package android.view.inputmethod;
public class InputMethodManager { public boolean showSoftInput(android.view.View v,int flags) { return true; } }''',
        "androidx/annotation/RestrictTo.java": '''package androidx.annotation;
public @interface RestrictTo { Scope[] value(); public enum Scope { LIBRARY } }''',
        "androidx/preference/PreferenceDialogFragmentCompat.java": '''package androidx.preference;
public class PreferenceDialogFragmentCompat {
  protected static final String ARG_KEY="key"; private Preference preference;
  public void setPreference(Preference p) { preference=p; } protected Preference getPreference() { return preference; }
  public void setArguments(android.os.Bundle b) {} public void onCreate(android.os.Bundle b) {}
  public void onSaveInstanceState(android.os.Bundle b) {} protected void onBindDialogView(android.view.View v) {}
  protected boolean needInputMethod() { return false; } protected void scheduleShowSoftInput() {}
  public void onDialogClosed(boolean accept) {}
}''',
    })
    for annotation in ("NonNull", "Nullable"):
        result[f"org/jspecify/annotations/{annotation}.java"] = (
            "package org.jspecify.annotations; @java.lang.annotation.Target({java.lang.annotation.ElementType.TYPE_USE}) "
            f"public @interface {annotation} {{}}")
    result["android/R.java"] = result["android/R.java"].replace("public class R {",
        "public class R { public static class id { public static final int edit=16908291, summary=16908304; }")
    result["android/widget/EditText.java"] = result["android/widget/EditText.java"].replace(
        "public class EditText {", "public class EditText extends android.view.View {")
    result["android/os/Bundle.java"] = result["android/os/Bundle.java"].replace(
        "  public String getString", """
  public Bundle() {} public Bundle(int count) {}
  public CharSequence getCharSequence(String key) { return (CharSequence) get(key); }
  public void putCharSequence(String key,CharSequence value) { put(key,value); }
  public String getString""")
    result["org/chromium/chrome/browser/settings/ChromeBaseSettingsFragment.java"] = result[
        "org/chromium/chrome/browser/settings/ChromeBaseSettingsFragment.java"].replace(
            "  public Activity requireActivity() { return new Activity(); }",
            "  private final android.app.Activity activity=new android.app.Activity();\n"
            "  public android.app.Activity requireActivity() { return activity; }").replace(
            "return Integer.toString(id);", "return org.chromium.chrome.PlaybackTestResources.getString(id,args);").replace(
            "return Integer.toString(n);", "return org.chromium.chrome.PlaybackTestResources.getQuantityString(id,n,args);")
    result["org/chromium/chrome/browser/tab/TabImpl.java"] = result[
        "org/chromium/chrome/browser/tab/TabImpl.java"].replace(
            "  public boolean incognito, destroyed;", "  public boolean incognito,destroyed,interactable=true;\n"
            "  public int hash,group; public int hashCode() { return hash; }\n"
            "  public String url=\"https://video.example/\";").replace(
            "isUserInteractable() { return true; }", "isUserInteractable() { return interactable; }").replace(
            'new org.chromium.url.GURL("https://video.example/")', "new org.chromium.url.GURL(url)")
    # Frame-closure suspension freezes a pipeline; it does not pause the HTML
    # player. The session adapter records the separate, actual system pause.
    preference = result["androidx/preference/Preference.java"]
    preference = preference.replace("public Preference(android.content.Context c) {}", """
  private int widget;
  public Preference(android.content.Context c) {}
  public Preference(android.content.Context c,android.util.AttributeSet a,int s,int r) { this(c); }
  protected int getPersistedInt(int v) { return v; }
  protected void onClick() {}
  protected void performClick(android.view.View view) { onClick(); }
  public android.content.Context getContext() { return org.chromium.base.ContextUtils.context; }
  protected void notifyDependencyChange(boolean disabled) {}
  public boolean shouldDisableDependents() { return false; }
  protected boolean getPersistedBoolean(boolean value) { return value; }
  protected boolean persistBoolean(boolean value) { return true; }
  protected boolean persistInt(int v) { return true; }
  public boolean isPersistent() { return false; } protected void onSetInitialValue(Object v) {}
  protected Object onGetDefaultValue(android.content.res.TypedArray a,int index) { return null; }
  protected android.os.Parcelable onSaveInstanceState() { return null; }
  protected void onRestoreInstanceState(android.os.Parcelable p) {}
  public static class BaseSavedState implements android.os.Parcelable {
    public BaseSavedState(android.os.Parcel p) {} public BaseSavedState(android.os.Parcelable p) {}
    public android.os.Parcelable getSuperState() { return null; } public void writeToParcel(android.os.Parcel p,int flags) {}
  }""").replace("public void setWidgetLayoutResource(int v) {}", "public void setWidgetLayoutResource(int v) { widget=v; }").replace(
        "public void bind() { onBindViewHolder(new PreferenceViewHolder(layout)); }",
        "public PreferenceViewHolder boundHolder; public void bind() { boundHolder=new PreferenceViewHolder(layout); boundHolder.addWidget(widget); boundHolder.itemView.setOnClickListener(view -> performClick(view)); onBindViewHolder(boundHolder); }")
    result["androidx/preference/Preference.java"] = preference.replace("summary=Integer.toString(v)",
        "summary=org.chromium.chrome.PlaybackTestResources.getString(v)")
    result["androidx/preference/PreferenceViewHolder.java"] = result[
        "androidx/preference/PreferenceViewHolder.java"].replace("private final java.util.Map",
            "public final android.view.View itemView=new android.view.View();\n private final java.util.Map").replace(
            "views.put(R.id.seekbar, new android.view.View())", "views.put(R.id.seekbar, new android.widget.SeekBar())").replace(
            "views.put(R.id.seekbar_value, new android.view.View())", "views.put(R.id.seekbar_value, new android.widget.TextView())").replace(
            "  public android.view.View findViewById", """
  public void addWidget(int layout) {
    if(layout==R.layout.argon_background_rule_switch) views.put(R.id.argon_background_rule_enabled,new androidx.appcompat.widget.SwitchCompat());
  }
  public android.view.View findViewById""")
    # The real SwitchPreferenceCompat accessibility path searches the row hierarchy too.
    result["androidx/preference/PreferenceViewHolder.java"] = result[
        "androidx/preference/PreferenceViewHolder.java"].replace(
            "    }\n  }\n", "    }\n    views.forEach((id,view) -> itemView.addView(id,view));\n  }\n", 1)
    edit = result["androidx/preference/EditTextPreference.java"]
    start = edit.index("  public android.widget.EditText openDialog() {")
    end = edit.index("  public void closeDialog(boolean accept) {")
    edit = edit[:start] + """
  private EditTextPreferenceDialogFragmentCompat dialog;
  public android.widget.EditText openDialog() {
    android.view.View view=new android.view.View();
    if(dialogLayout==R.layout.preference_dialog_edittext) view.addView(android.R.id.edit,new android.widget.EditText());
    dialog=new EditTextPreferenceDialogFragmentCompat(); dialog.setPreference(this);
    dialog.onCreate(null); dialog.onBindDialogView(view); input=view.findViewById(android.R.id.edit); return input;
  }
""" + edit[end:]
    result["androidx/preference/EditTextPreference.java"] = edit.replace(
        "if (accept && callChangeListener(input.getText().toString())) setText(input.getText().toString());",
        "dialog.onDialogClosed(accept);")
    return result


def main():
    verify_preference_pin()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        deps = dict(zip(DEPENDENCIES, pool.map(dependency, DEPENDENCIES)))
    platform = adapters()
    with zipfile.ZipFile(deps["preference-sources.jar"]) as archive:
        for name in ("SeekBarPreference", "EditTextPreferenceDialogFragmentCompat",
                     "SwitchPreferenceCompat", "TwoStatePreference"):
            path = f"androidx/preference/{name}.java"
            platform[path] = archive.read(path).decode()
    with zipfile.ZipFile(deps["preference.aar"]) as archive:
        symbols = archive.read("R.txt").decode()
        for layout, fields in {
            "preference_dialog_edittext": {"@android:id/edit": "EditText"},
            "preference_widget_seekbar_material": {"@+id/seekbar": "SeekBar", "@+id/seekbar_value": "TextView"},
            "preference_widget_switch_compat": {"@+id/switchWidget": "androidx.appcompat.widget.SwitchCompat"},
        }.items():
            if not re.search(rf"^int layout {layout} ", symbols, re.MULTILINE):
                raise ValueError(f"Missing AndroidX resource symbol: {layout}")
            tree = ET.fromstring(archive.read(f"res/layout/{layout}.xml"))
            actual = {node.attrib.get("{http://schemas.android.com/apk/res/android}id"): node.tag for node in tree.iter()}
            if any(actual.get(key) != value for key, value in fields.items()):
                raise ValueError(f"AndroidX layout contract changed: {layout}")
    overview = ET.parse(OVERLAY / "res/layout/argon_background_overview.xml").getroot()
    android = "{http://schemas.android.com/apk/res/android}"
    fields = {node.attrib.get(android + "id"): node for node in overview.iter()}
    switch = fields.get("@+id/switchWidget")
    help_link = fields.get("@+id/argon_background_help")
    if (switch is None or switch.tag != "androidx.appcompat.widget.SwitchCompat"
            or any(switch.get(android + flag) != "false" for flag in ("clickable", "focusable"))
            or help_link is None or help_link.get(android + "clickable") != "true"
            or help_link.get(android + "focusable") != "true"
            or fields.get("@android:id/title") is None or fields.get("@android:id/summary") is None):
        raise ValueError("Overview must preserve the native switch, title/summary IDs and independent help action")
    domain = ET.parse(OVERLAY / "res/layout/argon_background_domain_card.xml").getroot()
    domain_fields = {node.attrib.get(android + "id"): node for node in domain.iter()}
    scope_group = domain_fields.get("@+id/argon_background_scope_group")
    if (domain_fields.get("@android:id/title") is None
            or domain_fields.get("@android:id/summary") is None
            or scope_group is None
            or scope_group.get(android + "clickable") != "true"
            or scope_group.get(android + "focusable") != "true"
            or scope_group.get(android + "visibility") != "gone"
            or domain_fields.get("@+id/argon_background_scope_summary") is None
            or domain_fields.get("@+id/argon_background_scope_overlaps") is None):
        raise ValueError("Domain card must preserve AndroidX binding and accessible scope details")
    styleables = {name: sorted(set(re.findall(rf"R.styleable.({name}_\w+)",
        platform[f"androidx/preference/{name}.java"]))) for name in ("SeekBarPreference", "SwitchPreferenceCompat")}
    platform["androidx/preference/R.java"] = (
        "package androidx.preference; public class R {"
        "public static class attr { public static final int seekBarPreferenceStyle=1, switchPreferenceCompatStyle=2; }"
        "public static class id { public static final int seekbar=org.chromium.chrome.R.id.seekbar,"
        "seekbar_value=org.chromium.chrome.R.id.seekbar_value, switchWidget=org.chromium.chrome.R.id.switchWidget; }"
        "public static class styleable {" + "".join(
            f"public static final int[] {group}=new int[{len(names)}];" + "".join(
                f"public static final int {name}={i};" for i, name in enumerate(names))
            for group, names in styleables.items()) + "}}")
    with tempfile.TemporaryDirectory(prefix="argon-playback-flow-") as directory:
        root = Path(directory)
        for path, source in platform.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        sources = list(root.rglob("*.java")) + [
            OVERLAY / "src" / PACKAGE / f"ArgonBackgroundPlayback{name}.java"
            for name in ("Settings", "Store", "Policy", "Controller")
        ] + [OVERLAY / "src/org/chromium/chrome/browser/tab/ArgonBackgroundPlaybackVisibility.java",
             ROOT / "tests/java/ArgonBackgroundPlaybackFlowTest.java"]
        compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
        subprocess.run([*compiler, "-cp", str(deps["android-json.jar"]), "-d", directory, *map(str, sources)], check=True)
        subprocess.run(["java", "-cp", directory + ":" + str(deps["android-json.jar"]),
                        "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackFlowTest", *sys.argv[1:]], check=True)


if __name__ == "__main__":
    main()
