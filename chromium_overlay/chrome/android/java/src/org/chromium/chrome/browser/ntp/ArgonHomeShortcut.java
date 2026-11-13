// Copyright 2026 The Argon Authors
// Use of this source code is governed by the repository's LICENSE file.

package org.chromium.chrome.browser.ntp;

import android.net.Uri;

import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.url.GURL;

/** Opens a draft in the homepage's existing editor; never writes homepage data. */
@NullMarked
public final class ArgonHomeShortcut {
    private ArgonHomeShortcut() {}

    public static boolean canAdd(@Nullable Tab tab) {
        if (tab == null
                || tab.isDestroyed()
                || tab.isIncognito()
                || tab.isNativePage()
                || tab.isShowingErrorPage()) {
            return false;
        }
        GURL url = tab.getUrl();
        return url.isValid()
                && ("https".equals(url.getScheme()) || "http".equals(url.getScheme()))
                && !url.getHost().isEmpty()
                && url.getSpec().length() <= 16384;
    }

    public static @Nullable String draftUrl(@Nullable Tab tab) {
        // Recheck when the command is selected: the tab may have navigated or closed.
        if (!canAdd(tab) || tab == null) return null;
        String title = tab.getTitle();
        int end = Math.min(title.length(), 512);
        if (end > 0 && Character.isHighSurrogate(title.charAt(end - 1))) end--;
        // The fragment is not part of the WebUI resource request. The page consumes
        // it once and requires an explicit Save; these values never become JavaScript.
        return "chrome://argon/#add-site?url="
                + Uri.encode(tab.getUrl().getSpec())
                + "&title="
                + Uri.encode(title.substring(0, end));
    }
}
