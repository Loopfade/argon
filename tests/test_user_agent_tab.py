"""Compile Argon's patched tab-switch method and verify per-tab mode selection."""
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_user_agent as patch
from test_user_agent import compile_java, run_java
from apply_homepage import method_source

FIXTURES = ROOT / 'tests/fixtures/user_agent'
MENU_START = '        if (id == R.id.request_desktop_site_id || id == R.id.request_desktop_site_check_id) {'


def menu_branch(source):
    if source.count(MENU_START) != 1:
        raise ValueError('Desktop-site menu branch changed')
    start = source.index(MENU_START)
    end = source.index('\n        }', start) + len('\n        }\n')
    return source[start:end]

UPSTREAM = """\
import org.chromium.chrome.browser.media.MediaCaptureDevicesDispatcherAndroid;
public class TabUtils {
    private static WebContents assumeNonNull(WebContents value) { return value; }
    public static void switchUserAgent(Tab tab, boolean switchToDesktop) {
        final boolean reloadOnChange = !tab.isNativePage();
        assumeNonNull(tab.getWebContents())
                .getNavigationController()
                .setUseDesktopUserAgent(
                        switchToDesktop, reloadOnChange, /* skipOnInitialNavigation= */ true);
    }
}
"""

ADAPTERS = """\
import java.util.*;
@interface ContentSetting { int ALLOW=1, BLOCK=2; }
@interface TabUserAgent { int DEFAULT=0, MOBILE=1, DESKTOP=2; }
class ContentSettingsType { static final int REQUEST_DESKTOP_SITE=1; }
class Tab {
    WebContents web = new WebContents();
    int mode = TabUserAgent.DEFAULT;
    boolean nativePage;
    WebContents getWebContents(){return web;}
    boolean isNativePage(){return nativePage;}
    Object getUrl(){return new Object();}
    Object getProfile(){return new Object();}
    Object getContext(){return new Object();}
    void setUserAgent(int value){mode=value;TestHarness.log.add("tab");}
    int getUserAgent(){return mode;}
}
class WebContents {
    NavigationController controller = new NavigationController();
    boolean overrideInNewTabs;
    NavigationController getNavigationController(){return controller;}
}
class NavigationController {
    boolean desktop;
    boolean reload;
    boolean getUseDesktopUserAgent(){return desktop;}
    void setUseDesktopUserAgent(boolean value,boolean shouldReload,boolean skipInitial){
        desktop=value;reload=shouldReload;
        if(!skipInitial)throw new AssertionError();
        TestHarness.log.add("navigation");
    }
}
class WebsitePreferenceBridge {
    static int rule;
    static int writes;
    static int getContentSetting(Object profile,int type,Object url,Object other){
        if(type!=ContentSettingsType.REQUEST_DESKTOP_SITE)throw new AssertionError();
        TestHarness.log.add("rule");
        return rule;
    }
}
class DesktopSiteUtils {
    static boolean shouldOverrideDesktopSite(Object profile,Object url,Object context){
        return WebsitePreferenceBridge.rule==ContentSetting.ALLOW;
    }
    static void setRequestDesktopSiteContentSettingsForUrl(Object profile,Object url,boolean desktop){
        WebsitePreferenceBridge.rule=desktop?ContentSetting.ALLOW:ContentSetting.BLOCK;
        WebsitePreferenceBridge.writes++;
    }
}
class R {static class id {static final int request_desktop_site_id=1,request_desktop_site_check_id=2,argon_reset_user_agent_id=3;}}
class EventConstants {static final int APP_MENU_DESKTOP_SITE_EXCEPTION_ADDED=1;}
class TrackerFactory {
    static TrackerFactory getTrackerForProfile(Object profile){return new TrackerFactory();}
    void notifyEvent(int event){TestHarness.log.add("site-exception");}
}
class RequestDesktopUtils {static void recordUserChangeUserAgent(boolean desktop,Tab tab){}}
class ContentUtils {
    static void setUserAgentOverride(WebContents contents,boolean overrideInNewTabs){
        // Chromium's JNI boolean controls new-tab inheritance, not UA content.
        contents.overrideInNewTabs=overrideInNewTabs;
        TestHarness.log.add("override");
    }
}
class TestHarness {
    static List<String> log = new ArrayList<>();
    static void check(boolean b){if(!b)throw new AssertionError(log);}
    static void choice(int rule,boolean desktop,int expected){
        Tab tab=new Tab();WebsitePreferenceBridge.rule=rule;log.clear();
        TabUtils.switchUserAgent(tab,desktop);
        check(tab.mode==expected);
        check(tab.web.controller.desktop==desktop && tab.web.controller.reload);
        check(tab.web.overrideInNewTabs==desktop);
        check(log.equals(List.of("tab","override","navigation")));
    }
    public static void main(String[] args){
        if(args.length>0){menuChoices();return;}
        choice(ContentSetting.ALLOW,true,TabUserAgent.DESKTOP);
        choice(ContentSetting.ALLOW,false,TabUserAgent.MOBILE);
        choice(ContentSetting.BLOCK,true,TabUserAgent.DESKTOP);
        choice(ContentSetting.BLOCK,false,TabUserAgent.MOBILE);
        Tab tab=new Tab();tab.mode=TabUserAgent.MOBILE;log.clear();
        TabUtils.switchUserAgent(tab,true,false);
        check(tab.mode==TabUserAgent.MOBILE);
        check(!tab.web.controller.desktop);
        check(!tab.web.overrideInNewTabs);
        check(log.equals(List.of("override","navigation")));
        tab.mode=TabUserAgent.DESKTOP;log.clear();
        TabUtils.switchUserAgent(tab,false,false);
        check(tab.mode==TabUserAgent.DESKTOP);
        check(tab.web.controller.desktop);
        check(tab.web.overrideInNewTabs);
        check(log.equals(List.of("override","navigation")));
        tab.mode=TabUserAgent.DEFAULT;log.clear();
        TabUtils.switchUserAgent(tab,false,false);
        check(!tab.web.controller.desktop);
        check(!tab.web.overrideInNewTabs);
        check(tab.mode==TabUserAgent.DEFAULT);
        tab.nativePage=true;
        TabUtils.switchUserAgent(tab,true,false);
        check(!tab.web.controller.reload);
        System.out.println("PASS: automatic navigation cannot override explicit mobile/desktop tab modes");
    }
    static void menuChoices(){
        for(int rule:new int[]{ContentSetting.ALLOW,ContentSetting.BLOCK}){
            for(boolean desktop:new boolean[]{false,true}){
                for(int item:new int[]{R.id.request_desktop_site_id,R.id.request_desktop_site_check_id}){
                    Tab selected=new Tab(),other=new Tab();
                    selected.web.controller.desktop=!desktop;
                    other.web.controller.desktop=rule==ContentSetting.ALLOW;
                    WebsitePreferenceBridge.rule=rule;WebsitePreferenceBridge.writes=0;log.clear();
                    check(new MenuActions().select(selected,item));
                    check(selected.mode==(desktop?TabUserAgent.DESKTOP:TabUserAgent.MOBILE));
                    check(selected.web.controller.desktop==desktop && selected.web.controller.reload);
                    check(selected.web.overrideInNewTabs==desktop);
                    check(WebsitePreferenceBridge.rule==rule && WebsitePreferenceBridge.writes==0);
                    check(other.mode==TabUserAgent.DEFAULT);
                    check(other.web.controller.desktop==(rule==ContentSetting.ALLOW));
                    check(log.equals(List.of("tab","override","navigation")));
                }
            }
        }
        System.out.println("PASS: menu choices preserve site exceptions and other tabs");
    }
    static void automatic(){
        for(int rule:new int[]{ContentSetting.ALLOW,ContentSetting.BLOCK}){
            for(int pinned:new int[]{TabUserAgent.MOBILE,TabUserAgent.DESKTOP}){
                Tab tab=new Tab(),other=new Tab();tab.mode=pinned;
                WebsitePreferenceBridge.rule=rule;WebsitePreferenceBridge.writes=0;
                check(new MenuActions().select(tab,R.id.argon_reset_user_agent_id));
                check(tab.mode==TabUserAgent.DEFAULT);
                check(tab.web.controller.desktop==(rule==ContentSetting.ALLOW));
                check(WebsitePreferenceBridge.writes==0 && WebsitePreferenceBridge.rule==rule);
                check(other.mode==TabUserAgent.DEFAULT && !other.web.controller.desktop);
                WebsitePreferenceBridge.rule=rule==ContentSetting.ALLOW?ContentSetting.BLOCK:ContentSetting.ALLOW;
                TabUtils.resetUserAgent(tab);
                check(tab.web.controller.desktop==(rule!=ContentSetting.ALLOW));
            }
        }
        System.out.println("PASS: automatic mode follows existing site rules and preserves other tabs");
    }
}
"""


def run_tab_flow(utils_source, menu_source, *args):
    signatures = ['    public static void switchUserAgent(Tab tab, boolean switchToDesktop) {']
    if 'boolean explicitSelection)' in utils_source:
        signatures.append('    public static void switchUserAgent(\n')
    if 'public static void resetUserAgent(' in utils_source:
        signatures.append('    public static void resetUserAgent(Tab tab) {')
    utils = '\n'.join(method_source(utils_source, signature) for signature in signatures)
    source = ('import java.util.*;\npublic class TabUtils {\n'
              '    private static WebContents assumeNonNull(WebContents value) {return value;}\n'
              + utils + '\n}\n' + ADAPTERS.replace('import java.util.*;\n', '')
              + '\nclass MenuActions {\n'
              '    Tab activityTab; Tab getActivityTab(){return activityTab;}\n'
              '    boolean select(Tab currentTab,int id){\n'
              '        activityTab=currentTab; Object currentProfile=currentTab.getProfile();\n'
              '        WebContents webContents=currentTab.getWebContents();\n'
              + (method_source(menu_source,
                    '        if (id == R.id.argon_reset_user_agent_id) {')
                 if 'R.id.argon_reset_user_agent_id' in menu_source else '')
              + menu_branch(menu_source) + '\n        return false;\n    }\n}\n')
    if args == ('automatic',):
        source = source.replace('if(args.length>0){menuChoices();return;}',
                                'if(args.length>0){automatic();return;}')
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        compile_java(root, [], {'TabUtils.java': source})
        return run_java(root, 'TestHarness', *args)


def verify_upstream(src):
    # Compare the actual menu after all earlier patches and run that code,
    # rather than a test-specific substitute for ChromeActivity's caller.
    menu = (src / patch.CHROME_ACTIVITY).read_text()
    expected = patch.patch_menu_user_agent_selection(
        (FIXTURES / 'desktop_site_menu.java.in').read_text())
    if menu_branch(menu) != menu_branch(expected):
        raise ValueError('Pinned desktop-site menu changed; review the per-tab regression')
    signature = '        if (id == R.id.argon_reset_user_agent_id) {'
    if method_source(menu, signature) != method_source(expected, signature):
        raise ValueError('Pinned automatic-mode action changed')
    utils = (src / patch.TAB_UTILS).read_text()
    return (run_tab_flow(utils, menu, 'menu')
            + run_tab_flow(utils, menu, 'automatic')).strip()


class TabSwitchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_reset_uses_site_rules_without_mutating_them(self):
        utils = patch.patch_tab_user_agent_utils(UPSTREAM)
        menu = patch.patch_menu_user_agent_selection(
            (FIXTURES / 'desktop_site_menu.java.in').read_text())
        self.assertIn('PASS: automatic mode', run_tab_flow(utils, menu, 'automatic'))

    def test_auto_menu_is_available_only_for_manual_tabs(self):
        for count in (1, 2):
            source = ('import org.chromium.chrome.browser.tab.Tab;\n'
                      + 'if (rdsListItem != null) modelList.add(rdsListItem);\n' * count)
            result = patch.patch_automatic_mode_menu(source, count)
            self.assertEqual(result, patch.patch_automatic_mode_menu(result, count))
            self.assertEqual(result.count('R.id.argon_reset_user_agent_id'), count)
            self.assertIn('currentTab.getUserAgent() == TabUserAgent.MOBILE', result)
            self.assertIn('currentTab.getUserAgent() == TabUserAgent.DESKTOP', result)
        with self.assertRaises(ValueError):
            patch.patch_automatic_mode_menu('class ChangedMenu {}', 2)
    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_real_java_tab_switch_method(self):
        patched = patch.patch_tab_user_agent_utils(UPSTREAM)
        menu = patch.patch_menu_user_agent_selection(
            (FIXTURES / 'desktop_site_menu.java.in').read_text())
        self.assertIn('PASS:', run_tab_flow(patched, menu))

    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_menu_preserves_site_rules_and_other_tabs(self):
        utils = patch.patch_tab_user_agent_utils(UPSTREAM)
        original = (FIXTURES / 'desktop_site_menu.java.in').read_text()
        with self.assertRaisesRegex(ValueError, 'AssertionError'):
            run_tab_flow(utils, original, 'menu')
        menu = patch.patch_menu_user_agent_selection(original)
        self.assertIn('PASS:', run_tab_flow(utils, menu, 'menu'))

    def test_menu_patch_is_idempotent_and_rejects_changed_callers(self):
        source = (FIXTURES / 'desktop_site_menu.java.in').read_text()
        patched = patch.patch_menu_user_agent_selection(source)
        self.assertEqual(patched, patch.patch_menu_user_agent_selection(patched))
        with self.assertRaises(ValueError):
            patch.patch_menu_user_agent_selection(source.replace('currentProfile,', 'newProfile,'))
        with self.assertRaises(ValueError):
            patch.patch_menu_user_agent_selection(source + source)

    def test_idempotent_and_pinned_anchor(self):
        patched = patch.patch_tab_user_agent_utils(UPSTREAM)
        self.assertEqual(patched, patch.patch_tab_user_agent_utils(patched))
        with self.assertRaises(ValueError):
            patch.patch_tab_user_agent_utils("class TabUtils {}")
