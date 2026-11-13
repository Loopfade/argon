// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.externalnav;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.widget.Toast;

import org.chromium.base.ContextUtils;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.components.embedder_support.util.UrlUtilities;
import org.chromium.url.GURL;

import java.util.HashSet;
import java.util.Set;
import java.util.TreeSet;

/** User-owned whole-site rules. Permission never comes from the referring page. */
@NullMarked
public final class ArgonExternalNavigationPolicy {
    public static final int ALLOW = 0;
    public static final int ALLOW_LIST = 1;
    public static final int BLOCK = 2;
    public static final int MAX_SITES = 100;
    private static final String MODE = "mode";
    private static final String SITES = "sites";
    private static @Nullable ArgonExternalNavigationPolicy sInstance;
    private final SharedPreferences mPreferences;
    private @Nullable Set<String> mSavedSnapshot;
    private Set<String> mCanonicalSites = Set.of();

    private ArgonExternalNavigationPolicy(SharedPreferences preferences) {
        mPreferences = preferences;
    }

    public static ArgonExternalNavigationPolicy get() {
        if (sInstance == null) {
            sInstance = new ArgonExternalNavigationPolicy(ContextUtils.getApplicationContext()
                    .getSharedPreferences("argon_external_navigation", Context.MODE_PRIVATE));
        }
        return sInstance;
    }

    public int getMode() {
        int mode;
        try {
            mode = mPreferences.getInt(MODE, ALLOW);
        } catch (ClassCastException ignored) {
            return BLOCK;
        }
        return mode == ALLOW || mode == ALLOW_LIST ? mode : BLOCK;
    }

    public void setMode(int mode) {
        if (mode < ALLOW || mode > BLOCK) throw new IllegalArgumentException("Invalid mode");
        mPreferences.edit().putInt(MODE, mode).apply();
    }

    /** Always copy SharedPreferences' set; another screen may have changed it. */
    public Set<String> getSites() {
        try {
            Set<String> saved = mPreferences.getStringSet(SITES, Set.of());
            if (saved == null) saved = Set.of();
            // Navigation runs on the UI thread. Parse domains only when settings change.
            if (mSavedSnapshot == null || !mSavedSnapshot.equals(saved)) {
                Set<String> result = new TreeSet<>();
                for (String site : saved) {
                    String host = normalizeSite(site);
                    if (host != null && result.size() < MAX_SITES) result.add(host);
                }
                mSavedSnapshot = new HashSet<>(saved);
                mCanonicalSites = result;
            }
        } catch (ClassCastException ignored) {
            mSavedSnapshot = null;
            mCanonicalSites = Set.of();
        }
        return new TreeSet<>(mCanonicalSites);
    }

    public boolean addSite(String value) {
        String host = normalizeSite(value);
        if (host == null) return false;
        Set<String> sites = getSites();
        if (sites.size() >= MAX_SITES || !sites.add(host)) return false;
        mPreferences.edit().putStringSet(SITES, new HashSet<>(sites)).apply();
        return true;
    }

    public void removeSite(String host) {
        Set<String> sites = getSites();
        sites.remove(host);
        mPreferences.edit().putStringSet(SITES, new HashSet<>(sites)).apply();
    }

    /** Accept a domain or a pasted HTTP(S) link, preserving the full entered host. */
    public static @Nullable String normalizeSite(String value) {
        String input = value.trim();
        if (input.isEmpty() || input.length() > 16384 || input.matches("(?s).*[\\\\\\s*].*")) return null;
        GURL url = new GURL(input.contains("://") ? input : "https://" + input);
        if (!url.isValid() || !UrlUtilities.isHttpOrHttps(url)
                || !url.getUsername().isEmpty() || !url.getPassword().isEmpty()) return null;
        String host = networkHost(url);
        if (host.isEmpty()) return null;
        // A registry such as co.uk or github.io must not grant access to unrelated sites.
        String registered = UrlUtilities.getDomainAndRegistry("https://" + host + "/", true);
        return registered == null || registered.isEmpty() ? null : host;
    }

    private static String networkHost(GURL url) {
        if (!url.isValid() || !UrlUtilities.isHttpOrHttps(url)) return "";
        String host = url.getHost();
        if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        return host.contains(".") && !host.startsWith(".") && !host.endsWith(".")
                && !host.contains("..") ? host : "";
    }

    public boolean shouldBlock(GURL url, Intent targetIntent) {
        int mode = getMode();
        if (mode == ALLOW || isBrowserScheme(url.getScheme())) return false;
        if (mode == BLOCK) return true;

        GURL destination = url;
        if ("intent".equals(url.getScheme())) {
            // The fallback/referrer cannot authorize an app. Only its actual web destination can.
            var data = targetIntent.getData();
            if (data == null) return true;
            destination = new GURL(data.toString());
        }
        String host = networkHost(destination);
        if (host.isEmpty()) return true;
        for (String site : getSites()) {
            if (host.equals(site) || host.endsWith("." + site)) return false;
        }
        return true;
    }

    private static boolean isBrowserScheme(String scheme) {
        // Preserve browser-internal navigation and local documents.
        return switch (scheme) {
            case "about", "chrome", "chrome-native", "chrome-search", "devtools", "data",
                    "javascript", "blob", "file", "content", "filesystem" -> true;
            default -> false;
        };
    }

    public static void showBlockedMessage() {
        Toast.makeText(ContextUtils.getApplicationContext(),
                R.string.argon_external_blocked, Toast.LENGTH_SHORT).show();
    }
}
