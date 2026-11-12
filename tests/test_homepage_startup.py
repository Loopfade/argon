"""Exercise the production launch readiness helper with lifecycle adapters."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / 'chromium_overlay/chrome/android/java/src/org/chromium/chrome/browser/ntp/ArgonHomeStartup.java'

STUBS = {
    'android/os/Looper.java': '''package android.os;
public class Looper { public static Looper getMainLooper() { return new Looper(); } }''',
    'android/os/Handler.java': '''package android.os;
import java.util.ArrayList;
public class Handler {
 public static final ArrayList<Runnable> tasks = new ArrayList<>();
 public Handler(Looper looper) {}
 public boolean postDelayed(Runnable task, long delay) {
  if (delay != 3000) throw new AssertionError("failure deadline changed");
  tasks.add(task); return true;
 }
 public void removeCallbacks(Runnable task) { tasks.remove(task); }
 public static void expire() { for (var task : new ArrayList<>(tasks)) task.run(); }
}''',
    'org/chromium/build/annotations/NullMarked.java': '''package org.chromium.build.annotations;
public @interface NullMarked {}''',
    'org/chromium/build/annotations/Nullable.java': '''package org.chromium.build.annotations;
import java.lang.annotation.*;
@Target({ElementType.TYPE_USE,ElementType.PARAMETER}) public @interface Nullable {}''',
    'org/chromium/content_public/browser/WebContents.java': '''package org.chromium.content_public.browser;
public class WebContents { public boolean destroyed; public boolean isDestroyed() { return destroyed; } }''',
    'org/chromium/chrome/browser/exit/ArgonExitCoordinator.java': '''package org.chromium.chrome.browser.exit;
public class ArgonExitCoordinator {
 public static boolean blocking;
 public static boolean isBlockingPages() { return blocking; }
}''',
    'org/chromium/chrome/browser/tab/TabObserver.java': '''package org.chromium.chrome.browser.tab;
public interface TabObserver {
 default void onContentChanged(Tab tab) {}
 default void onUrlUpdated(Tab tab) {}
 default void onDestroyed(Tab tab) {}
 default void onCrash(Tab tab) {}
}''',
    'org/chromium/chrome/browser/tab/Tab.java': '''package org.chromium.chrome.browser.tab;
import java.util.ArrayList;
import org.chromium.content_public.browser.WebContents;
public class Tab {
 public static class LoadUrlResult {}
 public boolean home=true, destroyed, error, hidden, incognito, extensionOverride;
 public WebContents contents = new WebContents();
 public final ArrayList<TabObserver> observers = new ArrayList<>();
 public boolean isDestroyed() { return destroyed; }
 public boolean isShowingErrorPage() { return error; }
 public WebContents getWebContents() { return contents; }
 public void addObserver(TabObserver observer) { observers.add(observer); }
 public void removeObserver(TabObserver observer) { observers.remove(observer); }
 public void changed() { for (var observer : new ArrayList<>(observers)) observer.onContentChanged(this); }
 public void urlChanged() { for (var observer : new ArrayList<>(observers)) observer.onUrlUpdated(this); }
 public void crash() { for (var observer : new ArrayList<>(observers)) observer.onCrash(this); }
 public void destroy() {
  destroyed=true; for (var observer : new ArrayList<>(observers)) observer.onDestroyed(this);
 }
}''',
    'org/chromium/chrome/browser/ntp/ArgonHomeBridge.java': '''package org.chromium.chrome.browser.ntp;
import org.chromium.chrome.browser.tab.Tab;
public class ArgonHomeBridge {
 public static boolean isHomeStartup(Tab tab) {
  return tab != null && tab.home && !tab.destroyed && !tab.error
    && !tab.incognito && !tab.extensionOverride;
 }
}''',
}


def run_startup_flow(tab_observer_source=None):
    sources = dict(STUBS)
    if tab_observer_source is not None:
        # Compile the complete pinned interface, not a replacement that can
        # accidentally invent an obsolete superclass such as EmptyTabObserver.
        sources['org/chromium/chrome/browser/tab/TabObserver.java'] = tab_observer_source
        # Environment types used by callbacks which this helper does not call.
        for name in (
                'android.graphics.Bitmap', 'org.chromium.base.Token',
                'org.chromium.chrome.browser.browser_controls.BrowserControlsOffsetTagsInfo',
                'org.chromium.components.find_in_page.FindMatchRectsDetails',
                'org.chromium.components.find_in_page.FindNotificationDetails',
                'org.chromium.content_public.browser.LoadUrlParams',
                'org.chromium.content_public.browser.NavigationHandle',
                'org.chromium.ui.base.WindowAndroid', 'org.chromium.url.GURL'):
            package, simple = name.rsplit('.', 1)
            sources[name.replace('.', '/') + '.java'] = (
                f'package {package}; public class {simple} {{}}')
        for name in (
                'org.chromium.cc.input.BrowserControlsState',
                'org.chromium.components.tabs.TabAlert',
                'org.chromium.chrome.browser.tab.TabSelectionType',
                'org.chromium.chrome.browser.tab.TabHidingType'):
            package, simple = name.rsplit('.', 1)
            sources[name.replace('.', '/') + '.java'] = (
                f'package {package}; public @interface {simple} {{}}')
        sources['org/chromium/ui/mojom/VirtualKeyboardMode.java'] = '''package org.chromium.ui.mojom;
public class VirtualKeyboardMode { public @interface EnumType {} }'''
    sources['org/chromium/chrome/browser/ntp/ArgonHomeStartup.java'] = PRODUCTION.read_text()
    sources['org/chromium/chrome/browser/ntp/HomeStartupFlow.java'] = (
        ROOT / 'tests/fixtures/homepage/HomeStartupFlow.java.in').read_text()
    compiler = ['javac'] if shutil.which('javac') else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path, text in sources.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        compiled = subprocess.run([*compiler, '--release', '17', '-d', directory,
                                   *map(str, root.rglob('*.java'))], capture_output=True, text=True)
        if compiled.returncode:
            raise ValueError('Homepage startup compilation failed:\n' + compiled.stderr)
        result = subprocess.run(['java', '-ea', '-cp', directory,
                                 'org.chromium.chrome.browser.ntp.HomeStartupFlow'],
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return result.stdout


class HomepageStartupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_production_readiness_lifecycle(self):
        self.assertIn('PASS: first frame', run_startup_flow())
