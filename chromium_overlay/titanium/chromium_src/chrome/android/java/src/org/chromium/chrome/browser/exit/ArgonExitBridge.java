// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import org.jni_zero.CalledByNative;
import org.jni_zero.JniType;
import org.jni_zero.NativeMethods;

import org.chromium.base.Callback;
import org.chromium.chrome.browser.profiles.Profile;
import org.chromium.chrome.browser.tab.TabImpl;
import org.chromium.chrome.browser.tabmodel.TabClosureParams;
import org.chromium.chrome.browser.tabmodel.TabModel;

import java.util.ArrayList;

/** Native removal reports failures instead of discarding failed_data_types. */
final class ArgonExitBridge {
    @CalledByNative
    private static boolean isBlocked() { return ArgonExitCoordinator.isBlockingPages(); }

    @CalledByNative
    private static boolean prepareModel(TabModel model, boolean closeTabs) {
        var tabs = new ArrayList<TabImpl>();
        for (int i = 0; i < model.getCount(); ++i) {
            var tab = model.getTabAt(i);
            if (tab instanceof TabImpl) tabs.add((TabImpl) tab);
        }
        for (TabImpl tab : tabs) {
            if (!tab.prepareForArgonExit(!closeTabs && !model.isOffTheRecord())) return false;
        }
        if (closeTabs || model.isOffTheRecord()) {
            model.getTabRemover().forceCloseTabs(TabClosureParams.closeAllTabs()
                    .uponExit(true).allowUndo(false).saveToTabRestoreService(false)
                    .hideTabGroups(true)
                    .allowUnloadHandlers(false).build());
            model.commitAllTabClosures();
        }
        return true;
    }
    @NativeMethods
    interface Natives {
        @JniType("std::string") String profileKey(@JniType("Profile*") Profile profile);
        void prepare(@JniType("Profile*") Profile profile, boolean closeTabs, boolean recovery,
                Callback<Boolean> done);
        void clear(@JniType("Profile*") Profile profile, int types, Callback<Integer> done);
        void resume(@JniType("Profile*") Profile profile);
    }
}
