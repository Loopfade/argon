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
import org.chromium.components.omnibox.AutocompleteInput;
import org.chromium.components.omnibox.OmniboxFocusReason;
import org.chromium.content_public.browser.RenderCoordinates;
import org.chromium.content_public.browser.WebContents;
import org.chromium.url.GURL;

import java.lang.ref.WeakReference;
import java.util.WeakHashMap;
import java.util.function.Supplier;

/** Adapts the bundled WebUI search box to Chromium's existing NTP/omnibox transitions. */
@NullMarked
public final class ArgonHomeBridge {
    private static final WeakHashMap<WebContents, WeakReference<ArgonHomeBridge>> PAGES =
            new WeakHashMap<>();
    private final WebContents mContents;
    private final Rect mLastBounds = new Rect();
    private long mNativeObserver;
    private boolean mHasBounds;
    private boolean mNavigatingAway;
    private float mLeft;
    private float mTop;
    private float mWidth;
    private float mHeight;
    private float mScrollFraction;
    private float mAlpha = Float.NaN;
    private boolean mFocused;
    private @Nullable Callback<Float> mScrollCallback;

    private ArgonHomeBridge(WebContents contents, long observer) {
        mContents = contents;
        mNativeObserver = observer;
    }

    private @Nullable ChromeTabbedActivity activity() {
        if (mContents.isDestroyed()) return null;
        var window = mContents.getTopLevelNativeWindow();
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

    @CalledByNative
    private void destroy() {
        var reference = PAGES.get(mContents);
        if (reference != null && reference.get() == this) PAGES.remove(mContents);
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
        mLeft = left;
        mTop = top;
        mWidth = width;
        mHeight = height;
        mScrollFraction = Math.max(0f, Math.min(1f, scroll));
        mHasBounds = width > 0f && height > 0f;
        if (firstLayout && mHasBounds) refreshToolbar();
        else if (mScrollCallback != null) mScrollCallback.onResult(mScrollFraction);
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
        bounds.set(mLastBounds);
        translation.set(0, 0);
    }

    private void setAlpha(float alpha) {
        alpha = Math.max(0f, Math.min(1f, alpha));
        var activity = activity();
        var toolbar = activity == null || !isActive(activity) ? null : activity.getToolbarManager();
        boolean focused = toolbar != null && toolbar.isUrlBarFocused();
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

    private static @Nullable ArgonHomeBridge page(@Nullable Tab tab) {
        var page = registered(tab);
        return page != null && tab != null && !tab.isShowingErrorPage() && isHomeUrl(tab.getUrl())
                && !page.mNavigatingAway && page.mHasBounds && page.isSupported() ? page : null;
    }

    /** Show the normal address bar before an external navigation commits. */
    public static void onNavigationStarted(Tab tab, String url) {
        var page = registered(tab);
        if (page == null) return;
        page.mNavigatingAway = !isHomeUrl(new GURL(url));
        page.refreshToolbar();
    }

    /** A cancelled navigation must restore the homepage, but never hide another pending URL. */
    public static void onNavigationFinished(Tab tab, boolean hasPendingNavigation) {
        var page = registered(tab);
        if (page == null) return;
        page.mNavigatingAway = hasPendingNavigation;
        page.refreshToolbar();
    }

    /** Only the phone delegate is wrapped; the native tablet and incognito policies remain intact. */
    public static NewTabPageDelegate wrap(
            Supplier<@Nullable Tab> currentTab, View toolbar, NewTabPageDelegate fallback,
            Callback<Float> transition) {
        return new NewTabPageDelegate() {
            private @Nullable ArgonHomeBridge mVisiblePage;

            private @Nullable ArgonHomeBridge current() { return page(currentTab.get()); }

            @Override
            public boolean wasShowingNtp() {
                return mVisiblePage != null || fallback.wasShowingNtp();
            }

            @Override
            public boolean isCurrentlyVisible() {
                return current() != null || fallback.isCurrentlyVisible();
            }

            @Override
            public boolean isIncognitoNewTabPageCurrentlyVisible() {
                return fallback.isIncognitoNewTabPageCurrentlyVisible();
            }

            @Override
            public boolean isLocationBarShown() {
                return current() != null || fallback.isLocationBarShown();
            }

            @Override
            public boolean transitioningAwayFromLocationBar() {
                return (mVisiblePage != null && current() == null)
                        || fallback.transitioningAwayFromLocationBar();
            }

            @Override
            public boolean hasCompletedFirstLayout() {
                return current() != null || fallback.hasCompletedFirstLayout();
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
                mVisiblePage = current();
                if (mVisiblePage != null) {
                    mVisiblePage.mScrollCallback = fraction -> {
                        transition.onResult(fraction);
                        callback.onResult(fraction);
                    };
                    mVisiblePage.mScrollCallback.onResult(mVisiblePage.mScrollFraction);
                }
            }

            @Override
            public void getSearchBoxBounds(Rect bounds, Point translation) {
                var page = current();
                if (page != null) page.bounds(toolbar, bounds, translation);
                else if (mVisiblePage != null) mVisiblePage.bounds(toolbar, bounds, translation);
                else fallback.getSearchBoxBounds(bounds, translation);
            }

            @Override
            public int getSearchBoxBoundsVerticalInset() {
                return current() != null ? 0 : fallback.getSearchBoxBoundsVerticalInset();
            }

            @Override
            public void setSearchBoxAlpha(float alpha) {
                var page = current();
                if (page != null) page.setAlpha(alpha);
                else if (mVisiblePage != null) mVisiblePage.setAlpha(alpha);
                else fallback.setSearchBoxAlpha(alpha);
            }

            @Override
            public void setSearchProviderLogoAlpha(float alpha) {
                if (current() == null && mVisiblePage == null) fallback.setSearchProviderLogoAlpha(alpha);
            }

            @Override
            public void setUrlFocusChangeAnimationPercent(float fraction) {
                if (current() == null && mVisiblePage == null) fallback.setUrlFocusChangeAnimationPercent(fraction);
            }

            @Override
            public boolean dispatchTouchEvent(MotionEvent event) {
                var page = current();
                var activity = page == null ? null : page.activity();
                Tab tab = activity == null ? null : activity.getActivityTab();
                View content = tab == null ? null : tab.getContentView();
                return page != null && content != null
                        ? content.dispatchTouchEvent(event)
                        : mVisiblePage == null && fallback.dispatchTouchEvent(event);
            }
        };
    }

    @NativeMethods
    interface Natives {
        void onSearchBoxAlphaChanged(long observer, float alpha, boolean focused);
    }
}
