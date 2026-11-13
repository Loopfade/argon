#!/usr/bin/env python3
"""Bundle the Argon homepage and route regular Android NTPs to chrome://argon/.

This runs after the pinned Vanadium patches. Changed upstream methods must be
reviewed before this patch will apply; every input is checked before writing.
"""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'chromium_overlay'
UI = Path('chrome/browser/ui/webui/argon_home')
RESOURCES = Path('chrome/browser/resources/argon_home')
CONFIGS = Path('chrome/browser/ui/webui/chrome_web_ui_configs.cc')
GN = Path('chrome/browser/ui/webui/BUILD.gn')
NATIVE_FACTORY = Path('chrome/android/java/src/org/chromium/chrome/browser/native_page/NativePageFactory.java')
NATIVE_HANDLER = Path('chrome/browser/android/ntp/new_tab_page_url_handler.cc')
URL_RESOLVER_FACTORY = Path('chrome/browser/url_constants/android/java/src/org/chromium/chrome/browser/url_constants/UrlConstantResolverFactory.java')
TABBED_ACTIVITY = Path('chrome/android/java/src/org/chromium/chrome/browser/ChromeTabbedActivity.java')
TOOLBAR_MANAGER = Path('chrome/android/java/src/org/chromium/chrome/browser/toolbar/ToolbarManager.java')
TOOLBAR_CONTAINER = Path('chrome/browser/ui/android/toolbar/java/src/org/chromium/chrome/browser/toolbar/top/ToolbarControlContainer.java')
TOOLBAR_PHONE = Path('chrome/browser/ui/android/toolbar/java/src/org/chromium/chrome/browser/toolbar/top/ToolbarPhone.java')
NEW_TAB_BUTTON = Path('chrome/browser/ui/android/toolbar/java/src/org/chromium/chrome/browser/toolbar/adaptive/OptionalNewTabButtonController.java')
JAVA_CATALOG = Path('chrome/android/chrome_java_sources.gni')
JNI_CATALOG = Path('chrome/android/BUILD.gn')
JAVA_BRIDGE = Path('chrome/android/java/src/org/chromium/chrome/browser/ntp/ArgonHomeBridge.java')
JAVA_STARTUP = JAVA_BRIDGE.with_name('ArgonHomeStartup.java')
JAVA_SHORTCUT = JAVA_BRIDGE.with_name('ArgonHomeShortcut.java')
APP_MENU = Path('chrome/android/java/src/org/chromium/chrome/browser/tabbed_mode/TabbedAppMenuPropertiesDelegate.java')
ANDROID_RESOURCES = Path('chrome/android/chrome_java_resources.gni')
SHORTCUT_RESOURCES = tuple(Path('chrome/android/java/res') / locale / 'argon_home_strings.xml'
                           for locale in ('values', 'values-ru'))
INPUT_PATHS = (CONFIGS, GN, NATIVE_FACTORY, NATIVE_HANDLER, URL_RESOLVER_FACTORY, TABBED_ACTIVITY,
               TOOLBAR_MANAGER, TOOLBAR_CONTAINER, TOOLBAR_PHONE, NEW_TAB_BUTTON, JAVA_CATALOG, JNI_CATALOG,
               APP_MENU, ANDROID_RESOURCES)

SHORTCUT_ACTION_BEFORE = '''        final Tab currentTab = getActivityTab();
        boolean currentTabIsNtp = isTabNtp(currentTab);'''
SHORTCUT_ACTION_AFTER = SHORTCUT_ACTION_BEFORE + '''
        if (id == R.id.argon_add_to_home_id) {
            String draftUrl = org.chromium.chrome.browser.ntp.ArgonHomeShortcut.draftUrl(currentTab);
            if (draftUrl != null) {
                getTabCreator(/* incognito= */ false)
                        .createNewTab(
                                new LoadUrlParams(draftUrl, PageTransition.AUTO_BOOKMARK),
                                TabLaunchType.FROM_CHROME_UI,
                                currentTab);
            }
            return true;
        }'''
SHORTCUT_MENU_ANCHORS = (
    '        modelList.add(mBookmarksItemBuilder.buildBookmarksItem(shouldShowIconBeforeItem));',
    '        modelList.add(mBookmarksItemBuilder.buildBookmarksParentItem(currentTab));',
)
SHORTCUT_MENU_ADDITION = '''
        // ARGON: the homepage observes native bookmarks; the existing star adds sites.
'''

FACTORY_SIGNATURE = '    private static boolean isChromePageUrlOverridden(GURL url, boolean isIncognito) {'
HANDLER_SIGNATURE = 'bool HandleAndroidNativePageURL(GURL* url,'
RESOLVER_SIGNATURE = '    private static UrlConstantResolver buildOriginalResolver() {'
STARTUP_SIGNATURE = '            if (!mPendingInitialTabCreation) {'
CAPTURE_SIGNATURE = '        public boolean isDirty() {'
FACTORY_DIGEST = '905c86c4d9066d43abfa512b02ae1649961991706ec493fe026a16f386b7d315'
HANDLER_DIGEST = 'a09ff3d96542b4a7087fb747068004690df572bec9d9de4997dcde4140934c37'
RESOLVER_DIGEST = 'c8676f48d70c326f26086693906f9fd97473ced55a21ff3c80053613f06748d3'
STARTUP_DIGEST = 'dd8b71b758badb1913eddd6142d58425bc3a0178d03c41143acd5a3c84e6bc37'
CAPTURE_DIGEST = 'edcc5010cebdfdd9165831bb13dc8d3c12b27c21ea405e03ed548972ea8b8656'

CAPTURE_BEFORE = '''                    boolean isNativePage = tab == null || tab.isNativePage();
                    if (!isNativePage && mConstraintsObserver.areControlsLocked()) {'''
CAPTURE_AFTER = '''                    // ARGON: WebUI NTPs also change their fakebox/omnibox state after
                    // the page-load capture. chrome:// keeps controls locked, so waiting
                    // for an unlock would leave the old omnibox bitmap visible indefinitely.
                    boolean isNativePageOrNtp =
                            tab == null
                                    || tab.isNativePage()
                                    || (mToolbarDataProvider != null
                                            && mToolbarDataProvider
                                                    .getNewTabPageDelegate()
                                                    .isLocationBarShown());
                    if (!isNativePageOrNtp && mConstraintsObserver.areControlsLocked()) {'''

PHONE_RECT_SIGNATURE = '    public void getLocationBarContentRect(Rect outRect) {'
PHONE_READY_SIGNATURE = '    private CaptureReadinessResult getReadinessStateWithSuppression() {'
PHONE_TEXTURE_SIGNATURE = '    public void setTextureCaptureMode(boolean textureMode) {'
PHONE_RECT_DIGEST = '54471bfcbc436e9471359e579be7f4ab05ffd65eb9cff305bc5161575addb43e'
PHONE_READY_DIGEST = 'cbe57c21626c67efb223444dc6c65c93731f11ab8bf67a07f2676c8a404290e1'
PHONE_TEXTURE_DIGEST = 'ee79c888cfb44a6a9d2028684608477d0d0014ba9003c15a8f0030f0a358f0ca'
PHONE_RECT_BEFORE = '''        updateLocationBarBackgroundBounds(outRect, VisualState.NORMAL);'''
PHONE_RECT_AFTER = '''        // ARGON: the compositor draws this background separately from the bitmap.
        // A transparent Android omnibox must not leave a second, untouchable pill.
        if (!isLocationBarVisibleForCapture()) {
            outRect.setEmpty();
            return;
        }
''' + PHONE_RECT_BEFORE
PHONE_READY_BEFORE = '''            PhoneCaptureStateToken newSnapshotState = generateToolbarSnapshotState();'''
PHONE_READY_AFTER = '''            // ARGON: visibility can change while all snapshot-token properties stay equal.
            // Capture the new bitmap and background bounds instead of reusing the old omnibox.
            if (mLocationBarVisibleInLastCapture != isLocationBarVisibleForCapture()) {
                return CaptureReadinessResult.readyWithSnapshotDifference(
                        ToolbarSnapshotDifference.VISUAL_STATE);
            }
''' + PHONE_READY_BEFORE
PHONE_TEXTURE_BEFORE = '''            mPhoneCaptureStateToken = generateToolbarSnapshotState();'''
PHONE_TEXTURE_AFTER = PHONE_TEXTURE_BEFORE + '''
            mLocationBarVisibleInLastCapture = isLocationBarVisibleForCapture();'''
PHONE_CAPTURE_FIELD_BEFORE = '    private @Nullable PhoneCaptureStateToken mPhoneCaptureStateToken;'
PHONE_CAPTURE_FIELD_AFTER = PHONE_CAPTURE_FIELD_BEFORE + '''
    private boolean mLocationBarVisibleInLastCapture;'''
PHONE_VISIBILITY_BEFORE = '''    private boolean isLocationBarCurrentlyShown() {
        return !isLocationBarShownInNtp() || mUrlExpansionFraction > 0;
    }'''
PHONE_VISIBILITY_AFTER = PHONE_VISIBILITY_BEFORE + '''

    private boolean isLocationBarVisibleForCapture() {
        return isNativeLibraryReady()
                && isLocationBarCurrentlyShown()
                && mLocationBar.getPhoneCoordinator().getAlpha() > 0f;
    }'''

PHONE_SHORTCUT_SIGNATURE = '    private boolean shouldShowOptionalButtonInToolbar(@Nullable ButtonData buttonData) {'
PHONE_SHORTCUT_DIGEST = '8969502e39cf67a0277938d3a568225c92c22e39046bc4dceae29b6184310dbc'
PHONE_SHORTCUT_BEFORE = PHONE_SHORTCUT_SIGNATURE + '''
        boolean isNtp = isNtpVisualState(mVisualState);'''
PHONE_SHORTCUT_AFTER = PHONE_SHORTCUT_BEFORE + '''
        // ARGON: keep the selected new-tab action outside the hidden NTP omnibox.
        if (isNtp
                && !isIncognito()
                && buttonData != null
                && buttonData.canShow()
                && buttonData.getButtonSpec().getButtonVariant()
                        == AdaptiveToolbarButtonVariant.NEW_TAB) {
            return true;
        }'''
PHONE_SHORTCUT_IMPORT_BEFORE = 'import org.chromium.chrome.browser.toolbar.optional_button.ButtonData;'
PHONE_SHORTCUT_IMPORT_AFTER = (
    'import org.chromium.chrome.browser.toolbar.adaptive.AdaptiveToolbarButtonVariant;\n'
    + PHONE_SHORTCUT_IMPORT_BEFORE)
NEW_TAB_BUTTON_SIGNATURE = '    protected boolean shouldShowButton(@Nullable Tab tab) {'
NEW_TAB_BUTTON_DIGEST = '1f3de33ead218e1b0fa70d25cfc2aac7acc7622de14d483eb3b8d8c5d47f64cc'
NEW_TAB_BUTTON_BEFORE = '        if (UrlUtilities.isNtpUrl(tab.getUrl())) return false;'
NEW_TAB_BUTTON_AFTER = '''        // ARGON: a regular phone NTP still needs its configured new-tab shortcut.
        if (UrlUtilities.isNtpUrl(tab.getUrl()) && (mIsTablet || tab.isIncognito())) return false;'''

STARTUP_BEFORE = '''                boolean isTabWebUiNtp = UrlOverrideUtils.isWebUiNtpOverrideEnabled();
                assumeNonNull(currentTab);
                if (isTabNtp && !currentTab.isNativePage() && !isTabWebUiNtp) {'''
STARTUP_AFTER = '''                boolean isTabWebUiNtp = UrlOverrideUtils.isWebUiNtpOverrideEnabled();
                assumeNonNull(currentTab);
                // ARGON: regular chrome://newtab and chrome-native://newtab use WebUI.
                // Restored tabs may already have WebContents, so waiting for a later
                // NativePage/onContentChanged event can keep the launch window hidden forever.
                boolean isArgonHomeNtp =
                        isTabNtp && UrlConstants.NTP_HOST.equals(currentTab.getUrl().getHost());
                if (isTabNtp
                        && !currentTab.isNativePage()
                        && !isTabWebUiNtp
                        && !isArgonHomeNtp) {'''

STARTUP_FRAME_BEFORE = '''                } else {
                    mAppLaunchDrawBlocker.onActiveTabAvailable();
                }'''
STARTUP_FRAME_AFTER = '''                } else {
                    org.chromium.chrome.browser.ntp.ArgonHomeStartup.waitForFirstFrame(
                            currentTab, mAppLaunchDrawBlocker::onActiveTabAvailable);
                }'''
INITIAL_TAB_SIGNATURE = '    private void createInitialTab() {'
INITIAL_TAB_DIGEST = 'af29387383df44396d7b8f95fc6148f0cf2fa9afe4e95cc7fe4f5c1bcbfe8588'
INITIAL_TAB_BEFORE = '''        mAppLaunchDrawBlocker.onActiveTabAvailable();
        mAppLaunchDrawBlocker.onActiveTabAvailableForRecreation();'''
INITIAL_TAB_AFTER = '''        org.chromium.chrome.browser.ntp.ArgonHomeStartup.waitForFirstFrame(
                getActivityTab(), mAppLaunchDrawBlocker::onActiveTabAvailable);
        mAppLaunchDrawBlocker.onActiveTabAvailableForRecreation();'''

RESOLVER_BEFORE = '''        UrlConstantResolver resolver = new UrlConstantResolver();
        resolver.registerOverride(
                getOriginalNativeNtpUrl(),
                () -> {
                    // Returns 'chrome://newtab' even when WebUI NTP is enabled in order to respect
                    // extensions/NTP location overrides and delegate to C++ URL rewrite handler.
                    if (isNtpOverrideEnabled() || isWebUiNtpOverrideEnabled()) {
                        return getOriginalNtpUrl();
                    } else {
                        return null;
                    }
                });'''
RESOLVER_AFTER = '''        // ARGON: regular new tabs must enter the chrome:// URL rewrite chain.
        // A native-page factory veto alone leaves callers on chrome-native://,
        // whose empty renderer document cannot display the bundled WebUI.
        // Keep policy and extension overrides in C++, ahead of our fallback.
        // This must also work when ChromeNativeUrlOverriding is disabled.
        UrlConstantResolver resolver = new UrlConstantResolver() {
            @Override
            public String getNtpUrl() {
                return getOriginalNtpUrl();
            }
        };'''

FACTORY_BEFORE = FACTORY_SIGNATURE + '\n'
FACTORY_AFTER = FACTORY_BEFORE + '''        // ARGON: render the bundled homepage for regular NTPs in either scheme.
        // Incognito and overrides of bookmarks/history retain their native flow.
        if (!isIncognito
                && UrlConstants.NTP_HOST.equals(url.getHost())
                && (UrlConstants.CHROME_SCHEME.equals(url.getScheme())
                        || "chrome-native".equals(url.getScheme()))) {
            return true;
        }

'''
HANDLER_BEFORE = '''  if (url->SchemeIs(content::kChromeUIScheme)) {'''
HANDLER_AFTER = '''  // ARGON: the native-page factory lets regular NTPs reach this renderer route.
  // Extension URL overrides above keep priority; external/custom homepages do
  // not match either internal scheme. Incognito retains Chromium's NTP.
  if (!Profile::FromBrowserContext(browser_context)->IsOffTheRecord() &&
      (url->SchemeIs(content::kChromeUIScheme) ||
       url->SchemeIs(chrome::kChromeNativeScheme)) &&
      url->GetHost() == chrome::kChromeUINewTabHost) {
    *url = GURL("chrome://argon/");
    return true;
  }

''' + HANDLER_BEFORE


def replace_once(text, before, after):
    if text.count(after) == 1:
        if before in text.replace(after, '', 1):
            raise ValueError('Ambiguous already-applied Argon homepage patch')
        return text
    if text.count(after) or text.count(before) != 1:
        raise ValueError('Argon homepage patch anchor changed; review Chromium update')
    return text.replace(before, after, 1)


def method_source(text, signature):
    if text.count(signature) != 1:
        raise ValueError(f'Expected one Chromium method: {signature.strip()}')
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth and end < len(text):
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    if depth:
        raise ValueError('Incomplete Chromium method in homepage patch')
    return text[start:end]


def reviewed_method(text, signature, digest, before, after):
    method = method_source(text, signature)
    if method.count(after) == 1:
        method = method.replace(after, before, 1)
    if hashlib.sha256(method.encode()).hexdigest() != digest:
        raise ValueError(f'Chromium method changed; review homepage patch: {signature.strip()}')
    return replace_once(text, before, after)


def patch_factory(text):
    text = reviewed_method(text, FACTORY_SIGNATURE, FACTORY_DIGEST, FACTORY_BEFORE, FACTORY_AFTER)
    return replace_once(text,
        '     * <p>chrome-native:// scheme pages are not affected by this.',
        '     * <p>Argon also renders regular chrome-native://newtab pages in WebUI.')


def patch_handler(text):
    return reviewed_method(text, HANDLER_SIGNATURE, HANDLER_DIGEST, HANDLER_BEFORE, HANDLER_AFTER)


def patch_resolver_factory(text):
    return reviewed_method(text, RESOLVER_SIGNATURE, RESOLVER_DIGEST,
                           RESOLVER_BEFORE, RESOLVER_AFTER)


def patch_startup_draw(text):
    original = method_source(text, STARTUP_SIGNATURE)
    reviewed = original.replace(STARTUP_FRAME_AFTER, STARTUP_FRAME_BEFORE)
    reviewed = reviewed_method(reviewed, STARTUP_SIGNATURE, STARTUP_DIGEST,
                               STARTUP_BEFORE, STARTUP_AFTER)
    patched = replace_once(reviewed, STARTUP_FRAME_BEFORE, STARTUP_FRAME_AFTER)
    return replace_once(text, original, patched)


def patch_activity_startup(text):
    text = patch_startup_draw(text)
    return reviewed_method(text, INITIAL_TAB_SIGNATURE, INITIAL_TAB_DIGEST,
                           INITIAL_TAB_BEFORE, INITIAL_TAB_AFTER)


def patch_activity(text):
    return replace_once(patch_activity_startup(text), SHORTCUT_ACTION_BEFORE, SHORTCUT_ACTION_AFTER)


def patch_app_menu(text):
    for anchor in SHORTCUT_MENU_ANCHORS:
        text = replace_once(text, anchor, anchor + SHORTCUT_MENU_ADDITION)
    return text


def patch_android_resources(text):
    anchor = '  "java/res/values/ids.xml",'
    addition = ''.join('\n  "' + str(path.relative_to('chrome/android')) + '",'
                       for path in SHORTCUT_RESOURCES)
    return replace_once(text, anchor, anchor + addition)


def patch_toolbar_capture(text):
    return reviewed_method(text, CAPTURE_SIGNATURE, CAPTURE_DIGEST,
                           CAPTURE_BEFORE, CAPTURE_AFTER)


def patch_phone_capture(text):
    for signature, digest, before, after in (
        (PHONE_RECT_SIGNATURE, PHONE_RECT_DIGEST, PHONE_RECT_BEFORE, PHONE_RECT_AFTER),
        (PHONE_READY_SIGNATURE, PHONE_READY_DIGEST, PHONE_READY_BEFORE, PHONE_READY_AFTER),
        (PHONE_TEXTURE_SIGNATURE, PHONE_TEXTURE_DIGEST, PHONE_TEXTURE_BEFORE, PHONE_TEXTURE_AFTER),
    ):
        text = reviewed_method(text, signature, digest, before, after)
    text = replace_once(text, PHONE_CAPTURE_FIELD_BEFORE, PHONE_CAPTURE_FIELD_AFTER)
    return replace_once(text, PHONE_VISIBILITY_BEFORE, PHONE_VISIBILITY_AFTER)


def patch_phone_shortcut(text):
    text = reviewed_method(text, PHONE_SHORTCUT_SIGNATURE, PHONE_SHORTCUT_DIGEST,
                           PHONE_SHORTCUT_BEFORE, PHONE_SHORTCUT_AFTER)
    return replace_once(text, PHONE_SHORTCUT_IMPORT_BEFORE, PHONE_SHORTCUT_IMPORT_AFTER)


def patch_phone_toolbar(text):
    return patch_phone_shortcut(patch_phone_capture(text))


def patch_new_tab_button(text):
    return reviewed_method(text, NEW_TAB_BUTTON_SIGNATURE, NEW_TAB_BUTTON_DIGEST,
                           NEW_TAB_BUTTON_BEFORE, NEW_TAB_BUTTON_AFTER)


def patch_configs(text):
    text = replace_once(text,
        '#include "build/build_config.h"',
        '#include "build/build_config.h"\n\n'
        '#if BUILDFLAG(IS_ANDROID)\n'
        '#include "chrome/browser/ui/webui/argon_home/argon_home_ui.h"\n'
        '#endif')
    return replace_once(text,
        '#if BUILDFLAG(IS_ANDROID)\n  map.AddWebUIConfig(',
        '#if BUILDFLAG(IS_ANDROID)\n'
        '  map.AddWebUIConfig(std::make_unique<ArgonHomeUIConfig>());\n'
        '  map.AddWebUIConfig(')


def patch_gn(text):
    return replace_once(text,
        '      "//chrome/browser/ui/android/lens",',
        '      "//chrome/browser/ui/android/lens",\n'
        '      "//chrome/browser/ui/webui/argon_home",')


TOOLBAR_BEFORE = '''            return new ToolbarNtpDelegate() {
                @Override
                protected boolean shouldUpdateListener() {
                    assumeNonNull(mVisibleNtp);
                    return mVisibleNtp.isLocationBarShownInNtp();
                }
            };
        } else if (mToolbarLayout instanceof ToolbarTablet) {'''
TOOLBAR_AFTER = TOOLBAR_BEFORE.replace(
    'return new ToolbarNtpDelegate() {',
    'return org.chromium.chrome.browser.ntp.ArgonHomeBridge.wrap(\n'
    '                    mActivityTabProvider::get, mToolbarLayout, new ToolbarNtpDelegate() {'
).replace('            };\n', '            }, mNtpSearchBoxTransitionPercentageSupplier::set,\n'
          '                    () -> getNewTabPageForCurrentTab() != null);\n')


TOOLBAR_NAV_START = "                    public void onDidStartNavigationInPrimaryMainFrame(\n                            Tab tab, NavigationHandle navigationHandle) {"
TOOLBAR_NAV_FINISH = "                    public void onDidFinishNavigationEnd() {"
TOOLBAR_LOAD_STOP = '                    public void onLoadStopped(Tab tab, boolean toDifferentDocument) {'
TOOLBAR_LOAD_STOP_DIGEST = '5abbc3db6753c334611eb05e1ebad251b0c3e2c9f09471dde8c51d39ed29424c'
TOOLBAR_CRASH = '                    public void onCrash(Tab tab) {'
TOOLBAR_CRASH_DIGEST = 'd6197f275d513b218d32d9559da699ad30829b3f690b2261957987221649d593'

TOOLBAR_INIT_BEFORE = '''        mToolbarLayout = mActivity.findViewById(R.id.toolbar);
        NewTabPageDelegate ntpDelegate = createNewTabPageDelegate();'''
TOOLBAR_INIT_AFTER = '''        // ARGON: the phone delegate binds this provider during construction.
        mActivityTabProvider = tabProvider;
        mToolbarLayout = mActivity.findViewById(R.id.toolbar);
        NewTabPageDelegate ntpDelegate = createNewTabPageDelegate();'''
TOOLBAR_PROVIDER_BEFORE = '''        mActivityTabProvider = tabProvider;
        mActivityResultTracker = activityResultTracker;'''
TOOLBAR_PROVIDER_AFTER = '''        // ARGON: the tab provider is already initialized above.
        mActivityResultTracker = activityResultTracker;'''

TOOLBAR_ACTIVATION_SIGNATURE = '    private void onTabOrModelChanged() {'
TOOLBAR_ACTIVATION_DIGEST = '3bdd8a46ea125d798ab64c177d9ff3efccaf33cef3082b4586332f2e3427fdab'
TOOLBAR_ACTIVATION_BEFORE = '''    private void onTabOrModelChanged() {
        mToolbar.onTabOrModelChanged();
        checkIfNtpLoaded();
    }'''
TOOLBAR_ACTIVATION_REGISTERED_BEFORE = TOOLBAR_ACTIVATION_BEFORE.replace(
    '        checkIfNtpLoaded();', '''        checkIfNtpLoaded();
        // ARGON: a background WebUI may have reported its bounds before selection.
        Tab argonTab = mActivityTabProvider.get();
        if (org.chromium.chrome.browser.ntp.ArgonHomeBridge.isRegistered(argonTab)) {
            onArgonHomeSearchBoxChanged();
        }''')
TOOLBAR_ACTIVATION_AFTER = TOOLBAR_ACTIVATION_REGISTERED_BEFORE.replace(
    '.isRegistered(argonTab)', '.isHomeToolbarActive(argonTab)')
TOOLBAR_REFRESH_BEFORE = '''    /** Refresh after the WebUI search box completes its first layout or is detached. */
    public void onArgonHomeSearchBoxChanged() {
        if (mIsDestroyed) return;
        mLocationBarModel.notifyContentChanged();
        mToolbar.onTabContentViewChanged();
    }

'''
TOOLBAR_REFRESH_LAYOUT_BEFORE = '''    /** Refresh the live WebUI toolbar now and its compositor resource after layout. */
    public void onArgonHomeSearchBoxChanged() {
        if (mIsDestroyed) return;
        mLocationBarModel.notifyContentChanged();
        mToolbar.onTabContentViewChanged();
        Tab argonTab = mActivityTabProvider.get();
        // Child invalidations are ignored while controls are not ready or changing visibility.
        // The next pre-draw runs after layout, when the normal capture checks can accept a frame.
        androidx.core.view.OneShotPreDrawListener.add(mToolbarLayout, () -> {
            if (mIsDestroyed || !mInitializedWithNative || argonTab != mActivityTabProvider.get()) {
                return;
            }
            var resource = mControlContainer.getToolbarResourceAdapter();
            resource.invalidate(null);
            resource.onResourceRequested();
        });
        mToolbarLayout.getRootView().postInvalidateOnAnimation();
    }

'''
TOOLBAR_REFRESH_AFTER = TOOLBAR_REFRESH_LAYOUT_BEFORE.replace(
    '''        // Child invalidations are ignored while controls are not ready or changing visibility.
        // The next pre-draw runs after layout, when the normal capture checks can accept a frame.''',
    '''        // ARGON: the pre-draw may still have a child layout request pending. A normal
        // resource request can be suppressed and leave the previous visible omnibox cached.
        // Complete the toolbar layout and publish its bitmap through Chromium's forced path.'''
).replace(
    '''            var resource = mControlContainer.getToolbarResourceAdapter();
            resource.invalidate(null);
            resource.onResourceRequested();''',
    '''            mControlContainer.doSynchronousLayout(/* forceCaptureAfterLayout= */ true);''')
TOOLBAR_NATIVE_READY_BEFORE = '''        // Allow bitmap capturing once everything has been initialized.
        Tab currentTab = tabModelSelector.getCurrentTab();
        if (currentTab != null
                && currentTab.getWebContents() != null
                && !currentTab.getUrl().isEmpty()) {
            mControlContainer.setReadyForBitmapCapture(true);
        }'''
TOOLBAR_NATIVE_READY_REGISTERED_BEFORE = TOOLBAR_NATIVE_READY_BEFORE.replace(
    '            mControlContainer.setReadyForBitmapCapture(true);',
    '''            mControlContainer.setReadyForBitmapCapture(true);
            if (org.chromium.chrome.browser.ntp.ArgonHomeBridge.isRegistered(currentTab)) {
                onArgonHomeSearchBoxChanged();
            }''')
TOOLBAR_NATIVE_READY_AFTER = TOOLBAR_NATIVE_READY_REGISTERED_BEFORE.replace(
    '.isRegistered(currentTab)', '.isHomeToolbarActive(currentTab)')


def patch_toolbar(text):
    for signature, digest, callback in (
        (TOOLBAR_LOAD_STOP, TOOLBAR_LOAD_STOP_DIGEST, 'onPageLoadStopped'),
        (TOOLBAR_CRASH, TOOLBAR_CRASH_DIGEST, 'onPageCrashed'),
    ):
        before = signature + '\n'
        after = before + f'                        org.chromium.chrome.browser.ntp.ArgonHomeBridge.{callback}(tab);\n'
        text = reviewed_method(text, signature, digest, before, after)
    if TOOLBAR_ACTIVATION_REGISTERED_BEFORE in text:
        text = replace_once(text, TOOLBAR_ACTIVATION_REGISTERED_BEFORE, TOOLBAR_ACTIVATION_AFTER)
    if TOOLBAR_NATIVE_READY_REGISTERED_BEFORE in text:
        text = replace_once(text, TOOLBAR_NATIVE_READY_REGISTERED_BEFORE, TOOLBAR_NATIVE_READY_AFTER)
    text = replace_once(text, TOOLBAR_INIT_BEFORE, TOOLBAR_INIT_AFTER)
    text = replace_once(text, TOOLBAR_PROVIDER_BEFORE, TOOLBAR_PROVIDER_AFTER)
    text = replace_once(text, TOOLBAR_BEFORE, TOOLBAR_AFTER)
    text = reviewed_method(text, TOOLBAR_ACTIVATION_SIGNATURE, TOOLBAR_ACTIVATION_DIGEST,
                           TOOLBAR_ACTIVATION_BEFORE, TOOLBAR_ACTIVATION_AFTER)
    text = replace_once(text, TOOLBAR_NATIVE_READY_BEFORE, TOOLBAR_NATIVE_READY_AFTER)
    text = replace_once(text, TOOLBAR_NAV_START, TOOLBAR_NAV_START + '''
                        org.chromium.chrome.browser.ntp.ArgonHomeBridge.onNavigationStarted(
                                tab, navigationHandle.getUrl().getSpec());''')
    text = replace_once(text, TOOLBAR_NAV_FINISH, TOOLBAR_NAV_FINISH + '''
                        Tab argonTab = mActivityTabProvider.get();
                        if (argonTab != null && org.chromium.chrome.browser.ntp.ArgonHomeBridge.isRegistered(argonTab)) {
                            org.chromium.chrome.browser.ntp.ArgonHomeBridge.onNavigationFinished(
                                    argonTab, hasPendingNonNtpNavigation(argonTab));
                        }''')
    anchor = ('    /**\n     * @return The {@link OmniboxStub}.\n     */\n'
              '    public @Nullable OmniboxStub getOmniboxStub() {')
    if TOOLBAR_REFRESH_BEFORE in text:
        text = replace_once(text, TOOLBAR_REFRESH_BEFORE, TOOLBAR_REFRESH_AFTER)
    if TOOLBAR_REFRESH_LAYOUT_BEFORE in text:
        text = replace_once(text, TOOLBAR_REFRESH_LAYOUT_BEFORE, TOOLBAR_REFRESH_AFTER)
    return replace_once(text, anchor, TOOLBAR_REFRESH_AFTER + anchor)


def patch_java_catalog(text):
    anchor = '  "java/src/org/chromium/chrome/browser/ntp/NewTabPage.java",'
    if text.count(anchor) != 1:
        raise ValueError('Argon homepage Java catalog anchor changed')
    for path in (JAVA_BRIDGE, JAVA_STARTUP, JAVA_SHORTCUT):
        entry = '  "' + str(path.relative_to('chrome/android')) + '",'
        if text.count(entry) > 1:
            raise ValueError('Duplicate Argon home Java registration')
        if entry not in text:
            text = replace_once(text, anchor, entry + '\n' + anchor)
    return text


def patch_jni_catalog(text):
    anchor = '  generate_jni("chrome_jni_headers") {\n    sources = ['
    entry = '\n      "//' + JAVA_BRIDGE.as_posix() + '",'
    if text.count(entry) > 1:
        raise ValueError('Duplicate Argon home JNI registration')
    return text if entry in text else replace_once(text, anchor, anchor + entry)


def resource_header(resources):
    entries = [('index.html', 'kArgonHomeHtml', 'ARGON_HTML'),
               ('app.css', 'kArgonHomeCss', 'ARGON_CSS'),
               ('app.js', 'kArgonHomeJs', 'ARGON_JS'),
               ('theme.js', 'kArgonHomeTheme', 'ARGON_THEME'),
               ('startup.js', 'kArgonHomeStartup', 'ARGON_STARTUP'),
               ('state.mjs', 'kArgonHomeState', 'ARGON_STATE'),
               ('icons.mjs', 'kArgonHomeIcons', 'ARGON_ICONS'),
               ('bookmarks.mjs', 'kArgonHomeBookmarks', 'ARGON_BOOKMARKS'),
               ('browser-bookmarks.mjs', 'kArgonBrowserBookmarks', 'ARGON_BM'),
               ('browser-search.mjs', 'kArgonHomeSearch', 'ARGON_SEARCH'),
               ('site-icons.mjs', 'kArgonHomeSiteIcons', 'ARGON_SITE_ICONS'),
               ('backgrounds.mjs', 'kArgonHomeBackgrounds', 'ARGON_BG'),
               ('i18n.js', 'kArgonHomeI18n', 'ARGON_I18N')]
    result = ['// Generated by scripts/apply_homepage.py; do not edit.\n']
    for filename, name, delimiter in entries:
        value = (resources / filename).read_text()
        if len(delimiter) > 16 or '\x00' in value or f'){delimiter}"' in value:
            raise ValueError(f'Invalid bundled homepage resource: {filename}')
        result.append(f'constexpr char {name}[] = R"{delimiter}({value}){delimiter}";\n')
    return '\n'.join(result)


def apply(src):
    transforms = [(CONFIGS, patch_configs), (GN, patch_gn),
                  (NATIVE_FACTORY, patch_factory), (NATIVE_HANDLER, patch_handler),
                  (URL_RESOLVER_FACTORY, patch_resolver_factory),
                  (TABBED_ACTIVITY, patch_activity), (APP_MENU, patch_app_menu),
                  (ANDROID_RESOURCES, patch_android_resources),
                  (TOOLBAR_MANAGER, patch_toolbar), (JAVA_CATALOG, patch_java_catalog),
                  (TOOLBAR_CONTAINER, patch_toolbar_capture),
                  (TOOLBAR_PHONE, patch_phone_toolbar),
                  (NEW_TAB_BUTTON, patch_new_tab_button),
                  (JNI_CATALOG, patch_jni_catalog)]
    writes = {src / path: transform((src / path).read_text()) for path, transform in transforms}
    for source in (OVERLAY / UI).iterdir():
        if source.is_file():
            target = src / UI / source.name
            value = source.read_text()
            if target.exists() and target.read_text() != value:
                raise ValueError(f'Refusing to overwrite an unexpected homepage source: {target}')
            writes[target] = value
    writes[src / UI / 'argon_home_resources.inc'] = resource_header(OVERLAY / RESOURCES)
    for path in (JAVA_BRIDGE, JAVA_STARTUP, JAVA_SHORTCUT, *SHORTCUT_RESOURCES):
        value = (OVERLAY / path).read_text()
        if (src / path).exists() and (src / path).read_text() != value:
            raise ValueError(f'Unexpected Argon home source: {path}')
        writes[src / path] = value
    for target, value in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != value:
            target.write_text(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', type=Path, required=True)
    args = parser.parse_args()
    apply(args.chromium_src.resolve())


if __name__ == '__main__':
    main()
