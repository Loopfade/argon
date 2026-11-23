// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.ntp;

import android.os.Handler;
import android.os.Looper;

import org.chromium.base.Log;

import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.browser.exit.ArgonExitCoordinator;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tab.TabObserver;
import org.chromium.content_public.browser.WebContents;
import org.chromium.url.GURL;

import java.util.ArrayList;
import java.util.WeakHashMap;

/** Holds the launch cover until the configured homepage has a renderer frame. */
@NullMarked
public final class ArgonHomeStartup {
    private static final String TAG = "ArgonHomeStartup";
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    // A failed renderer/transport must still expose Chromium's recovery UI.
    private static final long MAX_WAIT_MS = 3_000;
    private static final WeakHashMap<WebContents, Boolean> READY = new WeakHashMap<>();
    private static final ArrayList<Waiter> WAITERS = new ArrayList<>();
    // Coalesce concurrent startup waiters into one reload; reset when all detach.
    private static final WeakHashMap<Tab, Boolean> RETRIED = new WeakHashMap<>();

    private ArgonHomeStartup() {}

    // Log only internal NTP origins, never a site's full URL, query or path.
    private static String safeUrl(@Nullable Tab tab) {
        if (tab == null) return "<no-tab>";
        GURL url = tab.getUrl();
        if (url == null) return "<no-url>";
        String scheme = url.getScheme();
        String host = url.getHost();
        if (("chrome".equals(scheme) || "chrome-native".equals(scheme))
                && ("argon".equals(host) || "newtab".equals(host))) {
            return scheme + "://" + host + "/";
        }
        return "<external>";
    }

    private static void logState(String event, @Nullable Tab tab, String reason) {
        WebContents contents = tab == null ? null : tab.getWebContents();
        Log.i(TAG, "event=%s reason=%s url=%s loading=%b tabDestroyed=%b "
                        + "contents=%s contentsId=%d exitBlocked=%b",
                event, reason, safeUrl(tab),
                tab != null && !tab.isDestroyed() && tab.isLoading(),
                tab != null && tab.isDestroyed(),
                contents == null ? "none" : contents.isDestroyed() ? "destroyed" : "attached",
                contents == null ? 0 : System.identityHashCode(contents),
                ArgonExitCoordinator.isBlockingPages());
    }

    public static void waitForFirstFrame(@Nullable Tab tab, Runnable ready) {
        if (tab == null || !ArgonHomeBridge.isHomeStartup(tab)
                || ArgonExitCoordinator.isBlockingPages()) {
            logState("bypass", tab, ArgonExitCoordinator.isBlockingPages()
                    ? "exit-blocked" : "not-home");
            ready.run();
            return;
        }
        new Waiter(tab, ready).start();
    }

    static void reset(WebContents contents) { READY.remove(contents); }

    static void onFirstFrameReady(WebContents contents) {
        if (contents.isDestroyed()) {
            Log.w(TAG, "event=firstFrameReady reason=destroyed-contents contentsId=%d",
                    System.identityHashCode(contents));
            return;
        }
        Log.i(TAG, "event=firstFrameReady reason=visual-state-success contentsId=%d",
                System.identityHashCode(contents));
        READY.put(contents, true);
        for (var waiter : new ArrayList<>(WAITERS)) waiter.check();
    }

    private static void clearRetryWhenIdle(Tab tab) {
        // A Tab may survive Home -> site -> Home and a later explicit Exit.
        // Keep the guard while any concurrent waiter is active, but do not
        // permanently suppress recovery on the next startup attempt.
        for (var waiter : WAITERS) {
            if (waiter.mTab == tab) return;
        }
        RETRIED.remove(tab);
    }

    private static final class Waiter implements TabObserver, Runnable {
        private final Tab mTab;
        private @Nullable Runnable mReady;
        private final Runnable mTimeout = this::onTimeout;
        private boolean mRecoveryStarted;

        Waiter(Tab tab, Runnable ready) { mTab = tab; mReady = ready; }

        void start() {
            logState("wait", mTab, "first-frame");
            WAITERS.add(this);
            mTab.addObserver(this);
            MAIN.postDelayed(mTimeout, MAX_WAIT_MS);
            check();
        }

        void check() {
            if (mReady == null) return;
            var contents = mTab.getWebContents();
            if (mTab.isDestroyed()) finish("tab-destroyed");
            else if (mTab.isShowingErrorPage()) finish("error-page");
            else if (!ArgonHomeBridge.isHomeStartup(mTab)) finish("not-home");
            else if (ArgonExitCoordinator.isBlockingPages()) finish("exit-blocked");
            else if (contents != null && READY.containsKey(contents)) finish("frame-ready");
        }

        private @Nullable String retryBlockReason(@Nullable WebContents contents) {
            if (mRecoveryStarted) return "retry-limit";
            if (mTab.isDestroyed()) return "tab-destroyed";
            if (mTab.isShowingErrorPage()) return "error-page";
            // Another waiter may have started our shared recovery reload. Keep
            // its cover until a frame or the second timeout, even while loading.
            if (mTab.isLoading() && !RETRIED.containsKey(mTab)) return "active-load";
            if (!ArgonHomeBridge.isHomeStartup(mTab)) return "not-home";
            if (ArgonExitCoordinator.isBlockingPages()) return "exit-blocked";
            if (contents == null) return "no-webcontents";
            if (contents.isDestroyed()) return "webcontents-destroyed";
            if (READY.containsKey(contents)) return "frame-ready";
            return null;
        }

        private void onTimeout() {
            if (mReady == null) return;
            logState("timeout", mTab, mRecoveryStarted ? "after-retry" : "first-wait");
            // The launch cover alone cannot repair a restored WebUI whose renderer
            // never produced a frame (notably after an explicit browser exit).
            // Retry only once per active group of waiters, then release the recovery UI.
            String blocked = retryBlockReason(mTab.getWebContents());
            if (blocked == null) {
                mRecoveryStarted = true;
                if (!RETRIED.containsKey(mTab)) {
                    RETRIED.put(mTab, true);
                    logState("reload", mTab, "missing-first-frame");
                    mTab.reload();
                } else {
                    logState("retry-skipped", mTab, "coalesced-with-other-waiter");
                }
                MAIN.postDelayed(mTimeout, MAX_WAIT_MS);
                return;
            }
            logState("retry-skipped", mTab, blocked);
            finish("timeout-" + blocked);
        }

        @Override
        public void onContentChanged(Tab tab) { check(); }

        @Override
        public void onUrlUpdated(Tab tab) { check(); }

        @Override
        public void onCrash(Tab tab) { finish("renderer-crashed"); }

        @Override
        public void onDestroyed(Tab tab) { finish("tab-destroyed"); }

        @Override
        public void run() { finish("observer"); }

        private void finish(String reason) {
            var ready = mReady;
            if (ready == null) return;
            mReady = null;
            logState("release", mTab, reason);
            MAIN.removeCallbacks(mTimeout);
            mTab.removeObserver(this);
            WAITERS.remove(this);
            clearRetryWhenIdle(mTab);
            ready.run();
        }
    }
}
