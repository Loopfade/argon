// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.media;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import org.chromium.base.ContextUtils;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.url.GURL;

import java.util.ArrayList;
import java.util.List;

/** Private app settings; never exposed to websites or applied to off-the-record tabs. */
@NullMarked
public final class ArgonBackgroundPlaybackStore {
    public static final String ENABLED = "enabled";
    private static final String RULES = "rules";
    private static @Nullable ArgonBackgroundPlaybackStore sInstance;

    public final ArgonBackgroundPlaybackPolicy policy = new ArgonBackgroundPlaybackPolicy();
    public final SharedPreferences preferences;

    private ArgonBackgroundPlaybackStore() {
        preferences = ContextUtils.getApplicationContext().getSharedPreferences(
                "argon_background_playback", Context.MODE_PRIVATE);
        reload();
    }

    public static ArgonBackgroundPlaybackStore get() {
        if (sInstance == null) sInstance = new ArgonBackgroundPlaybackStore();
        return sInstance;
    }

    public void reload() {
        List<ArgonBackgroundPlaybackPolicy.Rule> rules = new ArrayList<>();
        try {
            JSONArray saved = new JSONArray(preferences.getString(RULES, "[]"));
            for (int i = 0; i < saved.length() && rules.size() < ArgonBackgroundPlaybackPolicy.MAX_SITES; i++) {
                JSONObject item = saved.getJSONObject(i);
                String host = normalizeHost(item.getString("host"));
                int minutes = item.getInt("minutes");
                if (host == null || minutes < 1 || minutes > ArgonBackgroundPlaybackPolicy.MAX_MINUTES) continue;
                boolean duplicate = false;
                for (var rule : rules) duplicate |= rule.host.equals(host);
                if (!duplicate) {
                    rules.add(new ArgonBackgroundPlaybackPolicy.Rule(host, minutes,
                            item.optBoolean("enabled", false), item.optBoolean("subdomains", false)));
                }
            }
        } catch (JSONException ignored) {
            // A corrupt list grants no new permissions.
            rules.clear();
        }
        policy.configure(preferences.getBoolean(ENABLED, false), rules);
    }

    public void save(List<ArgonBackgroundPlaybackPolicy.Rule> rules) {
        if (rules.size() > ArgonBackgroundPlaybackPolicy.MAX_SITES) throw new IllegalArgumentException("Too many sites");
        JSONArray values = new JSONArray();
        for (var rule : rules) {
            JSONObject item = new JSONObject();
            try {
                item.put("host", rule.host);
                item.put("minutes", rule.minutes);
                item.put("enabled", rule.enabled);
                item.put("subdomains", rule.subdomains);
            } catch (JSONException impossible) {
                throw new IllegalStateException(impossible);
            }
            values.put(item);
        }
        preferences.edit().putString(RULES, values.toString()).apply();
        reload();
    }

    public static @Nullable String normalizeHost(String value) {
        String host = value.trim();
        if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        if (host.isEmpty() || host.matches("(?s).*[\\s/:@?#%*\\\\\\[\\]].*")) return null;
        GURL url = new GURL("https://" + host + "/");
        if (!url.isValid() || !url.getHost().contains(".")) return null;
        return url.getHost();
    }
}
