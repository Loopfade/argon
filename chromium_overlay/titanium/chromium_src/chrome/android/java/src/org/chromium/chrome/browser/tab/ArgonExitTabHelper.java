// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.tab;

import org.chromium.build.annotations.NullMarked;

/** Keeps access to Chromium's package-private TabImpl inside its owning package. */
@NullMarked
public final class ArgonExitTabHelper {
    private ArgonExitTabHelper() {}

    public static boolean prepare(Tab tab, boolean preserveNavigation) {
        if (tab.isDestroyed()) return true;
        if (tab instanceof TabImpl tabImpl) {
            return tabImpl.prepareForArgonExit(preserveNavigation);
        }
        // An unknown live implementation must not keep running during deletion.
        return tab.getWebContents() == null;
    }
}
