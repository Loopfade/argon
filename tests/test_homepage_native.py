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
PHONE_TOKEN = TOOLBAR_PHONE.with_name('PhoneCaptureStateToken.java')
RESOURCE_COORDINATOR = Path('components/browser_ui/widget/android/java/src/org/chromium/components/browser_ui/widget/ViewResourceCoordinatorLayout.java')
VIEW_RESOURCE_ADAPTER = Path('ui/android/java/src/org/chromium/ui/resources/dynamics/ViewResourceAdapter.java')
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


def verify_phone_capture_source(source, token):
    from test_homepage import homepage

    reviewed = source
    for before, after in (
        (homepage.PHONE_RECT_BEFORE, homepage.PHONE_RECT_AFTER),
        (homepage.PHONE_READY_BEFORE, homepage.PHONE_READY_AFTER),
        (homepage.PHONE_STATUS_BEFORE, homepage.PHONE_STATUS_AFTER),
        (homepage.PHONE_TEXTURE_BEFORE, homepage.PHONE_TEXTURE_AFTER),
        (homepage.PHONE_VISIBILITY_BEFORE, homepage.PHONE_VISIBILITY_AFTER),
    ):
        reviewed = reviewed.replace(after, before, 1)
    fixture = (FIXTURES / 'toolbar_phone_capture.java.in').read_text()
    for signature in (homepage.PHONE_RECT_SIGNATURE, homepage.PHONE_READY_SIGNATURE,
                      homepage.PHONE_TEXTURE_SIGNATURE,
                      '    private boolean isLocationBarCurrentlyShown() {'):
        if homepage.method_source(reviewed, signature) != homepage.method_source(fixture, signature):
            raise ValueError('Chromium phone capture changed; review the compositor visibility regression')
    if token != (FIXTURES / 'PhoneCaptureStateToken.java.in').read_text():
        raise ValueError('Chromium snapshot token changed; review the compositor visibility regression')


def verify_activation_sources(manager, container, coordinator, adapter):
    from test_homepage import homepage

    manager = manager.replace(homepage.TOOLBAR_ACTIVATION_AFTER, homepage.TOOLBAR_ACTIVATION_BEFORE, 1)
    if homepage.method_source(manager, homepage.TOOLBAR_ACTIVATION_SIGNATURE) != (
            FIXTURES / 'toolbar_activation.java.in').read_text().rstrip('\n'):
        raise ValueError('Chromium toolbar activation changed; review the first-frame regression')
    fixture = (FIXTURES / 'toolbar_activation_capture.java.in').read_text()
    for source, signature in (
        (container, '        protected boolean isReadyForCapture() {'),
        (coordinator, '    public void onDescendantInvalidated(View child, View target) {'),
        (adapter, '    protected boolean isDirty() {'),
        (adapter, '    public void onResourceRequested() {'),
        (adapter, '    public void invalidate(@Nullable Rect dirtyRect) {'),
    ):
        if homepage.method_source(source, signature) != homepage.method_source(fixture, signature):
            raise ValueError('Chromium resource invalidation changed; review the first-frame regression')


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
    public void setEmpty() { set(0, 0, 0, 0); }
    public int width() { return right - left; }
    public boolean isEmpty() { return left>=right || top>=bottom; }
    public void union(Rect r) {
        if(r.isEmpty()) return;
        if(isEmpty()) { set(r); return; }
        left=Math.min(left,r.left); top=Math.min(top,r.top);
        right=Math.max(right,r.right); bottom=Math.max(bottom,r.bottom);
    }
    public void offset(int x, int y) { left+=x; right+=x; top+=y; bottom+=y; }
}
""",
    'android/view/MotionEvent.java': 'package android.view; public class MotionEvent {}',
    'android/view/View.java': """
package android.view;
public class View {
    public int x, y, touches;
    public final java.util.List<Runnable> preDraw = new java.util.ArrayList<>();
    public float getX() { return x; }
    public float getY() { return y; }
    public int getWidth() { return 600; }
    public int getHeight() { return 56; }
    public View getRootView() { return this; }
    public void postInvalidateOnAnimation() {}
    public void dispatchPreDraw() {
        var tasks = new java.util.ArrayList<>(preDraw); preDraw.clear();
        for(var task : tasks) task.run();
    }
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
    public boolean isEmpty() { return uri.toString().isEmpty(); }
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


CAPTURE_STUBS = {
    'android/content/res/ColorStateList.java': '''
package android.content.res;
public class ColorStateList {
    private final int color;
    public ColorStateList(int color) { this.color = color; }
    public int getDefaultColor() { return color; }
}
''',
    'androidx/annotation/ColorInt.java': 'package androidx.annotation; public @interface ColorInt {}',
    'androidx/annotation/DrawableRes.java': 'package androidx.annotation; public @interface DrawableRes {}',
    'org/chromium/chrome/browser/browser_controls/BrowserControlsStateProvider.java': '''
package org.chromium.chrome.browser.browser_controls;
public class BrowserControlsStateProvider { public @interface ControlsPosition {} }
''',
    'org/chromium/chrome/browser/toolbar/optional_button/ButtonData.java': '''
package org.chromium.chrome.browser.toolbar.optional_button;
public interface ButtonData {}
''',
    'org/chromium/chrome/browser/toolbar/top/ToolbarPhone.java': '''
package org.chromium.chrome.browser.toolbar.top;
public class ToolbarPhone { public @interface VisualState { int NORMAL=0, NEW_TAB_NORMAL=1; } }
''',
    'org/chromium/chrome/browser/toolbar/top/ToolbarSnapshotDifference.java': '''
package org.chromium.chrome.browser.toolbar.top;
public @interface ToolbarSnapshotDifference {
    int NONE=0, NULL=1, TINT=2, THEME_COLOR=3, TAB_COUNT=4, OPTIONAL_BUTTON=5, VISUAL_STATE=6,
        SECURITY_ICON=7, SHOWING_UPDATE_BADGE=8, PAINT_PREVIEW=9, LOCATION_BAR_WIDTH=10,
        URL_TEXT=11, HOME_BUTTON=12, CONTROLS_POSITION=13, UNKNOWN=14;
}
''',
    'org/chromium/chrome/browser/toolbar/top/VisibleUrlText.java': '''
package org.chromium.chrome.browser.toolbar.top;
public class VisibleUrlText {
    private final String text, hint;
    public VisibleUrlText(String text, String hint) { this.text=text; this.hint=hint; }
    public static boolean isVisuallyEquivalent(VisibleUrlText a, VisibleUrlText b) {
        return a.text.equals(b.text) && a.hint.equals(b.hint);
    }
}
''',
}


@unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
class HomepageNativeTests(unittest.TestCase):
    def test_activation_recovers_invalidations_dropped_before_the_first_frame(self):
        from test_homepage import homepage

        fixture = (FIXTURES / 'toolbar_activation_capture.java.in').read_text()
        ready = homepage.method_source(fixture, '        protected boolean isReadyForCapture() {')
        descendant = homepage.method_source(fixture,
            '    public void onDescendantInvalidated(View child, View target) {')
        resource = '\n\n'.join(homepage.method_source(fixture, signature) for signature in (
            '    protected boolean isDirty() {', '    public void onResourceRequested() {',
            '    public void invalidate(@Nullable Rect dirtyRect) {'))
        variants = {
            'original': (homepage.TOOLBAR_REFRESH_BEFORE, homepage.TOOLBAR_ACTIVATION_BEFORE, homepage.TOOLBAR_NATIVE_READY_BEFORE),
            'redraw_only': (homepage.TOOLBAR_REFRESH_AFTER, homepage.TOOLBAR_ACTIVATION_BEFORE, homepage.TOOLBAR_NATIVE_READY_BEFORE),
            'activation_only': (homepage.TOOLBAR_REFRESH_AFTER, homepage.TOOLBAR_ACTIVATION_AFTER, homepage.TOOLBAR_NATIVE_READY_BEFORE),
            'fixed': (homepage.TOOLBAR_REFRESH_AFTER, homepage.TOOLBAR_ACTIVATION_AFTER, homepage.TOOLBAR_NATIVE_READY_AFTER),
        }
        for variant, (refresh, activation, native_ready) in variants.items():
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sources = dict(STUBS)
                sources['androidx/core/view/OneShotPreDrawListener.java'] = '''
package androidx.core.view;
public class OneShotPreDrawListener {
    public static OneShotPreDrawListener add(android.view.View view, Runnable action) {
        view.preDraw.add(action); return new OneShotPreDrawListener();
    }
}
'''
                sources['org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java'] = (
                    FIXTURES / 'NewTabPageDelegate.java.in').read_text()
                sources['org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'] = BRIDGE.read_text()
                manager = (FIXTURES / 'ToolbarActivationManager.java.in').read_text()
                for marker, source in (('ARGON_READY_METHOD', ready),
                        ('ARGON_INVALIDATION_METHOD', descendant), ('ARGON_RESOURCE_METHODS', resource),
                        ('ARGON_NATIVE_READY_BLOCK', native_ready),
                        ('ARGON_ACTIVATION_METHOD', activation), ('ARGON_REFRESH_METHOD', refresh)):
                    manager = manager.replace(marker, source)
                sources['org/chromium/chrome/browser/toolbar/ToolbarManager.java'] = manager
                sources['ToolbarActivationFlow.java'] = (FIXTURES / 'ToolbarActivationFlow.java.in').read_text()
                for path, source in sources.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(source)
                compiled = subprocess.run(['javac', '--release', '17', '-d', str(root),
                    *map(str, root.rglob('*.java'))], capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run(['java', '-ea', '-cp', str(root), 'ToolbarActivationFlow'],
                                        capture_output=True, text=True)
                if variant == 'fixed':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('PASS: first layout', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    expected = {'original': 'First layout must refresh',
                                'redraw_only': 'Selecting a measured background homepage must refresh',
                                'activation_only': 'Native initialization must allow a fresh activation request'}
                    self.assertIn(expected[variant], result.stderr)

    def test_compositor_background_and_visibility_only_snapshot_changes(self):
        from test_homepage import homepage

        fixture = homepage.PHONE_CAPTURE_FIELD_BEFORE + '\n' + (
            FIXTURES / 'toolbar_phone_capture.java.in').read_text()
        variants = {
            'original': fixture,
            'background_only': homepage.replace_once(homepage.reviewed_method(
                fixture, homepage.PHONE_RECT_SIGNATURE, homepage.PHONE_RECT_DIGEST,
                homepage.PHONE_RECT_BEFORE, homepage.PHONE_RECT_AFTER),
                homepage.PHONE_VISIBILITY_BEFORE, homepage.PHONE_VISIBILITY_AFTER),
            'fixed': homepage.patch_phone_capture(fixture),
            'before_hidden_icon_fix': homepage.patch_phone_capture(fixture).replace(
                homepage.PHONE_STATUS_AFTER, homepage.PHONE_STATUS_BEFORE, 1),
        }
        for variant, capture in variants.items():
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sources = {**STUBS, **CAPTURE_STUBS}
                sources['org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java'] = (
                    FIXTURES / 'NewTabPageDelegate.java.in').read_text()
                sources['org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'] = BRIDGE.read_text()
                sources['org/chromium/chrome/browser/toolbar/top/PhoneCaptureStateToken.java'] = (
                    FIXTURES / 'PhoneCaptureStateToken.java.in').read_text()
                start = capture.index(homepage.PHONE_RECT_SIGNATURE)
                field, methods = capture[:start], capture[start:]
                sources['org/chromium/chrome/browser/toolbar/top/ToolbarPhoneCaptureFlow.java'] = (
                    FIXTURES / 'ToolbarPhoneCaptureFlow.java.in').read_text().replace(
                        'ARGON_PHONE_CAPTURE_FIELD', field).replace('ARGON_PHONE_CAPTURE_METHODS', methods)
                for path, text in sources.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                compiled = subprocess.run(
                    ['javac', '--release', '17', '-d', str(root), *map(str, root.rglob('*.java'))],
                    capture_output=True, text=True)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                result = subprocess.run(
                    ['java', '-ea', '-cp', str(root),
                     'org.chromium.chrome.browser.toolbar.top.ToolbarPhoneCaptureFlow'],
                    capture_output=True, text=True)
                if variant == 'fixed':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('PASS: compositor background bounds', result.stdout)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    expected = {
                        'original': 'Compositor background must be absent',
                        'background_only': 'Visibility-only change must refresh',
                        'before_hidden_icon_fix': 'Hidden status icon animation must not block',
                    }
                    self.assertIn(expected[variant], result.stderr)

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
