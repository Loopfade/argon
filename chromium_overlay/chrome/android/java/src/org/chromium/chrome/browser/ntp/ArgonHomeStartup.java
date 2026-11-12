// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.ntp;

import android.os.Handler;
import android.os.Looper;

import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.browser.exit.ArgonExitCoordinator;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tab.TabObserver;
import org.chromium.content_public.browser.WebContents;

import java.util.ArrayList;
import java.util.WeakHashMap;

/** Holds the launch cover until the configured homepage has a renderer frame. */
@NullMarked
public final class ArgonHomeStartup {
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    // A failed renderer/transport must still expose Chromium's recovery UI.
    private static final long MAX_WAIT_MS = 3_000;
    private static final WeakHashMap<WebContents, Boolean> READY = new WeakHashMap<>();
    private static final ArrayList<Waiter> WAITERS = new ArrayList<>();

    private ArgonHomeStartup() {}

    public static void waitForFirstFrame(@Nullable Tab tab, Runnable ready) {
        if (tab == null || !ArgonHomeBridge.isHomeStartup(tab)
                || ArgonExitCoordinator.isBlockingPages()) {
            ready.run();
            return;
        }
        new Waiter(tab, ready).start();
    }

    static void reset(WebContents contents) { READY.remove(contents); }

    static void onFirstFrameReady(WebContents contents) {
        if (contents.isDestroyed()) return;
        READY.put(contents, true);
        for (var waiter : new ArrayList<>(WAITERS)) waiter.check();
    }

    private static final class Waiter implements TabObserver, Runnable {
        private final Tab mTab;
        private @Nullable Runnable mReady;

        Waiter(Tab tab, Runnable ready) { mTab = tab; mReady = ready; }

        void start() {
            WAITERS.add(this);
            mTab.addObserver(this);
            MAIN.postDelayed(this, MAX_WAIT_MS);
            check();
        }

        void check() {
            if (mReady == null) return;
            var contents = mTab.getWebContents();
            if (mTab.isDestroyed() || mTab.isShowingErrorPage()
                    || !ArgonHomeBridge.isHomeStartup(mTab)
                    || ArgonExitCoordinator.isBlockingPages()
                    || (contents != null && READY.containsKey(contents))) {
                run();
            }
        }

        @Override
        public void onContentChanged(Tab tab) { check(); }

        @Override
        public void onUrlUpdated(Tab tab) { check(); }

        @Override
        public void onCrash(Tab tab) { run(); }

        @Override
        public void onDestroyed(Tab tab) { run(); }

        @Override
        public void run() {
            var ready = mReady;
            if (ready == null) return;
            mReady = null;
            MAIN.removeCallbacks(this);
            mTab.removeObserver(this);
            WAITERS.remove(this);
            ready.run();
        }
    }
}
