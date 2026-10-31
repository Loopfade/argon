"""Compile and exercise the production phone adapter against the pinned NTP API."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/homepage'
DELEGATE_API = Path('chrome/browser/ui/android/omnibox/java/src/org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java')
TOOLBAR_PHONE = Path('chrome/browser/ui/android/toolbar/java/src/org/chromium/chrome/browser/toolbar/top/ToolbarPhone.java')
TOOLBAR_SCROLL_SIGNATURES = (
    '    private void onNtpScrollChanged(float scrollFraction) {',
    '    private void updateLocationBarTranslationOnScroll() {',
    '    private float getLocationBarTranslationY() {',
)
BRIDGE = ROOT / 'chromium_overlay/chrome/android/java/src/org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'


def verify_delegate_api(source):
    if source != (FIXTURES / 'NewTabPageDelegate.java.in').read_text():
        raise ValueError('Chromium NewTabPageDelegate changed; review the homepage adapter and fixture')


def verify_toolbar_scroll_source(source):
    from test_homepage import homepage

    fixture = (FIXTURES / 'toolbar_phone_scroll.java.in').read_text()
    for signature in TOOLBAR_SCROLL_SIGNATURES:
        if homepage.method_source(source, signature) != homepage.method_source(fixture, signature):
            raise ValueError('Chromium toolbar scrolling changed; review the keyboard regression')


def verify_toolbar_startup_source(source):
    # These two excerpts retain their actual order in the pinned constructor.
    blocks = (FIXTURES / 'toolbar_constructor_init.java.in').read_text().strip('\n').split('\n\n')
    if any(source.count(block) != 1 for block in blocks):
        raise ValueError('Chromium toolbar initialization changed; review the startup fixture')
    if source.index(blocks[0]) >= source.index(blocks[1]):
        raise ValueError('Chromium toolbar initialization order changed; review the startup fixture')


def verify_toolbar_capture_source(source):
    from test_homepage import homepage

    fixture = (FIXTURES / 'toolbar_capture.java.in').read_text()
    reviewed = source.replace(homepage.CAPTURE_AFTER, homepage.CAPTURE_BEFORE, 1)
    for signature in (homepage.CAPTURE_SIGNATURE,
                      '        private boolean checkCaptureReadinessResult() {'):
        if homepage.method_source(reviewed, signature) != homepage.method_source(fixture, signature):
            raise ValueError('Chromium toolbar capture changed; review the compositor regression')


# Android and browser boundaries only. The adapter itself and its interface are unmodified.
STUBS = {
    'android/app/Activity.java': """
package android.app;
public class Activity {
    public boolean finishing, destroyed;
    public boolean isFinishing() { return finishing; }
    public boolean isDestroyed() { return destroyed; }
}
""",
    'android/graphics/Point.java': """
package android.graphics;
public class Point {
    public int x, y;
    public void set(int a, int b) { x = a; y = b; }
}
""",
    'android/graphics/Rect.java': """
package android.graphics;
public class Rect {
    public int left, top, right, bottom;
    public void set(int a, int b, int c, int d) { left=a; top=b; right=c; bottom=d; }
    public void set(Rect r) { set(r.left, r.top, r.right, r.bottom); }
}
""",
    'android/view/MotionEvent.java': 'package android.view; public class MotionEvent {}',
    'android/view/View.java': """
package android.view;
public class View {
    public int x, y, touches;
    public void getLocationInWindow(int[] position) { position[0]=x; position[1]=y; }
    public boolean dispatchTouchEvent(MotionEvent event) { touches++; return true; }
}
""",
    'org/chromium/build/annotations/NullMarked.java':
        'package org.chromium.build.annotations; public @interface NullMarked {}',
    'org/chromium/build/annotations/Nullable.java': """
package org.chromium.build.annotations;
import java.lang.annotation.*;
@Target({ElementType.TYPE_USE, ElementType.PARAMETER})
public @interface Nullable {}
""",
    'org/jni_zero/CalledByNative.java': 'package org.jni_zero; public @interface CalledByNative {}',
    'org/jni_zero/NativeMethods.java': 'package org.jni_zero; public @interface NativeMethods {}',
    'org/chromium/base/Callback.java': """
package org.chromium.base;
public interface Callback<T> { void onResult(T value); }
""",
    'org/chromium/ui/base/WindowAndroid.java': """
package org.chromium.ui.base;
import android.app.Activity;
import java.lang.ref.WeakReference;
public class WindowAndroid {
    public Activity activity;
    public WeakReference<Activity> getActivity() { return new WeakReference<>(activity); }
}
""",
    'org/chromium/content_public/browser/WebContents.java': """
package org.chromium.content_public.browser;
import org.chromium.ui.base.WindowAndroid;
public class WebContents {
    public WindowAndroid window;
    public boolean destroyed;
    public WindowAndroid getTopLevelNativeWindow() { return window; }
    public boolean isDestroyed() { return destroyed; }
}
""",
    'org/chromium/content_public/browser/RenderCoordinates.java': """
package org.chromium.content_public.browser;
public class RenderCoordinates {
    private static final RenderCoordinates INSTANCE = new RenderCoordinates();
    public static RenderCoordinates fromWebContents(WebContents contents) { return INSTANCE; }
    public int getContentOffsetYPixInt() { return 112; }
    public float fromLocalCssToPix(float value) { return value * 2; }
}
""",
    'org/chromium/url/GURL.java': """
package org.chromium.url;
public class GURL {
    private final java.net.URI uri;
    public GURL(String value) { uri=java.net.URI.create(value); }
    public String getScheme() { return uri.getScheme(); }
    public String getHost() { return uri.getHost(); }
}
""",
    'org/chromium/chrome/browser/tab/Tab.java': """
package org.chromium.chrome.browser.tab;
import android.view.View;
import org.chromium.content_public.browser.WebContents;
import org.chromium.url.GURL;
public class Tab {
    public boolean incognito, error, destroyed, nativePage;
    public GURL url;
    public WebContents contents;
    public View view = new View();
    public boolean isIncognito() { return incognito; }
    public boolean isShowingErrorPage() { return error; }
    public boolean isDestroyed() { return destroyed; }
    public boolean isNativePage() { return nativePage; }
    public GURL getUrl() { return url; }
    public WebContents getWebContents() { return contents; }
    public View getContentView() { return view; }
}
""",
    'org/chromium/components/omnibox/OmniboxFocusReason.java': """
package org.chromium.components.omnibox;
public class OmniboxFocusReason {
    public static final int FAKE_BOX_TAP=7, FAKE_BOX_LONG_PRESS=8;
}
""",
    'org/chromium/components/omnibox/AutocompleteInput.java': """
package org.chromium.components.omnibox;
public class AutocompleteInput {
    public final int reason;
    public String text;
    public int state;
    public static class AutocompleteState { public static final int ENABLED=1; }
    public AutocompleteInput(int value) { reason=value; }
    public AutocompleteInput setUserText(String value) { text=value; return this; }
    public AutocompleteInput setAutocompleteState(int value) { state=value; return this; }
}
""",
    'org/chromium/chrome/browser/toolbar/ToolbarManager.java': """
package org.chromium.chrome.browser.toolbar;
import java.util.ArrayList;
import java.util.List;
import org.chromium.components.omnibox.AutocompleteInput;
public class ToolbarManager {
    public int refreshes;
    public boolean focused;
    public final Omnibox omnibox = new Omnibox();
    public void onArgonHomeSearchBoxChanged() { refreshes++; }
    public boolean isUrlBarFocused() { return focused; }
    public Omnibox getOmniboxStub() { return omnibox; }
    public class Omnibox {
        public final List<AutocompleteInput> inputs = new ArrayList<>();
        public void beginInput(AutocompleteInput input) { inputs.add(input); focused=true; }
    }
}
""",
    'org/chromium/chrome/browser/ChromeTabbedActivity.java': """
package org.chromium.chrome.browser;
import android.app.Activity;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.toolbar.ToolbarManager;
public class ChromeTabbedActivity extends Activity {
    public Tab tab;
    public final ToolbarManager toolbar = new ToolbarManager();
    public Tab getActivityTab() { return tab; }
    public ToolbarManager getToolbarManager() { return toolbar; }
}
""",
    'org/chromium/chrome/browser/ntp/ArgonHomeBridgeJni.java': """
package org.chromium.chrome.browser.ntp;
import java.util.ArrayList;
import java.util.List;
public class ArgonHomeBridgeJni {
    public static final List<Float> alphas = new ArrayList<>();
    public static final List<Boolean> focuses = new ArrayList<>();
    public static ArgonHomeBridge.Natives get() {
        return (observer, alpha, focused) -> { alphas.add(alpha); focuses.add(focused); };
    }
}
""",
}


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
class HomepageNativeTests(unittest.TestCase):
    def test_toolbar_startup_binds_the_tab_provider_after_initialization(self):
        from test_homepage import homepage, input_tree

        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            original = (src / homepage.TOOLBAR_MANAGER).read_text()
            patched = homepage.patch_toolbar(original)
        # Compile the production phone wrapper expression, stubbing only the
        # stock NTP delegate. Preserve the bound method reference that crashed.
        start = patched.index('return org.chromium.chrome.browser.ntp.ArgonHomeBridge.wrap(')
        end_marker = 'mNtpSearchBoxTransitionPercentageSupplier::set);'
        end = patched.index(end_marker, start) + len(end_marker)
        phone_call = patched[start:end]
        fallback = homepage.method_source(phone_call, 'new ToolbarNtpDelegate() {')
        phone_call = phone_call.replace(fallback, 'NewTabPageDelegate.EMPTY', 1)

        for fixed, source in ((False, original), (True, patched)):
            with self.subTest(fixed=fixed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                statements = re.findall(
                    r'(?m)^[ \t]*(?:mActivityTabProvider = tabProvider;|'
                    r'NewTabPageDelegate ntpDelegate = createNewTabPageDelegate\(\);)[ \t]*$',
                    source)
                self.assertEqual(len(statements), 2)
                initialization = '\n'.join(statements)
                toolbar = STUBS['org/chromium/chrome/browser/toolbar/ToolbarManager.java']
                toolbar = toolbar.replace('public class ToolbarManager {', '''
import android.view.View;
import org.chromium.chrome.browser.ActivityTabProvider;
import org.chromium.chrome.browser.omnibox.NewTabPageDelegate;

public class ToolbarManager {
    private final ActivityTabProvider mActivityTabProvider;
    private final View mToolbarLayout = new View();
    private final Transition mNtpSearchBoxTransitionPercentageSupplier = new Transition();
    public final NewTabPageDelegate delegate;
    private static class Transition { void set(Float fraction) {} }
    public ToolbarManager() { this(new ActivityTabProvider()); }
    public ToolbarManager(ActivityTabProvider tabProvider) {
ARGON_TOOLBAR_INITIALIZATION
        delegate = ntpDelegate;
    }
    private NewTabPageDelegate createNewTabPageDelegate() {
ARGON_PHONE_CALL
    }
'''.replace('ARGON_TOOLBAR_INITIALIZATION', initialization)
   .replace('ARGON_PHONE_CALL', phone_call), 1)
                sources = dict(STUBS)
                sources['org/chromium/chrome/browser/toolbar/ToolbarManager.java'] = toolbar
                sources['org/chromium/chrome/browser/ActivityTabProvider.java'] = '''
package org.chromium.chrome.browser;
import org.chromium.chrome.browser.tab.Tab;
public class ActivityTabProvider {
    public int reads;
    public Tab get() { reads++; return null; }
}
'''
                sources['org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java'] = (
                    FIXTURES / 'NewTabPageDelegate.java.in').read_text()
                sources['org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'] = BRIDGE.read_text()
                sources['ToolbarStartupFlow.java'] = (FIXTURES / 'ToolbarStartupFlow.java.in').read_text()
                for name, text in sources.items():
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                compiled = subprocess.run(
                    ['javac', '--release', '17', '-d', str(root),
                     *map(str, root.rglob('*.java'))], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run(['java', '-cp', str(root), 'ToolbarStartupFlow'],
                                        capture_output=True, text=True)
                if fixed:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('PASS: toolbar startup', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('java.lang.NullPointerException', result.stderr)
                    self.assertIn('java.util.Objects.requireNonNull', result.stderr)

    def test_native_homepage_flow(self):
        from test_homepage import homepage

        for fixed in (False, True):
            with self.subTest(fixed=fixed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sources = dict(STUBS)
                sources['org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java'] = (
                    FIXTURES / 'NewTabPageDelegate.java.in').read_text()
                sources['org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'] = BRIDGE.read_text()
                sources['ArgonHomeBridgeFlow.java'] = (FIXTURES / 'ArgonHomeBridgeFlow.java.in').read_text()
                sources['ToolbarPhoneScrollFlow.java'] = (
                    FIXTURES / 'ToolbarPhoneScrollFlow.java.in').read_text().replace(
                        'ARGON_SCROLL_METHODS', (FIXTURES / 'toolbar_phone_scroll.java.in').read_text())
                capture = (FIXTURES / 'toolbar_capture.java.in').read_text()
                if fixed:
                    capture = homepage.patch_toolbar_capture(capture)
                sources['ToolbarCaptureFlow.java'] = (
                    FIXTURES / 'ToolbarCaptureFlow.java.in').read_text().replace(
                        'ARGON_CAPTURE_METHODS', capture)
                for name, text in sources.items():
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                compile_result = subprocess.run(
                    ['javac', '--release', '17', '-d', str(root),
                     *map(str, root.rglob('*.java'))], capture_output=True, text=True)
                self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
                result = subprocess.run(
                    ['java', '-cp', str(root), 'ArgonHomeBridgeFlow'], capture_output=True, text=True)
                if fixed:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('PASS: native homepage', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Measured WebUI fakebox must replace the stale visible omnibox bitmap',
                                  result.stderr)


if __name__ == '__main__':
    unittest.main()
