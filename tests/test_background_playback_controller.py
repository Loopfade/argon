"""Exercise the production controller with deterministic Android/Chromium adapters."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "org/chromium/chrome/browser/media/"
SOURCE = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java/src" / PACKAGE
CONTROLLER = SOURCE / "ArgonBackgroundPlaybackController.java"

# Only platform services are substituted. The actual controller and budget policy
# run unchanged, including the MediaSessionObserver constructor's null rejection.
ADAPTERS = {
    "android/app/Activity.java": '''package android.app;
public class Activity extends android.content.Context {
  public boolean focused = true;
  public boolean hasWindowFocus() { return focused; }
}''',
    "android/app/KeyguardManager.java": '''package android.app;
public class KeyguardManager { public boolean isKeyguardLocked() { return false; } }''',
    "android/content/Context.java": '''package android.content;
public class Context {
  public final java.util.List<BroadcastReceiver> receivers = new java.util.ArrayList<>();
  public <T> T getSystemService(Class<T> type) {
    if (type == android.os.PowerManager.class) return type.cast(new android.os.PowerManager());
    if (type == android.app.KeyguardManager.class) return type.cast(new android.app.KeyguardManager());
    return null;
  }
  public android.content.res.Resources getResources() { return new android.content.res.Resources(); }
  public void unregisterReceiver(BroadcastReceiver receiver) { receivers.remove(receiver); }
}''',
    "android/content/BroadcastReceiver.java": '''package android.content;
public abstract class BroadcastReceiver { public abstract void onReceive(Context c, Intent i); }''',
    "android/content/Intent.java": '''package android.content;
public class Intent {
  public static final String ACTION_SCREEN_OFF = "off", ACTION_SCREEN_ON = "on", ACTION_USER_PRESENT = "present";
}''',
    "android/content/IntentFilter.java": '''package android.content;
public class IntentFilter { public void addAction(String action) {} }''',
    "android/content/SharedPreferences.java": '''package android.content;
public interface SharedPreferences {
  interface OnSharedPreferenceChangeListener { void onSharedPreferenceChanged(SharedPreferences p, String key); }
  void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener);
  void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener listener);
}''',
    "android/content/res/Resources.java": '''package android.content.res;
public class Resources {
  public java.io.InputStream openRawResource(int id) {
    return new java.io.ByteArrayInputStream("ARGON_OVERRIDE_ACTIVE;".getBytes(java.nio.charset.StandardCharsets.UTF_8));
  }
}''',
    "android/os/Looper.java": '''package android.os;
public class Looper { public static Looper getMainLooper() { return new Looper(); } }''',
    "android/os/SystemClock.java": '''package android.os;
public class SystemClock { public static long now; public static long elapsedRealtime() { return now; } }''',
    "android/os/PowerManager.java": '''package android.os;
public class PowerManager { public boolean isInteractive() { return true; } }''',
    "android/os/Handler.java": '''package android.os;
public class Handler {
  private final java.util.Map<Runnable, Long> tasks = new java.util.LinkedHashMap<>();
  private static final java.util.List<Handler> handlers = new java.util.ArrayList<>();
  public Handler(Looper looper) { handlers.add(this); }
  public boolean post(Runnable task) { tasks.put(task, SystemClock.now); return true; }
  public boolean postDelayed(Runnable task, long delay) { tasks.put(task, SystemClock.now + delay); return true; }
  public void removeCallbacks(Runnable task) { tasks.remove(task); }
  public void removeCallbacksAndMessages(Object token) { tasks.clear(); }
  public static int pendingCount() { return handlers.stream().mapToInt(h -> h.tasks.size()).sum(); }
  public static void runPending() {
    for (int attempts = 0; attempts < 100; attempts++) {
      Runnable ready = null;
      for (Handler handler : handlers) {
        for (var item : handler.tasks.entrySet()) {
          if (item.getValue() <= SystemClock.now) { ready = item.getKey(); break; }
        }
        if (ready != null) { handler.tasks.remove(ready); break; }
      }
      if (ready == null) return;
      ready.run();
    }
    throw new AssertionError("Controller posted an unbounded immediate loop");
  }
}''',
    "org/chromium/base/ContextUtils.java": '''package org.chromium.base;
public class ContextUtils {
  public static final android.content.Context context = new android.content.Context();
  public static android.content.Context getApplicationContext() { return context; }
  public static void registerNonExportedBroadcastReceiver(android.content.Context c,
      android.content.BroadcastReceiver r, android.content.IntentFilter f) { c.receivers.add(r); }
}''',
    "org/chromium/base/ApplicationStatus.java": '''package org.chromium.base;
public class ApplicationStatus {
  public interface ActivityStateListener { void onActivityStateChange(android.app.Activity a, int state); }
  public static final java.util.List<ActivityStateListener> listeners = new java.util.ArrayList<>();
  public static void registerStateListenerForActivity(ActivityStateListener l, android.app.Activity a) { listeners.add(l); }
  public static void unregisterActivityStateListener(ActivityStateListener l) { listeners.remove(l); }
}''',
    "org/chromium/build/annotations/NullMarked.java": '''package org.chromium.build.annotations;
public @interface NullMarked {}''',
    "org/chromium/build/annotations/Nullable.java": '''package org.chromium.build.annotations;
@java.lang.annotation.Target({java.lang.annotation.ElementType.TYPE_USE})
public @interface Nullable {}''',
    "org/chromium/chrome/R.java": '''package org.chromium.chrome;
public class R { public static class raw { public static final int argon_background_playback = 1; } }''',
    "org/chromium/url/GURL.java": '''package org.chromium.url;
public class GURL {
  private final java.net.URI uri;
  public GURL(String value) { uri = java.net.URI.create(value); }
  public String getScheme() { return uri.getScheme(); }
  public String getHost() { return uri.getHost() == null ? "" : uri.getHost(); }
}''',
    "org/chromium/chrome/browser/tab/Tab.java": '''package org.chromium.chrome.browser.tab;
public class Tab {
  public org.chromium.content_public.browser.WebContents contents;
  public boolean incognito, destroyed;
  public Tab(org.chromium.content_public.browser.WebContents c) { contents = c; }
  public boolean isOffTheRecord() { return incognito; }
  public boolean isDestroyed() { return destroyed; }
  public boolean isUserInteractable() { return true; }
  public org.chromium.content_public.browser.WebContents getWebContents() { return contents; }
  public org.chromium.url.GURL getUrl() { return new org.chromium.url.GURL("https://video.example/"); }
}''',
    "org/chromium/chrome/browser/tabmodel/TabModelSelector.java": '''package org.chromium.chrome.browser.tabmodel;
public class TabModelSelector {
  public final java.util.List<org.chromium.chrome.browser.tab.Tab> tabs = new java.util.ArrayList<>();
  public TabModelSelectorTabObserver observer;
}''',
    "org/chromium/chrome/browser/tabmodel/TabModelSelectorTabObserver.java": '''package org.chromium.chrome.browser.tabmodel;
import org.chromium.chrome.browser.tab.Tab;
public class TabModelSelectorTabObserver {
  private final TabModelSelector selector;
  public TabModelSelectorTabObserver(TabModelSelector s) {
    selector = s; s.observer = this;
    for (Tab tab : s.tabs) onTabRegistered(tab);
  }
  protected void onTabRegistered(Tab tab) {}
  protected void onTabUnregistered(Tab tab) {}
  public void onContentChanged(Tab tab) {}
  public void onPageLoadStarted(Tab tab, org.chromium.url.GURL url) {}
  public void onPageLoadFinished(Tab tab, org.chromium.url.GURL url) {}
  public void onUrlUpdated(Tab tab) {}
  public void onInteractabilityChanged(Tab tab, boolean interactable) {}
  public void onDestroyed(Tab tab) {}
  public void destroy() {
    for (Tab tab : selector.tabs) onTabUnregistered(tab);
    selector.observer = null;
  }
}''',
    "org/chromium/content_public/browser/MediaSession.java": '''package org.chromium.content_public.browser;
public class MediaSession {
  public final java.util.List<MediaSessionObserver> observers = new java.util.ArrayList<>();
  public boolean controllable, suspended;
  public static MediaSession fromWebContents(WebContents contents) { return contents.session; }
  public void addObserver(MediaSessionObserver observer) {
    observers.add(observer);
    observer.mediaSessionStateChanged(controllable, suspended);
  }
  public void removeObserver(MediaSessionObserver observer) { observers.remove(observer); }
  public void destroy() {
    for (var observer : java.util.List.copyOf(observers)) observer.mediaSessionDestroyed();
    for (var observer : java.util.List.copyOf(observers)) observer.stopObserving();
  }
}''',
    "org/chromium/content_public/browser/MediaSessionObserver.java": '''package org.chromium.content_public.browser;
public abstract class MediaSessionObserver {
  private MediaSession session;
  protected MediaSessionObserver(MediaSession session) { this.session = session; session.addObserver(this); }
  public final MediaSession getMediaSession() { return session; }
  public void mediaSessionStateChanged(boolean controllable, boolean suspended) {}
  public void mediaSessionDestroyed() {}
  public final void stopObserving() {
    if (session != null) { session.removeObserver(this); session = null; }
  }
}''',
    "org/chromium/content_public/browser/WebContents.java": '''package org.chromium.content_public.browser;
public class WebContents {
  public MediaSession session;
  public boolean destroyed;
  public int pauses;
  public final java.util.List<WebContentsObserver> observers = new java.util.ArrayList<>();
  public boolean isDestroyed() { return destroyed; }
  public void evaluateJavaScript(String script, Object callback) {}
  public void suspendAllMediaPlayers() { pauses++; }
  public void createSession(MediaSession value) {
    session = value;
    for (var observer : java.util.List.copyOf(observers)) observer.mediaSessionCreated(value);
  }
  public void destroy() {
    destroyed = true;
    for (var observer : java.util.List.copyOf(observers)) observer.webContentsDestroyed();
  }
}''',
    "org/chromium/content_public/browser/WebContentsObserver.java": '''package org.chromium.content_public.browser;
public class WebContentsObserver {
  private WebContents contents;
  public WebContentsObserver(WebContents contents) { observe(contents); }
  public final void observe(WebContents value) {
    if (contents == value) return;
    if (contents != null) contents.observers.remove(this);
    contents = value;
    if (contents != null) contents.observers.add(this);
  }
  public void mediaSessionCreated(MediaSession session) {}
  public void webContentsDestroyed() {}
}''',
    PACKAGE + "ArgonBackgroundPlaybackStore.java": '''package org.chromium.chrome.browser.media;
public class ArgonBackgroundPlaybackStore {
  private static final ArgonBackgroundPlaybackStore instance = new ArgonBackgroundPlaybackStore();
  public static ArgonBackgroundPlaybackStore get() { return instance; }
  public final ArgonBackgroundPlaybackPolicy policy = new ArgonBackgroundPlaybackPolicy();
  public final Preferences preferences = new Preferences();
  public void reload() {}
  public static String hostFromUrl(org.chromium.url.GURL url) {
    return "https".equals(url.getScheme()) ? url.getHost() : "";
  }
  public static class Preferences implements android.content.SharedPreferences {
    public final java.util.List<OnSharedPreferenceChangeListener> listeners = new java.util.ArrayList<>();
    public void registerOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l) { listeners.add(l); }
    public void unregisterOnSharedPreferenceChangeListener(OnSharedPreferenceChangeListener l) { listeners.remove(l); }
  }
}''',
}


class BackgroundPlaybackControllerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required for controller lifecycle regression")
    def test_startup_and_media_session_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix="argon-controller-") as directory:
            root = Path(directory)
            sources = []
            for name, code in ADAPTERS.items():
                target = root / "src" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(code)
                sources.append(str(target))
            sources += [str(CONTROLLER), str(SOURCE / "ArgonBackgroundPlaybackPolicy.java"),
                        str(ROOT / "tests/java/ArgonBackgroundPlaybackControllerTest.java")]
            compiler = (["javac"] if shutil.which("javac")
                        else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"])
            compiled = subprocess.run([*compiler, "-d", str(root / "classes"), *sources],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run(
                ["java", "-cp", str(root / "classes"),
                 "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackControllerTest"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: production controller startup and media lifecycle", result.stdout)
