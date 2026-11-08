// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.base;

import org.chromium.build.annotations.NullMarked;
import org.chromium.chrome.browser.content.ContentUtils;
import org.chromium.chrome.browser.desktop_site.DesktopSiteUtils;
import org.chromium.chrome.browser.profiles.Profile;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tab.TabObserver;
import org.chromium.components.browser_ui.site_settings.WebsitePreferenceBridge;
import org.chromium.components.content_settings.ContentSetting;
import org.chromium.components.content_settings.ContentSettingsType;
import org.chromium.url.GURL;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.Map;
import java.util.WeakHashMap;

/** Applies a saved browser family to live renderers and Chromium's desktop-site settings. */
@NullMarked
public final class ArgonUserAgentController {
    // Values must not retain a Tab or its WebContents through a weak map key.
    private static final Map<Tab, Boolean> sTabs = new WeakHashMap<>();
    private static int sGeneration;
    private static final TabObserver OBSERVER = new TabObserver() {
        @Override
        public void onShown(Tab tab, int type) {
            reloadIfNeeded(tab);
        }

        @Override
        public void onPageLoadStarted(Tab tab, GURL url) {
            if (sTabs.containsKey(tab)) sTabs.put(tab, false);
        }

        @Override
        public void onDestroyed(Tab tab) {
            sTabs.remove(tab);
        }
    };

    private ArgonUserAgentController() {}

    public static void initialize() {
        ArgonUserAgent.setChangeListener(ArgonUserAgentController::onPreferenceChanged);
    }

    /** Called before TabImpl calculates the initial desktop-site mode. */
    public static void onTabInitialized(Tab tab) {
        initialize();
        if (!sTabs.containsKey(tab)) tab.addObserver(OBSERVER);
        sTabs.put(tab, false);
        // Migrate presets saved by versions which only changed the startup UA.
        // Once synchronized, subsequent launches preserve manual desktop-site settings.
        if (ArgonUserAgent.needsModeSync()) {
            Boolean desktop = ArgonUserAgent.requestedDesktopMode();
            if (desktop != null) setDefaultMode(tab.getProfile(), desktop);
            if (!tab.isOffTheRecord()) ArgonUserAgent.markModeSynced();
        }
    }

    static void onPreferenceChanged() {
        final int generation = ++sGeneration;
        // Acknowledge updates in all live renderers before loading new documents.
        ContentUtils.updateUserAgent(ArgonUserAgent.getUserAgent(false),
                ArgonUserAgent.getUserAgent(true), () -> {
            if (generation != sGeneration) return;
            var tabs = new ArrayList<>(sTabs.keySet());
            Boolean desktop = ArgonUserAgent.requestedDesktopMode();
            var profiles = new HashSet<Profile>();
            boolean regularProfileUpdated = false;
            for (Tab tab : tabs) {
                if (tab.isDestroyed() || tab.isClosing()) continue;
                if (desktop != null && profiles.add(tab.getProfile())) {
                    setDefaultMode(tab.getProfile(), desktop);
                    regularProfileUpdated |= !tab.isOffTheRecord();
                }
            }
            if (regularProfileUpdated) ArgonUserAgent.markModeSynced();
            for (Tab tab : tabs) {
                if (tab.isDestroyed() || tab.isClosing()) continue;
                var contents = tab.getWebContents();
                if (contents == null || contents.isDestroyed()) continue;
                GURL url = tab.getUrl();
                if (desktop != null && ("http".equals(url.getScheme())
                        || "https".equals(url.getScheme()))) {
                    DesktopSiteUtils.setRequestDesktopSiteContentSettingsForUrl(
                            tab.getProfile(), url, desktop);
                }
                boolean useDesktop = DesktopSiteUtils.shouldOverrideDesktopSite(
                        tab.getProfile(), url, tab.getContext());
                sTabs.put(tab, true);
                ContentUtils.setUserAgentOverride(contents, useDesktop);
                contents.getNavigationController().setUseDesktopUserAgent(
                        useDesktop, false, false);
                if (!tab.isHidden()) reloadIfNeeded(tab);
            }
        });
    }

    private static void setDefaultMode(Profile profile, boolean desktop) {
        WebsitePreferenceBridge.setDefaultContentSetting(
                profile, ContentSettingsType.REQUEST_DESKTOP_SITE,
                desktop ? ContentSetting.ALLOW : ContentSetting.BLOCK);
    }

    private static void reloadIfNeeded(Tab tab) {
        if (!Boolean.TRUE.equals(sTabs.get(tab))) return;
        sTabs.put(tab, false);
        if (!tab.isDestroyed() && !tab.isClosing() && !tab.isNativePage()
                && tab.getWebContents() != null) {
            tab.reload();
        }
    }
}
