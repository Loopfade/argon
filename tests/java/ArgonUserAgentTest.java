package org.chromium.chrome.browser.base;

import java.util.HashSet;

import org.chromium.base.CommandLine;
import org.chromium.base.ContextUtils;
import org.chromium.base.version_info.VersionConstants;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;

public class ArgonUserAgentTest {
    private static String startBrowser() {
        CommandLine.getInstance().values.clear();
        ArgonUserAgent.applySavedOverride();
        assert CommandLine.getInstance().values.get("argon-user-agent")
                .equals(ArgonUserAgent.getUserAgent(false));
        assert CommandLine.getInstance().values.get("argon-user-agent-desktop")
                .equals(ArgonUserAgent.getUserAgent(true));
        return ArgonUserAgent.getUserAgent(Boolean.TRUE.equals(ArgonUserAgent.requestedDesktopMode()));
    }

    public static void main(String[] args) {
        String mobileChrome = startBrowser();
        assert ArgonUserAgent.getPreset() == Preset.CHROME_MOBILE;
        assert mobileChrome.contains(
                "Chrome/" + VersionConstants.PRODUCT_MAJOR_VERSION + ".0.0.0 Mobile Safari/537.36");
        assert Preset.values().length == 7;

        for (String legacy : new String[] {"DEFAULT", "", "removed-preset"}) {
            ContextUtils.getAppSharedPreferences().edit()
                    .putString("argon_user_agent.preset", legacy).apply();
            assert ArgonUserAgent.getPreset() == Preset.CHROME_MOBILE;
            assert mobileChrome.equals(startBrowser());
        }

        HashSet<String> values = new HashSet<>();
        for (Preset preset : Preset.values()) {
            if (preset == Preset.CUSTOM) continue;
            var startupArguments = new java.util.HashMap<>(CommandLine.getInstance().values);
            ArgonUserAgent.save(preset, "");
            assert ArgonUserAgent.getPreset() == preset;
            assert CommandLine.getInstance().values.equals(startupArguments);
            assert values.add(startBrowser());
        }
        assert values.size() == 6;

        ArgonUserAgent.save(Preset.YANDEX_DESKTOP, "");
        assert ArgonUserAgent.requestedDesktopMode();
        assert ArgonUserAgent.needsModeSync();
        assert ArgonUserAgent.getUserAgent(false).contains("YaBrowser/");
        assert ArgonUserAgent.getUserAgent(false).contains("Mobile");
        assert !ArgonUserAgent.getUserAgent(true).contains("Mobile");
        ArgonUserAgent.markModeSynced();
        assert !ArgonUserAgent.needsModeSync();
        ArgonUserAgent.save(Preset.YANDEX_DESKTOP, "");
        assert ArgonUserAgent.needsModeSync();

        ArgonUserAgent.save(Preset.CUSTOM, "  MyBrowser/1.0 Mobile  ");
        assert startBrowser().equals("MyBrowser/1.0 Mobile");
        ArgonUserAgent.save(Preset.CHROME_MOBILE, "");
        assert startBrowser().equals(mobileChrome);
        assert ArgonUserAgent.getCustom().equals("MyBrowser/1.0 Mobile");

        for (String invalid : new String[] {"", "   ", "Bad\r\nHeader", "x".repeat(1025)}) {
            ArgonUserAgent.save(Preset.CUSTOM, invalid);
            assert startBrowser().equals(mobileChrome);
            assert ArgonUserAgent.getPreset() == Preset.CHROME_MOBILE;
        }

        ContextUtils.getAppSharedPreferences().edit()
                .putString("argon_user_agent.preset", "CUSTOM")
                .putString("argon_user_agent.custom", "").apply();
        assert ArgonUserAgent.getPreset() == Preset.CHROME_MOBILE;
        assert mobileChrome.equals(startBrowser());

        ContextUtils.getAppSharedPreferences().edit()
                .putString("argon_user_agent.custom", "RestoredBrowser/2.0").apply();
        assert ArgonUserAgent.getPreset() == Preset.CUSTOM;
        assert startBrowser().equals("RestoredBrowser/2.0");

        System.out.println("PASS: mobile Chrome default, legacy migration and saved choices");
    }
}
