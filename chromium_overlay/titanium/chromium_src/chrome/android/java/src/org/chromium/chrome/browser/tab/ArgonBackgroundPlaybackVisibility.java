// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.tab;

import org.chromium.build.annotations.NullMarked;

/** Keeps access to Chromium's package-private TabImpl inside the tab package. */
@NullMarked
public final class ArgonBackgroundPlaybackVisibility {
    private ArgonBackgroundPlaybackVisibility() {}

    public static void refresh(Tab tab) {
        if (!tab.isDestroyed() && tab instanceof TabImpl tabImpl) {
            tabImpl.refreshBackgroundPlaybackVisibility();
        }
    }
}
