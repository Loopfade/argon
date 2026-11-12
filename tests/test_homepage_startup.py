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
    'org/chromium/chrome/browser/tab/EmptyTabObserver.java': '''package org.chromium.chrome.browser.tab;
public class EmptyTabObserver {
 public void onContentChanged(Tab tab) {}
 public void onUrlUpdated(Tab tab) {}
 public void onDestroyed(Tab tab) {}
 public void onCrash(Tab tab) {}
}''',
    'org/chromium/chrome/browser/tab/Tab.java': '''package org.chromium.chrome.browser.tab;
import java.util.ArrayList;
import org.chromium.content_public.browser.WebContents;
public class Tab {
 public boolean home=true, destroyed, error, hidden, incognito, extensionOverride;
 public WebContents contents = new WebContents();
 public final ArrayList<EmptyTabObserver> observers = new ArrayList<>();
 public boolean isDestroyed() { return destroyed; }
 public boolean isShowingErrorPage() { return error; }
 public WebContents getWebContents() { return contents; }
 public void addObserver(EmptyTabObserver observer) { observers.add(observer); }
 public void removeObserver(EmptyTabObserver observer) { observers.remove(observer); }
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


class HomepageStartupTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'), 'JDK required')
    def test_production_readiness_lifecycle(self):
        sources = dict(STUBS)
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
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run(['java', '-ea', '-cp', directory,
                                     'org.chromium.chrome.browser.ntp.HomeStartupFlow'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: first frame', result.stdout)
