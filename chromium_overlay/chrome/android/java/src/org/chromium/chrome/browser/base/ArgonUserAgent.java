// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.base;

import org.chromium.base.CommandLine;
import org.chromium.base.ContextUtils;
import org.chromium.base.StrictModeContext;
import org.chromium.build.annotations.NullMarked;

/** A saved selection and a separate, immutable selection for this browser process. */
@NullMarked
public final class ArgonUserAgent {
    private static final String PREF_PRESET = "argon_user_agent.preset";
    private static final String PREF_CUSTOM = "argon_user_agent.custom";
    private static final String WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64";
    private static final String CHROMIUM = " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/";
    private static final String FIREFOX_VERSION = "154.0";
    private static final int MAX_CUSTOM_LENGTH = 1024;
    private static boolean sInitialized;
    private static Preset sActivePreset = Preset.DEFAULT;
    private static String sActiveCustom = "";

    public enum Preset { DEFAULT, FIREFOX, YANDEX, CUSTOM }

    private ArgonUserAgent() {}

    public static Preset getPreset() {
        String saved = ContextUtils.getAppSharedPreferences().getString(PREF_PRESET, "");
        if (saved == null) return Preset.DEFAULT;
        // Old mobile/desktop selections migrate to a family. Site layout preferences
        // already belong to Chromium and must never be rewritten by UA selection.
        return switch (saved) {
            case "FIREFOX", "FIREFOX_MOBILE", "FIREFOX_DESKTOP" -> Preset.FIREFOX;
            case "YANDEX", "YANDEX_MOBILE", "YANDEX_DESKTOP" -> Preset.YANDEX;
            case "CUSTOM" -> isValidCustom(getCustom()) ? Preset.CUSTOM : Preset.DEFAULT;
            default -> Preset.DEFAULT;
        };
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
        var editor = ContextUtils.getAppSharedPreferences().edit();
        if (preset == Preset.CUSTOM) {
            if (!isValidCustom(custom)) return;
            editor.putString(PREF_CUSTOM, custom.trim());
        }
        editor.putString(PREF_PRESET, preset.name()).apply();
    }

    public static Preset getActivePreset() { return sActivePreset; }

    public static boolean hasPendingChange() {
        return getPreset() != sActivePreset
                || (sActivePreset == Preset.CUSTOM && !getCustom().trim().equals(sActiveCustom));
    }

    public static void discardPendingChange() {
        save(sActivePreset, sActiveCustom);
    }

    /** Finish pending SharedPreferences writes before Chromium terminates the process. */
    public static boolean flushForRestart() {
        try (StrictModeContext ignored = StrictModeContext.allowDiskWrites()) {
            return ContextUtils.getAppSharedPreferences().edit()
                    .putString(PREF_PRESET, getPreset().name()).commit();
        }
    }

    /** Empty means use Chromium's unmodified UA and Client Hints. */
    public static String getUserAgent(boolean desktop) {
        return switch (sActivePreset) {
            case DEFAULT -> "";
            case CUSTOM -> sActiveCustom;
            case FIREFOX -> desktop
                    ? WINDOWS + "; rv:" + FIREFOX_VERSION + ") Gecko/20100101 Firefox/" + FIREFOX_VERSION
                    : "Mozilla/5.0 (Android 10; Mobile; rv:" + FIREFOX_VERSION
                            + ") Gecko/" + FIREFOX_VERSION + " Firefox/" + FIREFOX_VERSION;
            case YANDEX -> desktop
                    ? WINDOWS + ")" + CHROMIUM + "150.0.0.0 YaBrowser/26.8.0.0 Safari/537.36"
                    : "Mozilla/5.0 (Linux; arm_64; Android 16; 2412DPC0AG)"
                            + CHROMIUM + "152.0.7977.125 YaBrowser/26.10.2.125.00 Mobile Safari/537.36";
        };
    }

    static void applySavedOverride() {
        if (sInitialized) return;
        sActivePreset = getPreset();
        sActiveCustom = getCustom().trim();
        sInitialized = true;
        if (sActivePreset == Preset.DEFAULT) return;
        CommandLine commandLine = CommandLine.getInstance();
        commandLine.appendSwitchWithValue("argon-user-agent", getUserAgent(false));
        commandLine.appendSwitchWithValue("argon-user-agent-desktop", getUserAgent(true));
        commandLine.appendSwitch("argon-user-agent-hide-client-hints");
    }
}
