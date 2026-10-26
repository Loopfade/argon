"""Compile complete exit UI/bridge classes with Chromium's actual API boundaries."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_exit import exit_patch

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / 'chromium_overlay/titanium/chromium_src/chrome/android/java/src'
SETTINGS_API = Path('components/browser_ui/settings/android/java/src/org/chromium/components/browser_ui/settings/SettingsFragment.java')
TAB_IMPL = Path('chrome/android/java/src/org/chromium/chrome/browser/tab/TabImpl.java')

STUBS = {
    'android/content/Context.java': 'package android.content; public class Context {}',
    'android/os/Bundle.java': 'package android.os; public class Bundle {}',
    'androidx/annotation/IntDef.java': '''package androidx.annotation;
        public @interface IntDef { long[] value(); }''',
    'org/chromium/build/annotations/NullMarked.java': '''package org.chromium.build.annotations;
        public @interface NullMarked {}''',
    'org/chromium/build/annotations/Nullable.java': '''package org.chromium.build.annotations;
        import java.lang.annotation.*;
        @Target({ElementType.TYPE_USE,ElementType.PARAMETER}) public @interface Nullable {}''',
    'org/jni_zero/CalledByNative.java': 'package org.jni_zero; public @interface CalledByNative {}',
    'org/jni_zero/NativeMethods.java': 'package org.jni_zero; public @interface NativeMethods {}',
    'org/jni_zero/JniType.java': '''package org.jni_zero; import java.lang.annotation.*;
        @Target({ElementType.TYPE_USE,ElementType.PARAMETER,ElementType.METHOD})
        public @interface JniType { String value(); }''',
    'org/chromium/base/Callback.java': 'package org.chromium.base; public interface Callback<T> { void onResult(T value); }',
    'org/chromium/base/supplier/MonotonicObservableSupplier.java': '''package org.chromium.base.supplier;
        public interface MonotonicObservableSupplier<T> {}''',
    'org/chromium/base/supplier/SettableMonotonicObservableSupplier.java': '''package org.chromium.base.supplier;
        public class SettableMonotonicObservableSupplier<T> implements MonotonicObservableSupplier<T> {
            public void set(T value) {}
        }''',
    'org/chromium/base/supplier/ObservableSuppliers.java': '''package org.chromium.base.supplier;
        public class ObservableSuppliers {
            public static <T> SettableMonotonicObservableSupplier<T> createMonotonic() {
                return new SettableMonotonicObservableSupplier<T>();
            }
        }''',
    'androidx/preference/Preference.java': '''package androidx.preference;
        public class Preference {
            public String key; public boolean checked; public OnPreferenceChangeListener listener;
            public Preference(android.content.Context context) {}
            public void setSummary(int title) {} public void setSelectable(boolean v) {}
            public void setTitle(int title) {} public void setKey(String value) { key=value; }
            public void setPersistent(boolean v) {} public void setChecked(boolean value) { checked=value; }
            public void setOnPreferenceChangeListener(OnPreferenceChangeListener value) { listener=value; }
            public interface OnPreferenceChangeListener { boolean onPreferenceChange(Preference p,Object v); }
        }''',
    'androidx/preference/PreferenceScreen.java': '''package androidx.preference;
        public class PreferenceScreen {
            public final java.util.List<Preference> rows=new java.util.ArrayList<>();
            public void addPreference(Preference value) { rows.add(value); }
        }''',
    'androidx/preference/PreferenceManager.java': '''package androidx.preference;
        public class PreferenceManager {
            public PreferenceScreen createPreferenceScreen(android.content.Context c) { return new PreferenceScreen(); }
        }''',
    'org/chromium/components/browser_ui/settings/ChromeBaseCheckBoxPreference.java': '''package org.chromium.components.browser_ui.settings;
        public class ChromeBaseCheckBoxPreference extends androidx.preference.Preference {
            public ChromeBaseCheckBoxPreference(android.content.Context c) { super(c); }
        }''',
    'org/chromium/chrome/browser/settings/ChromeBaseSettingsFragment.java': '''package org.chromium.chrome.browser.settings;
        public abstract class ChromeBaseSettingsFragment implements org.chromium.components.browser_ui.settings.SettingsFragment {
            private androidx.preference.PreferenceScreen screen;
            public abstract void onCreatePreferences(android.os.Bundle saved,String root);
            public abstract org.chromium.base.supplier.MonotonicObservableSupplier<String> getPageTitle();
            public String getString(int id) { return "title"; }
            public android.content.Context requireContext() { return new android.content.Context(); }
            public androidx.preference.PreferenceManager getPreferenceManager() { return new androidx.preference.PreferenceManager(); }
            public void setPreferenceScreen(androidx.preference.PreferenceScreen s) { screen=s; }
            public androidx.preference.PreferenceScreen getPreferenceScreen() { return screen; }
        }''',
    'org/chromium/chrome/browser/profiles/Profile.java': 'package org.chromium.chrome.browser.profiles; public class Profile {}',
    'org/chromium/chrome/browser/profiles/ProfileManager.java': '''package org.chromium.chrome.browser.profiles;
        public class ProfileManager { public static Profile getLastUsedRegularProfile() { return new Profile(); } }''',
    'org/chromium/chrome/browser/tab/Tab.java': '''package org.chromium.chrome.browser.tab;
        public interface Tab { boolean isDestroyed(); Object getWebContents(); }''',
    'org/chromium/chrome/browser/tab/TabImpl.java': '''package org.chromium.chrome.browser.tab;
        class TabImpl implements Tab {
            static class WebContents { boolean stopped; void stop() { stopped=true; } }
            WebContents mWebContents=new WebContents();
            String mUrl,mTitle; boolean destroyed;
            public boolean isDestroyed() { return destroyed; }
            public Object getWebContents() { return mWebContents; }
            String getUrl() {return "url";} String getTitle() {return "title";}
            // PRODUCTION_PREPARE_METHOD
        }''',
    'org/chromium/chrome/browser/tab/TabFixture.java': '''package org.chromium.chrome.browser.tab;
        public class TabFixture {
            public static Tab create(boolean live) {
                TabImpl t=new TabImpl();if(!live)t.mWebContents=null;return t;
            }
            public static boolean preserved(Tab t) {return "url".equals(((TabImpl)t).mUrl);}
            public static boolean stopped(Tab t) {return ((TabImpl)t).mWebContents.stopped;}
            public static void close(Tab t) {((TabImpl)t).mWebContents=null;}
        }''',
    'org/chromium/chrome/browser/tabmodel/TabClosureParams.java': '''package org.chromium.chrome.browser.tabmodel;
        public class TabClosureParams {
            public boolean exit,undo=true,restore=true,hide,unload=true;
            public static TabClosureParams closeAllTabs() { return new TabClosureParams(); }
            public TabClosureParams uponExit(boolean v) {exit=v;return this;}
            public TabClosureParams allowUndo(boolean v) {undo=v;return this;}
            public TabClosureParams saveToTabRestoreService(boolean v) {restore=v;return this;}
            public TabClosureParams hideTabGroups(boolean v) {hide=v;return this;}
            public TabClosureParams allowUnloadHandlers(boolean v) {unload=v;return this;}
            public TabClosureParams build() {return this;}
        }''',
    'org/chromium/chrome/browser/tabmodel/TabRemover.java': '''package org.chromium.chrome.browser.tabmodel;
        public interface TabRemover { void forceCloseTabs(TabClosureParams params); }''',
    'org/chromium/chrome/browser/tabmodel/TabModel.java': '''package org.chromium.chrome.browser.tabmodel;
        public interface TabModel {
            int getCount(); org.chromium.chrome.browser.tab.Tab getTabAt(int i);
            boolean isOffTheRecord(); TabRemover getTabRemover(); void commitAllTabClosures();
        }''',
    'org/chromium/chrome/browser/exit/ArgonExitCoordinator.java': '''package org.chromium.chrome.browser.exit;
        class ArgonExitCoordinator { static boolean blocked; static boolean isBlockingPages() { return blocked; } }''',
    'org/chromium/chrome/browser/exit/ArgonExitStore.java': '''package org.chromium.chrome.browser.exit;
        class ArgonExitStore { static int types; static int selection(String p) { return types; }
            static void select(String p,int value) { types=value; } }''',
    'org/chromium/chrome/browser/exit/ArgonExitBridgeJni.java': '''package org.chromium.chrome.browser.exit;
        class ArgonExitBridgeJni {
            static ArgonExitBridge.Natives get() { return new ArgonExitBridge.Natives() {
                public String profileKey(org.chromium.chrome.browser.profiles.Profile p) {return "regular";}
                public void prepare(org.chromium.chrome.browser.profiles.Profile p,boolean c,boolean r,org.chromium.base.Callback<Boolean> d) {}
                public void clear(org.chromium.chrome.browser.profiles.Profile p,int t,org.chromium.base.Callback<Integer> d) {}
                public void resume(org.chromium.chrome.browser.profiles.Profile p) {}
            }; }
        }''',
}

HARNESS = '''package org.chromium.chrome.browser.exit;
import org.chromium.chrome.browser.tab.*;
import org.chromium.chrome.browser.tabmodel.*;
import org.chromium.components.browser_ui.settings.SettingsFragment;
public class ExitAndroidApiTest {
    static class Model implements TabModel {
        Tab tab; boolean privateMode,closed,committed;
        Model(Tab value,boolean p) {tab=value;privateMode=p;}
        public int getCount() {return 1;} public Tab getTabAt(int i) {return tab;}
        public boolean isOffTheRecord() {return privateMode;}
        public TabRemover getTabRemover() {return p -> {
            assert p.exit && !p.undo && !p.restore && p.hide && !p.unload;
            // Chromium TabsEventRouter::OnTabRemoved CHECKs this before
            // normal closure destroys WebContents. Reproduce that contract.
            assert tab.getWebContents()!=null : "contents destroyed before closure observers";
            closed=true;
            TabFixture.close(tab);
        };}
        public void commitAllTabClosures() {committed=true;}
    }
    static boolean prepare(Model model,boolean close) throws Exception {
        var method=ArgonExitBridge.class.getDeclaredMethod("prepareModel",TabModel.class,boolean.class);
        method.setAccessible(true);return (Boolean)method.invoke(null,model,close);
    }
    public static void main(String[] args) throws Exception {
        Model regular=new Model(TabFixture.create(true),false);
        assert prepare(regular,false) && TabFixture.preserved(regular.tab) && !regular.closed;
        assert regular.tab.getWebContents()!=null && TabFixture.stopped(regular.tab);
        Model close=new Model(TabFixture.create(true),false);
        assert prepare(close,true) && !TabFixture.preserved(close.tab) && close.closed && close.committed;
        Model privateTabs=new Model(TabFixture.create(true),true);
        assert prepare(privateTabs,false) && !TabFixture.preserved(privateTabs.tab) && privateTabs.closed;
        Model frozen=new Model(TabFixture.create(false),false);
        assert prepare(frozen,false) && !frozen.closed && !frozen.committed;
        Tab unknown=new Tab() {public boolean isDestroyed(){return false;} public Object getWebContents(){return new Object();}};
        assert !prepare(new Model(unknown,false),true);
        ArgonExitSettings page=new ArgonExitSettings();
        assert page.getAnimationType()==SettingsFragment.AnimationType.PROPERTY;
        page.onCreatePreferences(null,null);
        var rows=page.getPreferenceScreen().rows; assert rows.size()==7;
        assert rows.get(1).listener.onPreferenceChange(rows.get(1),true) && ArgonExitStore.types==1;
        ArgonExitCoordinator.blocked=true;
        assert !rows.get(2).listener.onPreferenceChange(rows.get(2),true) && ArgonExitStore.types==1;
        System.out.println("PASS: production exit Java bridge, package visibility, tab closure and settings contract");
    }
}
'''


def production_prepare_method(tab_impl=None):
    if tab_impl is None:
        tab_impl = exit_patch.patch_tab('''
    public boolean loadIfNeeded(boolean forceBackingSize) {
    public LoadUrlResult loadUrl(LoadUrlParams params) {
        boolean eligibleToCreateWebContents = webContents == null && !isDormant();
    private @TabDestroyStatus int destroyWebContents(boolean deleteNativeWebContents) {
''')
    return re.search(r'    public boolean prepareForArgonExit\(boolean preserveNavigation\) \{.*?\n    \}',
                     tab_impl, re.S)[0]


def verify_exit_android_api(settings_api=None, tab_impl=None, prepare_method=None):
    if settings_api is None:
        settings_api = (ROOT / 'tests/fixtures/exit/SettingsFragment.java.in').read_text()
    if tab_impl is not None and not re.search(r'^class TabImpl implements Tab, TabInternal', tab_impl, re.M):
        raise ValueError('Review the pinned TabImpl visibility contract')
    sources = dict(STUBS)
    sources['org/chromium/chrome/browser/tab/TabImpl.java'] = sources[
        'org/chromium/chrome/browser/tab/TabImpl.java'].replace(
            '// PRODUCTION_PREPARE_METHOD', prepare_method or production_prepare_method(tab_impl))
    sources['org/chromium/components/browser_ui/settings/SettingsFragment.java'] = settings_api
    for relative in ('exit/ArgonExitBridge.java', 'exit/ArgonExitSettings.java', 'tab/ArgonExitTabHelper.java'):
        name = 'org/chromium/chrome/browser/' + relative
        sources[name] = (JAVA / name).read_text()
    sources['org/chromium/chrome/browser/exit/ExitAndroidApiTest.java'] = HARNESS
    titles = re.findall(r'R\.string\.(\w+)', sources['org/chromium/chrome/browser/exit/ArgonExitSettings.java'])
    sources['org/chromium/chrome/R.java'] = ('package org.chromium.chrome; public class R { public static class string {'
        + ''.join(f'public static final int {name}={i};' for i, name in enumerate(dict.fromkeys(titles))) + '} }')
    compiler = ['javac'] if shutil.which('javac') else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        paths = []
        for name, value in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            paths.append(str(path))
        result = subprocess.run([*compiler, '-d', str(root), *paths], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        result = subprocess.run(['java', '-ea', '-cp', str(root),
            'org.chromium.chrome.browser.exit.ExitAndroidApiTest'], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout.strip()


class ExitAndroidApiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_complete_production_bridge_and_settings_compile_and_run(self):
        self.assertIn('PASS:', verify_exit_android_api())

    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_destroying_contents_before_closure_observers_is_rejected(self):
        broken = production_prepare_method().replace('mWebContents.stop();', 'mWebContents = null;')
        with self.assertRaises(AssertionError):
            verify_exit_android_api(prepare_method=broken)
