// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.ntp;

import android.app.Activity;
import android.graphics.Point;
import android.graphics.Rect;
import android.view.MotionEvent;
import android.view.View;

import org.jni_zero.CalledByNative;
import org.jni_zero.NativeMethods;

import org.chromium.base.Callback;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.browser.ChromeTabbedActivity;
import org.chromium.chrome.browser.omnibox.NewTabPageDelegate;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.url_constants.UrlOverrideUtils;
import org.chromium.components.omnibox.AutocompleteInput;
import org.chromium.components.omnibox.OmniboxFocusReason;
import org.chromium.content_public.browser.RenderCoordinates;
import org.chromium.content_public.browser.WebContents;
import org.chromium.url.GURL;
import org.chromium.ui.base.DeviceFormFactor;

import java.lang.ref.WeakReference;
import java.util.WeakHashMap;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

/** Adapts the bundled WebUI search box to Chromium's existing NTP/omnibox transitions. */
@NullMarked
public final class ArgonHomeBridge {
    private static final WeakHashMap<WebContents, WeakReference<ArgonHomeBridge>> PAGES =
            new WeakHashMap<>();
    private static final WeakHashMap<Tab, Boolean> EARLY_NAVIGATIONS = new WeakHashMap<>();
    private final WebContents mContents;
    private final Rect mLastBounds = new Rect();
    private long mNativeObserver;
    private @Nullable WeakReference<Tab> mHomeNavigation;
    private boolean mHasBounds;
    private boolean mNavigatingAway;
    private float mLeft;
    private float mTop;
    private float mWidth;
    private float mHeight;
    private float mScrollFraction;
    private float mUrlFocusFraction;
    private float mAlpha = Float.NaN;
    private boolean mFocused;
    private @Nullable Callback<Float> mScrollCallback;

    private ArgonHomeBridge(WebContents contents, long observer) {
        mContents = contents;
        mNativeObserver = observer;
    }

    private @Nullable ChromeTabbedActivity activity() {
        return activity(mContents);
    }

    private static @Nullable ChromeTabbedActivity activity(@Nullable WebContents contents) {
        if (contents == null || contents.isDestroyed()) return null;
        var window = contents.getTopLevelNativeWindow();
        Activity activity = window == null ? null : window.getActivity().get();
        return activity instanceof ChromeTabbedActivity ? (ChromeTabbedActivity) activity : null;
    }

    private boolean isActive(ChromeTabbedActivity activity) {
        Tab tab = activity.getActivityTab();
        return tab != null && tab.getWebContents() == mContents;
    }

    @CalledByNative
    private static @Nullable ArgonHomeBridge create(WebContents contents, long observer) {
        var page = new ArgonHomeBridge(contents, observer);
        PAGES.put(contents, new WeakReference<>(page));
        return page;
    }

    @CalledByNative
    private boolean isSupported() {
        return !mContents.isDestroyed() && activity() != null;
    }

    /** Use Chromium's own tablet form-factor detection, independent of viewport width. */
    @CalledByNative
    private boolean isTablet() {
        if (mContents.isDestroyed()) return false;
        var window = mContents.getTopLevelNativeWindow();
        return window != null && DeviceFormFactor.isWindowOnTablet(window);
    }

    @CalledByNative
    private void destroy() {
        var reference = PAGES.get(mContents);
        if (reference != null && reference.get() == this) {
            Tab tab = mHomeNavigation == null ? null : mHomeNavigation.get();
            if (tab != null && tab.getWebContents() == mContents && !tab.isDestroyed()
                    && !tab.isIncognito() && !tab.isShowingErrorPage()
                    && !EARLY_NAVIGATIONS.containsKey(tab) && isHomeUrl(tab.getUrl())) {
                // Home/reload replaces this handler before the new document can register.
                // Return to the same pending-home state used before the first JNI attachment.
                PAGES.remove(mContents);
            } else {
                // Other detachments must not reactivate a dead document from its old URL.
                reference.clear();
            }
        }
        mHomeNavigation = null;
        mNativeObserver = 0;
        mScrollCallback = null;
        refreshToolbar();
    }

    @CalledByNative
    private boolean focus(String text) {
        var activity = activity();
        if (mNativeObserver == 0 || activity == null || activity.isFinishing()
                || activity.isDestroyed() || !isActive(activity)) return false;
        var toolbar = activity.getToolbarManager();
        var omnibox = toolbar == null ? null : toolbar.getOmniboxStub();
        if (omnibox == null) return false;
        omnibox.beginInput(new AutocompleteInput(text.isEmpty() ? OmniboxFocusReason.FAKE_BOX_TAP
                : OmniboxFocusReason.FAKE_BOX_LONG_PRESS)
                .setUserText(text.isEmpty() ? null : text)
                .setAutocompleteState(AutocompleteInput.AutocompleteState.ENABLED));
        return true;
    }

    @CalledByNative
    private void updateBounds(float left, float top, float width, float height, float scroll) {
        boolean firstLayout = !mHasBounds;
        float scrollFraction = Math.max(0f, Math.min(1f, scroll));
        boolean scrolled = mScrollFraction != scrollFraction;
        mLeft = left;
        mTop = top;
        mWidth = width;
        mHeight = height;
        mScrollFraction = scrollFraction;
        mHasBounds = width > 0f && height > 0f;
        if (firstLayout && mHasBounds) refreshToolbar();
        // IME resize changes the fakebox geometry while the real omnibox is focused.
        // ToolbarPhone treats a scroll callback as a transition back to that fakebox.
        else if (scrolled && mScrollCallback != null && !isOmniboxFocused()) {
            mScrollCallback.onResult(mScrollFraction);
        }
    }

    private void refreshToolbar() {
        var activity = activity();
        if (activity == null || !isActive(activity)) return;
        var toolbar = activity.getToolbarManager();
        if (toolbar != null) toolbar.onArgonHomeSearchBoxChanged();
    }

    private void bounds(View toolbar, Rect bounds, Point translation) {
        var activity = activity();
        Tab tab = activity == null ? null : activity.getActivityTab();
        View content = tab == null || tab.getWebContents() != mContents ? null : tab.getContentView();
        if (content != null && !mContents.isDestroyed()) {
            int[] contentPosition = new int[2];
            int[] toolbarPosition = new int[2];
            content.getLocationInWindow(contentPosition);
            toolbar.getLocationInWindow(toolbarPosition);
            var coordinates = RenderCoordinates.fromWebContents(mContents);
            int x = contentPosition[0] - toolbarPosition[0];
            int y = contentPosition[1] - toolbarPosition[1] + coordinates.getContentOffsetYPixInt();
            mLastBounds.set(
                    x + Math.round(coordinates.fromLocalCssToPix(mLeft)),
                    y + Math.round(coordinates.fromLocalCssToPix(mTop)),
                    x + Math.round(coordinates.fromLocalCssToPix(mLeft + mWidth)),
                    y + Math.round(coordinates.fromLocalCssToPix(mTop + mHeight)));
        }
        // Legacy ToolbarPhone reads the fakebox's animated position instead of
        // applying the URL focus fraction to its own vertical translation.
        int top = Math.round(mLastBounds.top * (1f - mUrlFocusFraction));
        bounds.set(mLastBounds.left, top, mLastBounds.right,
                top + mLastBounds.bottom - mLastBounds.top);
        translation.set(0, 0);
    }

    private boolean isOmniboxFocused() {
        var activity = activity();
        var toolbar = activity == null || !isActive(activity) ? null : activity.getToolbarManager();
        return toolbar != null && toolbar.isUrlBarFocused();
    }

    private static boolean isOmniboxFocused(@Nullable Tab tab) {
        if (tab == null) return false;
        var activity = activity(tab.getWebContents());
        var toolbar = activity == null || activity.getActivityTab() != tab
                ? null : activity.getToolbarManager();
        return toolbar != null && toolbar.isUrlBarFocused();
    }

    private void setAlpha(float alpha) {
        alpha = Math.max(0f, Math.min(1f, alpha));
        boolean focused = isOmniboxFocused();
        if (mNativeObserver == 0 || (mAlpha == alpha && mFocused == focused)) return;
        mAlpha = alpha;
        mFocused = focused;
        ArgonHomeBridgeJni.get().onSearchBoxAlphaChanged(mNativeObserver, alpha, focused);
    }

    private static boolean isHomeUrl(GURL url) {
        return (url.getScheme().equals("chrome") || url.getScheme().equals("chrome-native"))
                && (url.getHost().equals("argon") || url.getHost().equals("newtab"));
    }

    private static @Nullable ArgonHomeBridge registered(@Nullable Tab tab) {
        if (tab == null || tab.isDestroyed() || tab.isIncognito()) return null;
        var reference = PAGES.get(tab.getWebContents());
        var page = reference == null ? null : reference.get();
        return page != null && page.mNativeObserver != 0 ? page : null;
    }

    public static boolean isRegistered(@Nullable Tab tab) { return registered(tab) != null; }

    /** Recognize our home before the renderer and the measured fakebox are ready. */
    public static boolean isHomeToolbarActive(@Nullable Tab tab) {
        if (tab == null || tab.isDestroyed() || tab.isIncognito() || tab.isShowingErrorPage()
                || EARLY_NAVIGATIONS.containsKey(tab) || !isHomeUrl(tab.getUrl())) return false;
        var contents = tab.getWebContents();
        if (contents != null && contents.isDestroyed()) return false;
        var reference = PAGES.get(contents);
        if (reference != null) {
            var page = reference.get();
            return page != null && page.mNativeObserver != 0 && !page.mNavigatingAway
                    && page.isSupported();
        }
        // Direct chrome://argon belongs to us even with an NTP override. Aliases must
        // preserve an extension's or enterprise policy's replacement new-tab page.
        return (tab.getUrl().getScheme().equals("chrome") && tab.getUrl().getHost().equals("argon"))
                || (tab.getUrl().getHost().equals("newtab") && !UrlOverrideUtils.isNtpOverrideEnabled());
    }

    private static @Nullable ArgonHomeBridge page(@Nullable Tab tab) {
        var page = registered(tab);
        return page != null && isHomeToolbarActive(tab) && page.mHasBounds ? page : null;
    }

    private static void refreshToolbar(Tab tab) {
        var activity = activity(tab.getWebContents());
        if (activity == null || activity.getActivityTab() != tab) return;
        var toolbar = activity.getToolbarManager();
        if (toolbar != null) toolbar.onArgonHomeSearchBoxChanged();
    }

    /** Show the normal address bar before an external navigation commits. */
    public static void onNavigationStarted(Tab tab, String url) {
        var page = registered(tab);
        boolean wasHome = isHomeToolbarActive(tab);
        boolean leaving = !isHomeUrl(new GURL(url));
        if (!leaving) {
            EARLY_NAVIGATIONS.remove(tab);
            var reference = PAGES.get(tab.getWebContents());
            if (reference != null && reference.get() == null) PAGES.remove(tab.getWebContents());
        } else if (page != null || wasHome) {
            EARLY_NAVIGATIONS.put(tab, true);
        }
        if (page != null) {
            page.mNavigatingAway = leaving;
            page.mHomeNavigation = leaving ? null : new WeakReference<>(tab);
        }
        if (page != null || wasHome || isHomeToolbarActive(tab)) refreshToolbar(tab);
    }

    /** A cancelled load must not mark a later, unrelated detachment as a home replacement. */
    public static void onPageLoadStopped(Tab tab) {
        var page = registered(tab);
        if (page != null) page.mHomeNavigation = null;
    }

    /** A renderer crash must end prediction even if it happens between home documents. */
    public static void onPageCrashed(Tab tab) {
        var page = registered(tab);
        if (page != null) page.mHomeNavigation = null;
        var contents = tab.getWebContents();
        if (contents == null || (!PAGES.containsKey(contents) && !isHomeToolbarActive(tab))) return;
        PAGES.put(contents, new WeakReference<>(null));
        refreshToolbar(tab);
    }

    /** A cancelled navigation must restore the homepage, but never hide another pending URL. */
    public static void onNavigationFinished(Tab tab, boolean hasPendingNavigation) {
        var page = registered(tab);
        boolean early = EARLY_NAVIGATIONS.containsKey(tab);
        if (!hasPendingNavigation) EARLY_NAVIGATIONS.remove(tab);
        if (page != null) {
            page.mNavigatingAway = hasPendingNavigation;
            if (hasPendingNavigation) page.mHomeNavigation = null;
        }
        if (page != null || early) refreshToolbar(tab);
    }

    /**
     * Wrap the phone delegate, querying its actual native NTP before accessing native views.
     * The stock delegate's isCurrentlyVisible() also accepts WebUI URLs without a NewTabPage.
     */
    public static NewTabPageDelegate wrap(
            Supplier<@Nullable Tab> currentTab, View toolbar, NewTabPageDelegate fallback,
            Callback<Float> transition, BooleanSupplier hasNativeNtp) {
        return new NewTabPageDelegate() {
            private @Nullable ArgonHomeBridge mVisiblePage;
            private boolean mWasShowingHome;

            private @Nullable ArgonHomeBridge current() { return page(currentTab.get()); }

            @Override
            public boolean wasShowingNtp() {
                return mWasShowingHome || fallback.wasShowingNtp();
            }

            @Override
            public boolean isCurrentlyVisible() {
                return isHomeToolbarActive(currentTab.get()) || fallback.isCurrentlyVisible();
            }

            @Override
            public boolean isIncognitoNewTabPageCurrentlyVisible() {
                return fallback.isIncognitoNewTabPageCurrentlyVisible();
            }

            @Override
            public boolean isLocationBarShown() {
                return isHomeToolbarActive(currentTab.get()) || fallback.isLocationBarShown();
            }

            @Override
            public boolean transitioningAwayFromLocationBar() {
                return (mWasShowingHome && !isHomeToolbarActive(currentTab.get()))
                        || fallback.transitioningAwayFromLocationBar();
            }

            @Override
            public boolean hasCompletedFirstLayout() {
                return isHomeToolbarActive(currentTab.get())
                        ? current() != null : fallback.hasCompletedFirstLayout();
            }

            @Override
            public void setSearchBoxScrollListener(Callback<Float> callback) {
                if (mVisiblePage != null) {
                    mVisiblePage.mScrollCallback = null;
                    mVisiblePage.setAlpha(1f);
                    transition.onResult(0f);
                }
                // Detach any previous native NTP's scroll listener through its own delegate.
                fallback.setSearchBoxScrollListener(callback);
                mWasShowingHome = isHomeToolbarActive(currentTab.get());
                mVisiblePage = current();
                if (mVisiblePage != null) {
                    mVisiblePage.mScrollCallback = fraction -> {
                        transition.onResult(fraction);
                        callback.onResult(fraction);
                    };
                    mVisiblePage.mScrollCallback.onResult(mVisiblePage.mScrollFraction);
                } else if (mWasShowingHome && !isOmniboxFocused(currentTab.get())) {
                    // Chromium hides its omnibox for an NTP without a completed first layout.
                    // Use that transition before JNI registration instead of exposing a frame
                    // of the old address bar while the WebUI renderer starts.
                    transition.onResult(0f);
                    callback.onResult(0f);
                }
            }

            @Override
            public void getSearchBoxBounds(Rect bounds, Point translation) {
                var page = current();
                if (page != null) page.bounds(toolbar, bounds, translation);
                else if (mVisiblePage != null) mVisiblePage.bounds(toolbar, bounds, translation);
                else if (hasNativeNtp.getAsBoolean()) fallback.getSearchBoxBounds(bounds, translation);
                else {
                    // An early WebUI NTP has neither measured bounds nor a native NewTabPage.
                    bounds.setEmpty();
                    translation.set(0, 0);
                }
            }

            @Override
            public int getSearchBoxBoundsVerticalInset() {
                return current() != null || !hasNativeNtp.getAsBoolean()
                        ? 0 : fallback.getSearchBoxBoundsVerticalInset();
            }

            @Override
            public void setSearchBoxAlpha(float alpha) {
                var page = current();
                if (page != null) page.setAlpha(alpha);
                else if (mVisiblePage != null) mVisiblePage.setAlpha(alpha);
                else if (hasNativeNtp.getAsBoolean()) fallback.setSearchBoxAlpha(alpha);
            }

            @Override
            public void setSearchProviderLogoAlpha(float alpha) {
                if (current() == null && mVisiblePage == null && hasNativeNtp.getAsBoolean()) {
                    fallback.setSearchProviderLogoAlpha(alpha);
                }
            }

            @Override
            public void setUrlFocusChangeAnimationPercent(float fraction) {
                var page = current();
                if (page == null) page = mVisiblePage;
                if (page != null) page.mUrlFocusFraction = Math.max(0f, Math.min(1f, fraction));
                else if (hasNativeNtp.getAsBoolean()) fallback.setUrlFocusChangeAnimationPercent(fraction);
            }

            @Override
            public boolean dispatchTouchEvent(MotionEvent event) {
                var page = current();
                var activity = page == null ? null : page.activity();
                Tab tab = activity == null ? null : activity.getActivityTab();
                View content = tab == null ? null : tab.getContentView();
                return page != null && content != null
                        ? content.dispatchTouchEvent(event)
                        : mVisiblePage == null && hasNativeNtp.getAsBoolean()
                                && fallback.dispatchTouchEvent(event);
            }
        };
    }

    @NativeMethods
    interface Natives {
        void onSearchBoxAlphaChanged(long observer, float alpha, boolean focused);
    }
}
