"""Run the production UA controller through desktop toggles and delayed renderer replies."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_user_agent import ADAPTERS, MODEL, ROOT

CONTROLLER = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java/src/org/chromium/chrome/browser/base/ArgonUserAgentController.java"
CONTENT_UTILS = Path("chrome/browser/android/content/java/src/org/chromium/chrome/browser/content/ContentUtils.java")
PREFIX = "org/chromium/chrome/browser/"
RUNTIME = {
    "android/content/Context.java": "package android.content; public class Context {}",
    "org/jni_zero/NativeMethods.java": "package org.jni_zero; public @interface NativeMethods {}",
    "org/jni_zero/JniType.java": """package org.jni_zero; import java.lang.annotation.*;
        @Target({ElementType.TYPE_USE,ElementType.PARAMETER,ElementType.METHOD})
        public @interface JniType { String value(); }""",
    "org/chromium/url/GURL.java": """package org.chromium.url;
        public class GURL {
            private final java.net.URI uri;
            public GURL(String value) { uri=java.net.URI.create(value); }
            public String getScheme() { return uri.getScheme(); }
            public String getHost() { return uri.getHost()==null ? "" : uri.getHost(); }
        }""",
    PREFIX + "profiles/Profile.java": """package org.chromium.chrome.browser.profiles;
        public class Profile {
            public boolean desktop;
            public final boolean incognito;
            public final java.util.Map<String,Boolean> sites=new java.util.HashMap<>();
            public Profile(boolean incognito) { this.incognito=incognito; }
        }""",
    "org/chromium/components/content_settings/ContentSetting.java": """
        package org.chromium.components.content_settings;
        public class ContentSetting { public static final int ALLOW=1,BLOCK=2; }""",
    "org/chromium/components/content_settings/ContentSettingsType.java": """
        package org.chromium.components.content_settings;
        public class ContentSettingsType { public static final int REQUEST_DESKTOP_SITE=1; }""",
    "org/chromium/components/browser_ui/site_settings/WebsitePreferenceBridge.java": """
        package org.chromium.components.browser_ui.site_settings;
        import org.chromium.chrome.browser.profiles.Profile;
        import org.chromium.components.content_settings.ContentSetting;
        public class WebsitePreferenceBridge {
            public static void setDefaultContentSetting(Profile p,int type,int value) {
                p.desktop=value==ContentSetting.ALLOW;
            }
        }""",
    PREFIX + "desktop_site/DesktopSiteUtils.java": """
        package org.chromium.chrome.browser.desktop_site;
        import android.content.Context;
        import org.chromium.chrome.browser.profiles.Profile;
        import org.chromium.url.GURL;
        public class DesktopSiteUtils {
            public static void setRequestDesktopSiteContentSettingsForUrl(Profile p,GURL u,boolean d) {
                p.sites.put(u.getHost(),d);
            }
            public static boolean shouldOverrideDesktopSite(Profile p,GURL u,Context c) {
                return p.sites.getOrDefault(u.getHost(),p.desktop);
            }
        }""",
    "org/chromium/content_public/browser/WebContents.java": """
        package org.chromium.content_public.browser;
        import org.chromium.chrome.browser.content.ContentUtilsJni;
        public class WebContents {
            public final Navigation navigation=new Navigation();
            public String desktopUa="";
            public boolean destroyed;
            public Navigation getNavigationController() { return navigation; }
            public boolean isDestroyed() { return destroyed; }
            public String requestUserAgent() {
                return navigation.desktop ? desktopUa : ContentUtilsJni.rendererUa;
            }
            public class Navigation {
                public boolean desktop;
                public boolean getUseDesktopUserAgent() { return desktop; }
                public void setUseDesktopUserAgent(boolean d,boolean reload,boolean skip) { desktop=d; }
            }
        }""",
    PREFIX + "content/ContentUtils.java": """
        package org.chromium.chrome.browser.content;
        import org.chromium.content_public.browser.WebContents;
        public class ContentUtils {
            public static void setUserAgentOverride(WebContents c,boolean next) {
                ContentUtilsJni.get().setUserAgentOverride(c,next);
            }
            public static void updateUserAgent(String mobile,String desktop,Runnable ready) {
                ContentUtilsJni.get().updateUserAgent(mobile,desktop,ready);
            }
            public interface Natives {
                void setUserAgentOverride(WebContents c,boolean next);
                void updateUserAgent(String mobile,String desktop,Runnable ready);
                String getBrowserUserAgent();
            }
        }""",
    PREFIX + "content/ContentUtilsJni.java": """
        package org.chromium.chrome.browser.content;
        import org.chromium.base.CommandLine;
        import org.chromium.content_public.browser.WebContents;
        public class ContentUtilsJni implements ContentUtils.Natives {
            private static final ContentUtilsJni INSTANCE=new ContentUtilsJni();
            public static final java.util.List<Runnable> replies=new java.util.ArrayList<>();
            public static String rendererUa="";
            private static String mobileUa,desktopUa;
            public static ContentUtils.Natives get() { return INSTANCE; }
            public String getBrowserUserAgent() {
                return mobileUa==null ? CommandLine.getInstance().values.get("argon-user-agent") : mobileUa;
            }
            public void setUserAgentOverride(WebContents c,boolean next) {
                c.desktopUa=desktopUa==null ? CommandLine.getInstance().values.get("argon-user-agent-desktop") : desktopUa;
            }
            public void updateUserAgent(String mobile,String desktop,Runnable ready) {
                mobileUa=mobile; desktopUa=desktop;
                String value=mobile;
                replies.add(() -> { rendererUa=value; ready.run(); });
            }
            public static void acknowledge() { replies.remove(0).run(); }
        }""",
    PREFIX + "tab/TabObserver.java": """package org.chromium.chrome.browser.tab;
        import org.chromium.url.GURL;
        public interface TabObserver {
            default void onShown(Tab tab,int type) {}
            default void onPageLoadStarted(Tab tab,GURL url) {}
            default void onDestroyed(Tab tab) {}
        }""",
    PREFIX + "tab/Tab.java": """package org.chromium.chrome.browser.tab;
        import android.content.Context;
        import org.chromium.chrome.browser.base.ArgonUserAgentController;
        import org.chromium.chrome.browser.content.ContentUtils;
        import org.chromium.chrome.browser.desktop_site.DesktopSiteUtils;
        import org.chromium.chrome.browser.profiles.Profile;
        import org.chromium.content_public.browser.WebContents;
        import org.chromium.url.GURL;
        public class Tab {
            public final Profile profile;
            public WebContents contents=new WebContents();
            public final java.util.List<TabObserver> observers=new java.util.ArrayList<>();
            public boolean hidden,closed,destroyed,nativePage;
            public int reloads;
            public GURL url;
            public Tab(Profile p,String url) { profile=p; this.url=new GURL(url); }
            public void initialize() {
                ArgonUserAgentController.onTabInitialized(this);
                boolean mode=DesktopSiteUtils.shouldOverrideDesktopSite(profile,url,getContext());
                ContentUtils.setUserAgentOverride(contents,mode);
                contents.navigation.setUseDesktopUserAgent(mode,false,false);
            }
            public void show() { hidden=false; for(var o:observers) o.onShown(this,0); }
            public void destroy() { destroyed=true; for(var o:observers) o.onDestroyed(this); }
            public void addObserver(TabObserver o) { observers.add(o); }
            public Profile getProfile() { return profile; }
            public boolean isOffTheRecord() { return profile.incognito; }
            public WebContents getWebContents() { return contents; }
            public GURL getUrl() { return url; }
            public Context getContext() { return new Context(); }
            public boolean isDestroyed() { return destroyed; }
            public boolean isClosing() { return closed; }
            public boolean isNativePage() { return nativePage; }
            public boolean isHidden() { return hidden; }
            public void reload() {
                reloads++; for(var o:observers) o.onPageLoadStarted(this,url);
            }
            public void checkbox(boolean desktop) {
                DesktopSiteUtils.setRequestDesktopSiteContentSettingsForUrl(profile,url,desktop);
                contents.navigation.setUseDesktopUserAgent(desktop,true,true);
            }
        }""",
}


def run_runtime_flow(content_utils=None):
    compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
    adapters = dict(ADAPTERS)
    adapters.update(RUNTIME)
    if content_utils is not None:
        adapters[PREFIX + "content/ContentUtils.java"] = content_utils
    with tempfile.TemporaryDirectory(prefix="argon-ua-runtime-") as directory:
        root = Path(directory)
        sources = [MODEL, CONTROLLER, ROOT / "tests/java/ArgonUserAgentRuntimeTest.java"]
        for path, text in adapters.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            sources.append(target)
        result = subprocess.run([*compiler, "-d", str(root / "classes"), *map(str, sources)],
                                text=True, capture_output=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        result = subprocess.run(["java", "-ea", "-cp", str(root / "classes"),
                                 "org.chromium.chrome.browser.base.ArgonUserAgentRuntimeTest"],
                                text=True, capture_output=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return result.stdout


class UserAgentRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_live_presets_desktop_checkbox_and_tab_lifecycle(self):
        self.assertIn("PASS: live User-Agent", run_runtime_flow())
