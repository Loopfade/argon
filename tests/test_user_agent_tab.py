"""Compile Argon's patched tab-switch method and verify per-tab mode selection."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_user_agent as patch

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
    void setUserAgent(int value){mode=value;TestHarness.log.add("tab");}
    int getUserAgent(){return mode;}
}
class WebContents {
    NavigationController controller = new NavigationController();
    NavigationController getNavigationController(){return controller;}
}
class NavigationController {
    boolean desktop;
    boolean reload;
    void setUseDesktopUserAgent(boolean value,boolean shouldReload,boolean skipInitial){
        desktop=value;reload=shouldReload;
        if(!skipInitial)throw new AssertionError();
        TestHarness.log.add("navigation");
    }
}
class WebsitePreferenceBridge {
    static int rule;
    static int getContentSetting(Object profile,int type,Object url,Object other){
        if(type!=ContentSettingsType.REQUEST_DESKTOP_SITE)throw new AssertionError();
        TestHarness.log.add("rule");
        return rule;
    }
}
class ContentUtils {
    static void setUserAgentOverride(WebContents contents,boolean desktop){
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
        check(log.equals(List.of("tab","override","navigation")));
    }
    public static void main(String[] args){
        choice(ContentSetting.ALLOW,true,TabUserAgent.DESKTOP);
        choice(ContentSetting.ALLOW,false,TabUserAgent.MOBILE);
        choice(ContentSetting.BLOCK,true,TabUserAgent.DESKTOP);
        choice(ContentSetting.BLOCK,false,TabUserAgent.MOBILE);
        Tab tab=new Tab();tab.mode=TabUserAgent.MOBILE;log.clear();
        TabUtils.switchUserAgent(tab,true,false);
        check(tab.mode==TabUserAgent.MOBILE);
        check(!tab.web.controller.desktop);
        check(log.equals(List.of("override","navigation")));
        tab.mode=TabUserAgent.DESKTOP;log.clear();
        TabUtils.switchUserAgent(tab,false,false);
        check(tab.mode==TabUserAgent.DESKTOP);
        check(tab.web.controller.desktop);
        check(log.equals(List.of("override","navigation")));
        tab.mode=TabUserAgent.DEFAULT;log.clear();
        TabUtils.switchUserAgent(tab,false,false);
        check(!tab.web.controller.desktop);
        check(tab.mode==TabUserAgent.DEFAULT);
        tab.nativePage=true;
        TabUtils.switchUserAgent(tab,true,false);
        check(!tab.web.controller.reload);
        System.out.println("PASS: automatic navigation cannot override explicit mobile/desktop tab modes");
    }
}
"""


class TabSwitchTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("javac") and shutil.which("java"), "JDK required")
    def test_real_java_tab_switch_method(self):
        patched = patch.patch_tab_user_agent_utils(UPSTREAM)
        # Java host adapters replace package imports, not production behavior.
        source = ("import java.util.*;\n" +
                  "\n".join(line for line in patched.splitlines()
                            if not line.startswith("import ")) + "\n" +
                  ADAPTERS.replace("import java.util.*;\n", ""))
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "TabUtils.java").write_text(source)
            compiled = subprocess.run(["javac", "-d", str(root), str(root / "TabUtils.java")],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(["java", "-ea", "-cp", str(root), "TestHarness"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PASS:", result.stdout)

    def test_idempotent_and_pinned_anchor(self):
        patched = patch.patch_tab_user_agent_utils(UPSTREAM)
        self.assertEqual(patched, patch.patch_tab_user_agent_utils(patched))
        with self.assertRaises(ValueError):
            patch.patch_tab_user_agent_utils("class TabUtils {}")
