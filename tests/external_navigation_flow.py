"""Host execution of the production settings, policy and pinned-handler interception guard.

Android services and native URL parsing use small adapters. This checks the
permission/dispatch contract, not Android intent resolution or an APK UI.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from test_background_playback_settings import ADAPTERS as SETTINGS_ADAPTERS

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java"
PACKAGE = "org/chromium/chrome/browser/externalnav/"

ADAPTERS = {path: source for path, source in SETTINGS_ADAPTERS.items()
            if "/media/" not in path}
ADAPTERS.update({
    "android/content/SharedPreferences.java": '''package android.content;
public interface SharedPreferences {
  int getInt(String key, int fallback);
  java.util.Set<String> getStringSet(String key, java.util.Set<String> fallback);
  Editor edit();
  interface Editor { Editor putInt(String key, int value); Editor putStringSet(String key, java.util.Set<String> value); void apply(); }
}''',
    "android/content/Context.java": '''package android.content;
public class Context {
  public static final int MODE_PRIVATE = 0;
  public static final java.util.Map<String, Object> values = new java.util.HashMap<>();
  public SharedPreferences getSharedPreferences(String name, int mode) {
    if (!name.equals("argon_external_navigation")) throw new AssertionError(name);
    return new SharedPreferences() {
      public int getInt(String k, int d) { return (Integer) values.getOrDefault(k, d); }
      @SuppressWarnings("unchecked") public java.util.Set<String> getStringSet(String k, java.util.Set<String> d) {
        return (java.util.Set<String>) values.getOrDefault(k, d);
      }
      public Editor edit() { return new Editor() {
        final java.util.Map<String,Object> pending = new java.util.HashMap<>();
        public Editor putInt(String k, int v) { pending.put(k,v); return this; }
        public Editor putStringSet(String k, java.util.Set<String> v) { pending.put(k,v); return this; }
        public void apply() { values.putAll(pending); }
      }; }
    };
  }
}''',
    "org/chromium/base/ContextUtils.java": '''package org.chromium.base;
public class ContextUtils { public static android.content.Context getApplicationContext() { return new android.content.Context(); } }''',
    "android/net/Uri.java": '''package android.net;
public class Uri { private final String value; private Uri(String v) { value=v; }
  public static String encode(String v) { return java.net.URLEncoder.encode(v, java.nio.charset.StandardCharsets.UTF_8); }
  public static Uri parse(String v) { return new Uri(v); } public String toString() { return value; } }''',
    "android/content/Intent.java": '''package android.content;
public class Intent {
  public static final String ACTION_VIEW="view", ACTION_INSERT="insert";
  public static final int FLAG_ACTIVITY_NEW_TASK=1, FLAG_ACTIVITY_REQUIRE_NON_BROWSER=2;
  private android.net.Uri data; public Intent() {}
  public Intent(String action, android.net.Uri value) { data=value; }
  public Intent(String value) { data = value == null ? null : android.net.Uri.parse(value); }
  public android.net.Uri getData() { return data; }
  public void setData(android.net.Uri value) { data=value; }
  public void setFlags(int flags) {} public void addFlags(int flags) {}
  public void setType(String type) {} public void putExtra(String key, String value) {}
}''',
    "org/chromium/base/IntentUtils.java": '''package org.chromium.base;
public class IntentUtils {
  public static final java.util.List<android.content.Intent> launches=new java.util.ArrayList<>();
  public static void safeStartActivity(android.content.Context c, android.content.Intent intent) { launches.add(intent); }
}''',
    "android/net/MailTo.java": '''package android.net;
public class MailTo {
  private String address;
  public static boolean isMailTo(String url) { return url.startsWith("mailto:"); }
  public static MailTo parse(String url) { var result=new MailTo(); result.address=url.substring(7); return result; }
  public String getTo() { return address; }
}''',
    "android/provider/ContactsContract.java": '''package android.provider;
public class ContactsContract {
  public static class Contacts { public static final String CONTENT_TYPE="contact"; }
  public static class Intents { public static class Insert { public static final String EMAIL="email", PHONE="phone"; } }
}''',
    "android/widget/Toast.java": '''package android.widget;
public class Toast { public static final int LENGTH_SHORT=0, LENGTH_LONG=1; public static int shown;
  public static Toast makeText(android.content.Context c, int message, int length) { return new Toast(); }
  public void show() { shown++; } }''',
    "android/app/AlertDialog.java": '''package android.app;
public class AlertDialog {
  public interface Click { void accept(Object dialog, int which); }
  public static Builder last;
  public static class Builder {
    public Click positive, negative; public Builder(android.content.Context c) { last=this; }
    public Builder setTitle(int v) { return this; } public Builder setTitle(String v) { return this; }
    public Builder setMessage(String v) { return this; } public Builder setMessage(int v) { return this; }
    public Builder setPositiveButton(int v, Click click) { positive=click; return this; }
    public Builder setNegativeButton(int v, Click click) { negative=click; return this; }
    public AlertDialog show() { return new AlertDialog(); }
  }
}''',
    "org/chromium/url/GURL.java": '''package org.chromium.url;
public class GURL {
  public final String url; private String scheme="",host="",user="",password=""; private boolean valid;
  public GURL(String value) {
    url=value;
    try {
      var parsed = new java.net.URI(value); scheme=parsed.getScheme()==null?"":parsed.getScheme().toLowerCase(java.util.Locale.ROOT);
      var authority=parsed.getRawAuthority();
      if (authority != null) {
        int at=authority.lastIndexOf('@');
        if(at>=0) { var login=authority.substring(0,at).split(":",2); user=login[0]; password=login.length>1?login[1]:""; authority=authority.substring(at+1); }
        int colon=authority.lastIndexOf(':'); if(colon>=0) authority=authority.substring(0,colon);
        host=java.net.IDN.toASCII(authority).toLowerCase(java.util.Locale.ROOT);
      }
      valid=!scheme.isEmpty() && (!(scheme.equals("http")||scheme.equals("https")) || !host.isEmpty());
    } catch(Exception ignored) {}
  }
  public boolean isValid() { return valid; } public boolean isEmpty() { return url.isEmpty(); }
  public String getSpec() { return url; } public String getScheme() { return scheme; }
  public String getHost() { return host; } public String getUsername() { return user; } public String getPassword() { return password; }
}''',
    "org/chromium/components/embedder_support/util/UrlUtilities.java": '''package org.chromium.components.embedder_support.util;
public class UrlUtilities {
  public static boolean isHttpOrHttps(org.chromium.url.GURL u) { return u.getScheme().equals("https") || u.getScheme().equals("http"); }
  public static boolean isTelScheme(org.chromium.url.GURL u) { return u.getScheme().equals("tel"); }
  public static String getTelNumber(org.chromium.url.GURL u) { return u.getSpec().substring(4); }
  public static String getDomainAndRegistry(String value, boolean privateRegistries) {
    String host=new org.chromium.url.GURL(value).getHost();
    if(!privateRegistries) throw new AssertionError("Private registries must be excluded too");
    if(!host.contains(".") || host.matches("[0-9.]+") || java.util.Set.of("co.uk", "github.io").contains(host)) return "";
    return host;
  }
}''',
    "org/chromium/components/url_formatter/UrlFormatter.java": '''package org.chromium.components.url_formatter;
public class UrlFormatter { public static String formatUrlForSecurityDisplay(org.chromium.url.GURL u, int s) { return u.getHost(); } }''',
    "androidx/preference/ListPreference.java": '''package androidx.preference;
public class ListPreference extends Preference {
  public CharSequence[] entries, values; public String value;
  public ListPreference(android.content.Context c) { super(c); }
  public void setDialogTitle(int v) {} public void setNegativeButtonText(int v) {} public void setEntries(CharSequence[] v) { entries=v; }
  public void setEntryValues(CharSequence[] v) { values=v; } public void setValue(String v) { value=v; }
  public CharSequence getEntry() { for(int i=0;i<values.length;i++) if(values[i].equals(value))return entries[i]; return ""; }
}''',
    "org/chromium/base/task/TaskTraits.java": '''package org.chromium.base.task;
public class TaskTraits { public static final int UI_DEFAULT=0; }''',
    "org/chromium/base/task/PostTask.java": '''package org.chromium.base.task;
public class PostTask {
  public static final java.util.List<Runnable> pending=new java.util.ArrayList<>();
  public static void postTask(int traits, Runnable task) { pending.add(task); }
  public static void drain() { var tasks=new java.util.ArrayList<>(pending); pending.clear(); tasks.forEach(Runnable::run); }
}''',
})
base = "org/chromium/chrome/browser/settings/ChromeBaseSettingsFragment.java"
ADAPTERS[base] = ADAPTERS[base].replace(
    'return Integer.toString(id);', 'return String.format(org.chromium.chrome.R.text.getOrDefault(id, Integer.toString(id)), args);'
).replace('public void onBackPressed() {}', 'public void onBackPressed() { backPresses++; }').replace(
    '  private androidx.preference.PreferenceScreen screen;', '  public static int backPresses;\n  private androidx.preference.PreferenceScreen screen;')


def resources_java(sources):
    names = {}
    for kind, name in re.findall(r"\bR\.(\w+)\.(\w+)", "\n".join(sources)):
        names.setdefault(kind, set()).add(name)
    catalog = {node.attrib["name"]: node.text for node in ET.parse(
        OVERLAY / "res/values/argon_external_navigation_strings.xml").getroot()}
    names.setdefault("string", set()).update(catalog)
    result = "package org.chromium.chrome; public class R {\n"
    entries = []
    for kind, keys in sorted(names.items()):
        result += "public static class " + kind + " {\n"
        for index, name in enumerate(sorted(keys), 100):
            result += f"public static final int {name}={index};\n"
            if kind == "string" and name in catalog:
                entries.append(f"java.util.Map.entry({index}, {json.dumps(catalog[name])})")
        result += "}\n"
    return result + "public static java.util.Map<Integer,String> text=java.util.Map.ofEntries(" + ",".join(entries) + ");}\n"


def explicit_actions(context_menu=None, open_in_app=None, helper_guard=None):
    """Use the pinned link action bodies, both in unit and upstream integration checks."""
    import apply_external_navigation as patch
    from apply_homepage import method_source
    fixture = (ROOT / "tests/fixtures/external_navigation/ExplicitActions.java.in").read_text()
    fixture = fixture.replace("/* HELPER_POLICY */", helper_guard or patch.EXPLICIT_HELPER_GUARD)
    if context_menu is None:
        return patch.patch_open_in_app(patch.patch_context_menu_delegate(fixture))
    for action in patch.CONTEXT_MENU_ACTIONS:
        signature = f"    public void {action}(GURL url) {{"
        method = method_source(context_menu, signature)
        if not method.startswith(signature + "\n" + patch.EXPLICIT_GUARD):
            raise ValueError("Explicit link action must check the policy before dispatch: " + action)
        fixture = fixture.replace(method_source(fixture, signature), method)
    action = method_source(open_in_app, "        Runnable openInApp =")
    if action.count(patch.OPEN_IN_APP_ANCHOR + patch.OPEN_IN_APP_GUARD) != 1:
        raise ValueError("Open in app must check current policy before launching or closing a tab")
    fixture = fixture.replace(method_source(fixture, "        Runnable openInApp ="), action)
    return patch.patch_explicit_action_import(fixture)


def run_flow(guard, fallback_guard, market_guard, context_menu=None, open_in_app=None,
             helper_guard=None, helper_api=None):
    import apply_external_navigation as patch
    adapters = dict(ADAPTERS)
    flow = (ROOT / "tests/fixtures/external_navigation/NavigationFlow.java.in").read_text().replace("/* USER_GUARD */", guard)
    flow = flow.replace("/* FALLBACK_GUARD */", fallback_guard).replace("/* MARKET_GUARD */", market_guard)
    test = ROOT / "tests/java/ArgonExternalNavigationTest.java"
    production = [OVERLAY / "src" / PACKAGE / ("ArgonExternalNavigation" + name + ".java")
                  for name in ("Policy", "Settings")]
    adapters[PACKAGE + "NavigationFlow.java"] = flow
    adapters[PACKAGE + "ExplicitActions.java"] = explicit_actions(context_menu, open_in_app, helper_guard)
    adapters["org/chromium/components/external_intents/ExternalNavigationHelper.java"] = helper_api or patch.patch_helper(
        (ROOT / "tests/fixtures/external_navigation/ExternalNavigationHelper.java.in").read_text())
    adapters["org/chromium/chrome/R.java"] = resources_java(
        [*adapters.values(), test.read_text(), *(p.read_text() for p in production)])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path, source in adapters.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
        result = subprocess.run([*compiler, "-d", directory,
                                 *map(str, root.rglob("*.java")), *map(str, production), str(test)],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        result = subprocess.run(["java", "-ea", "-cp", directory,
                                 "org.chromium.chrome.browser.externalnav.ArgonExternalNavigationTest"],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return result.stdout.strip()


def verify_upstream(src):
    import apply_external_navigation as patch
    from external_navigation_search import verify_search
    handler = (src / patch.HANDLER).read_text()
    for block in (patch.USER_GUARD, patch.FALLBACK_GUARD, patch.MARKET_GUARD):
        if handler.count(block) != 1:
            raise ValueError("Missing or duplicate destination-site guard")
    entry = handler.index("public OverrideUrlLoadingResult shouldOverrideUrlLoading(")
    guard = handler.index(patch.USER_GUARD, entry)
    if guard > handler.index("shouldOverrideUrlLoadingInternal(", entry):
        raise ValueError("The user guard must run before all dispatch/fallback paths")
    params = (src / patch.COMPONENT / "ExternalNavigationParams.java").read_text()
    if "mRequiredAsyncActionTakenCallback = new RequiredCallback<>(mAsyncActionTakenCallback);" not in params:
        raise ValueError("Review the pinned async interception callback lifecycle")
    custom_tab = (src / patch.CUSTOM_TAB).read_text()
    if "class CustomTabNavigationDelegate extends ExternalNavigationDelegateImpl" not in custom_tab:
        raise ValueError("Review the custom tab inheritance of the user policy")
    if "shouldBlockExternalNavigationForUser(" in custom_tab:
        raise ValueError("Review the custom tab override of the user policy")
    fallback = handler.index(patch.FALLBACK_GUARD)
    market = handler.index(patch.MARKET_GUARD)
    context_menu = (src / patch.CONTEXT_MENU_DELEGATE).read_text()
    open_in_app = (src / patch.OPEN_IN_APP).read_text()
    if handler.count(patch.EXPLICIT_HELPER_GUARD) != 1:
        raise ValueError("Missing or duplicate explicit app launch guard")
    start = handler.index(patch.EXPLICIT_HELPER_GUARD)
    navigation = run_flow(handler[guard:guard + len(patch.USER_GUARD)],
                    handler[fallback:fallback + len(patch.FALLBACK_GUARD)],
                    handler[market:market + len(patch.MARKET_GUARD)], context_menu, open_in_app,
                    handler[start:start + len(patch.EXPLICIT_HELPER_GUARD)],
                    (src / patch.HELPER).read_text())
    return navigation + '\n' + verify_search(src)
