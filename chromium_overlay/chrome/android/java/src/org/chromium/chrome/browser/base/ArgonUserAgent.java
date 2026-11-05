// Copyright 2026 Argon contributors
// Use of this source code is governed by a GPL-2.0-only style license that can be
// found in the LICENSE file.

package org.chromium.chrome.browser.base;

import android.content.SharedPreferences;

import org.chromium.base.CommandLine;
import org.chromium.base.ContextUtils;
import org.chromium.base.version_info.VersionConstants;
import org.chromium.build.annotations.NullMarked;

@NullMarked
public final class ArgonUserAgent {
    private static final String PREF_PRESET = "argon_user_agent.preset";
    private static final String PREF_CUSTOM = "argon_user_agent.custom";
    private static final String WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64";
    private static final String CHROMIUM = " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/";
    private static final String CHROME = CHROMIUM + VersionConstants.PRODUCT_MAJOR_VERSION + ".0.0.0";
    private static final String FIREFOX = "154.0";
    private static final int MAX_CUSTOM_LENGTH = 1024;

    public enum Preset {
        CHROME_MOBILE("Mozilla/5.0 (Linux; Android 10; K)" + CHROME + " Mobile Safari/537.36"),
        CHROME_DESKTOP(WINDOWS + ")" + CHROME + " Safari/537.36"),
        FIREFOX_MOBILE("Mozilla/5.0 (Android 10; Mobile; rv:" + FIREFOX
                + ") Gecko/" + FIREFOX + " Firefox/" + FIREFOX),
        FIREFOX_DESKTOP(WINDOWS + "; rv:" + FIREFOX + ") Gecko/20100101 Firefox/" + FIREFOX),
        YANDEX_MOBILE("Mozilla/5.0 (Linux; arm_64; Android 16; 2412DPC0AG)"
                + CHROMIUM + "152.0.7977.125 YaBrowser/26.10.2.125.00 Mobile Safari/537.36"),
        YANDEX_DESKTOP(WINDOWS + ")" + CHROMIUM + "150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36"),
        CUSTOM("");

        private final String mUserAgent;

        Preset(String userAgent) {
            mUserAgent = userAgent;
        }
    }

    private ArgonUserAgent() {}

    public static Preset getPreset() {
        String saved = ContextUtils.getAppSharedPreferences().getString(PREF_PRESET, "");
        for (Preset preset : Preset.values()) {
            if (preset.name().equals(saved)) {
                if (preset == Preset.CUSTOM && !isValidCustom(getCustom())) {
                    return Preset.CHROME_MOBILE;
                }
                return preset;
            }
        }
        return Preset.CHROME_MOBILE;
    }

    public static String getCustom() {
        String saved = ContextUtils.getAppSharedPreferences().getString(PREF_CUSTOM, "");
        return saved == null ? "" : saved;
    }

    public static boolean isValidCustom(String value) {
        if (value.length() > MAX_CUSTOM_LENGTH || value.trim().isEmpty()) return false;
        for (int i = 0; i < value.length(); ++i) {
            char c = value.charAt(i);
            if (c < ' ' || c > '~') return false;
        }
        return true;
    }

    public static void save(Preset preset, String custom) {
        SharedPreferences.Editor editor = ContextUtils.getAppSharedPreferences().edit();
        if (preset == Preset.CUSTOM) {
            if (!isValidCustom(custom)) return;
            editor.putString(PREF_CUSTOM, custom.trim());
        }
        editor.putString(PREF_PRESET, preset.name()).apply();
    }

    static void applySavedOverride() {
        Preset preset = getPreset();
        String userAgent = preset == Preset.CUSTOM ? getCustom() : preset.mUserAgent;
        if (!isValidCustom(userAgent)) userAgent = Preset.CHROME_MOBILE.mUserAgent;
        CommandLine.getInstance().appendSwitchWithValue("argon-user-agent", userAgent);
    }
}
