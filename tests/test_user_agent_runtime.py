"""Exercise production preference dialogs with Android adapters and real strings."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from test_user_agent import ADAPTERS, MODEL, ROOT, compile_java, run_java

OVERLAY=ROOT/'chromium_overlay/titanium/chromium_src/chrome/android/java'
PREFERENCE=OVERLAY/'src/org/chromium/chrome/browser/privacy/settings/ArgonUserAgentPreference.java'
UI={
'android/R.java': '''package android; public class R { public static class string { public static final int cancel=-1,ok=-2; } }''',
'android/content/Context.java': '''package android.content;
public class Context {
    public String getString(int id,Object... args) { return String.format(org.chromium.chrome.R.strings.get(id),args); }
    public android.content.res.Resources getResources() { return new android.content.res.Resources(); }
}''',
'android/content/res/Resources.java': '''package android.content.res;
public class Resources { public static class Metrics { public float density=1; }
public Metrics getDisplayMetrics() { return new Metrics(); } }''',
'android/text/InputType.java': '''package android.text;
public class InputType { public static final int TYPE_CLASS_TEXT=1,TYPE_TEXT_FLAG_NO_SUGGESTIONS=2; }''',
'android/view/View.java': '''package android.view;
public class View {
    public interface Listener { void onClick(View v); }
    private Listener listener;
    public void setOnClickListener(Listener v) { listener=v; }
    public void click() { if(listener!=null) listener.onClick(this); }
}''',
'android/widget/EditText.java': '''package android.widget;
public class EditText extends android.view.View {
    public static EditText latest; private String text=""; public CharSequence error;
    public EditText(android.content.Context c) { latest=this; }
    public void setSingleLine() {} public void setInputType(int v) {} public void setHint(int v) {}
    public void setText(String v) { text=v; } public CharSequence getText() { return text; }
    public int length() { return text.length(); } public void setSelection(int v) {}
    public void setError(CharSequence v) { error=v; }
}''',
'android/widget/FrameLayout.java': '''package android.widget;
public class FrameLayout { public FrameLayout(android.content.Context c) {}
public void setPadding(int a,int b,int c,int d) {} public void addView(EditText v) {} }''',
'android/widget/Toast.java': '''package android.widget;
public class Toast { public static final int LENGTH_LONG=1; public static int shown;
public static Toast makeText(android.content.Context c,int id,int length) { return new Toast(); }
public void show() { shown++; } }''',
'androidx/appcompat/app/AlertDialog.java': '''package androidx.appcompat.app;
public class AlertDialog {
    public static final int BUTTON_POSITIVE=-1,BUTTON_NEGATIVE=-2,BUTTON_NEUTRAL=-3;
    public static AlertDialog latest; public boolean shown; public CharSequence[] choices;
    public int checked,message; public Click choose;
    public final java.util.Map<Integer,android.view.View> buttons=new java.util.HashMap<>();
    public interface Click { void onClick(AlertDialog dialog,int which); }
    public void show() { latest=this; shown=true; }
    public void dismiss() { shown=false; }
    public android.view.View getButton(int which) { return buttons.get(which); }
    public void select(int which) { choose.onClick(this,which); }
    public void press(int which) { getButton(which).click(); }
    public static class Builder {
        private final AlertDialog d=new AlertDialog();
        public Builder(android.content.Context c) {}
        public Builder setTitle(int id) { return this; }
        public Builder setMessage(int id) { d.message=id; return this; }
        public Builder setView(Object view) { return this; }
        public Builder setSingleChoiceItems(CharSequence[] items,int checked,Click c) {
            d.choices=items;d.checked=checked;d.choose=c;return this;
        }
        private Builder button(int which,Click c) {
            var button=new android.view.View();
            button.setOnClickListener(v->{if(c!=null)c.onClick(d,which);d.dismiss();});
            d.buttons.put(which,button);return this;
        }
        public Builder setPositiveButton(int id,Click c) { return button(BUTTON_POSITIVE,c); }
        public Builder setNegativeButton(int id,Click c) { return button(BUTTON_NEGATIVE,c); }
        public Builder setNeutralButton(int id,Click c) { return button(BUTTON_NEUTRAL,c); }
        public AlertDialog create() { return d; }
        public AlertDialog show() { d.show();return d; }
    }
}''',
'androidx/lifecycle/Lifecycle.java': '''package androidx.lifecycle;
public class Lifecycle {
    public enum Event { ON_RESUME }
    private final java.util.List<LifecycleEventObserver> observers=new java.util.ArrayList<>();
    public void addObserver(LifecycleObserver o) { observers.add((LifecycleEventObserver)o); }
    public void resume() { for(var o:observers)o.onStateChanged(this,Event.ON_RESUME); }
}''',
'androidx/lifecycle/LifecycleObserver.java': '''package androidx.lifecycle;
public interface LifecycleObserver {}''',
'androidx/lifecycle/LifecycleEventObserver.java': '''package androidx.lifecycle;
public interface LifecycleEventObserver extends LifecycleObserver {
    void onStateChanged(Object owner,Lifecycle.Event event);
}''',
'androidx/preference/Preference.java': '''package androidx.preference;
public class Preference {
    public interface Click { boolean onClick(Preference p); }
    public CharSequence summary;public boolean visible=true;private Click click;
    public android.content.Context getContext() { return new android.content.Context(); }
    public void setSummary(CharSequence v) { summary=v; }
    public void setVisible(boolean v) { visible=v; }
    public void setOnPreferenceClickListener(Click v) { click=v; }
    public void click() { click.onClick(this); }
}''',
'androidx/preference/PreferenceFragmentCompat.java': '''package androidx.preference;
public class PreferenceFragmentCompat {
    public final java.util.Map<String,Preference> rows=new java.util.HashMap<>();
    private final androidx.lifecycle.Lifecycle lifecycle=new androidx.lifecycle.Lifecycle();
    public PreferenceFragmentCompat() {
        rows.put("argon_user_agent",new Preference());
        rows.put("argon_user_agent_restart",new Preference());
    }
    public Preference findPreference(String key) { return rows.get(key); }
    public androidx.lifecycle.Lifecycle getLifecycle() { return lifecycle; }
}''',
'org/chromium/chrome/browser/lifetime/ApplicationLifetime.java': '''package org.chromium.chrome.browser.lifetime;
public class ApplicationLifetime { public static int restarts;
public static void terminate(boolean restart) { if(!restart)throw new AssertionError(); restarts++; } }''',
}


def run_settings_flow():
    output=[]
    for locale in ('values','values-ru'):
        resources=ET.parse(OVERLAY/f'res/{locale}/argon_user_agent_strings.xml').getroot()
        strings={e.attrib['name']:''.join(e.itertext()) for e in resources}
        adapters={**ADAPTERS,**UI}
        adapters['org/chromium/chrome/R.java']='package org.chromium.chrome; public class R { public static class string {'+''.join(
            f'public static final int {name}={i};' for i,name in enumerate(strings,1))+'} public static final java.util.Map<Integer,String> strings=java.util.Map.ofEntries('+','.join(
            f'java.util.Map.entry({i},{json.dumps(value,ensure_ascii=True)})' for i,value in enumerate(strings.values(),1))+');}'
        with tempfile.TemporaryDirectory(prefix='argon-ua-ui-') as directory:
            root=Path(directory)
            compile_java(root,[MODEL,PREFERENCE,ROOT/'tests/java/ArgonUserAgentSettingsTest.java'],adapters)
            for start in ('DEFAULT','CUSTOM'):
                output.append(run_java(root,'org.chromium.chrome.browser.privacy.settings.ArgonUserAgentSettingsTest',start).strip())
    return 'PASS: production User-Agent settings in English and Russian; cancel, later, undo, resume, validation and restart failure'


class UserAgentSettingsTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'),'JDK required')
    def test_user_facing_flow_in_both_languages(self):
        self.assertIn('PASS:',run_settings_flow())
