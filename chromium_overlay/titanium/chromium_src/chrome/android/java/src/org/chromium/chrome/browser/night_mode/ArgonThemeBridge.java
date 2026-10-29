// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.night_mode;

import org.jni_zero.CalledByNative;
import org.jni_zero.NativeMethods;

/** A WebUI-scoped subscription to the same theme provider as browser activities. */
final class ArgonThemeBridge implements NightModeStateProvider.Observer {
    private final NightModeStateProvider mProvider;
    private long mObserver;

    private ArgonThemeBridge(long observer) {
        mObserver = observer;
        mProvider = GlobalNightModeStateProviderHolder.getInstance();
        mProvider.addObserver(this);
    }

    @CalledByNative
    private static ArgonThemeBridge create(long observer) {
        return new ArgonThemeBridge(observer);
    }

    @CalledByNative
    private void destroy() {
        mProvider.removeObserver(this);
        mObserver = 0;
    }

    /** Values shared with argon_theme_handler.cc: light, dark, AMOLED. */
    @CalledByNative
    private static int getTheme() {
        boolean dark = GlobalNightModeStateProviderHolder.getInstance().isInNightMode();
        if (NightModeUtils.isAmoledActive(dark)) return 2;
        return dark ? 1 : 0;
    }

    @Override
    public void onNightModeStateChanged() {
        if (mObserver != 0) {
            ArgonThemeBridgeJni.get().onThemeChanged(mObserver, getTheme());
        }
    }

    @NativeMethods
    interface Natives {
        void onThemeChanged(long observer, int theme);
    }
}
