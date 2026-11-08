package org.chromium.chrome.browser.base;

import org.chromium.base.ContextUtils;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;
import org.chromium.chrome.browser.content.ContentUtilsJni;
import org.chromium.chrome.browser.profiles.Profile;
import org.chromium.chrome.browser.tab.Tab;

public class ArgonUserAgentRuntimeTest {
    private static void select(Preset preset) {
        ArgonUserAgent.save(preset, "");
        ContentUtilsJni.acknowledge();
    }

    private static void expect(Tab tab, boolean desktop, String browser) {
        assert tab.contents.navigation.desktop == desktop;
        String ua = tab.contents.requestUserAgent();
        assert ua.contains(browser) : ua;
        assert ua.contains("Mobile") != desktop : ua;
    }

    public static void main(String[] args) {
        // A desktop preset saved by the old implementation is migrated once.
        ContextUtils.getAppSharedPreferences().edit()
                .putString("argon_user_agent.preset", "YANDEX_DESKTOP").apply();
        ArgonUserAgent.applySavedOverride();
        Profile normal = new Profile(false);
        Tab current = new Tab(normal, "https://4pda.to/forum/");
        current.initialize();
        expect(current, true, "YaBrowser/");
        assert normal.desktop && !ArgonUserAgent.needsModeSync();
        Profile privateProfile = new Profile(true);
        Tab privateTab = new Tab(privateProfile, "https://example.org/");
        privateTab.initialize();
        Tab hidden = new Tab(normal, "https://example.com/");
        hidden.hidden = true;
        hidden.initialize();

        int reloads = current.reloads;
        ArgonUserAgent.save(Preset.YANDEX_MOBILE, "");
        assert current.reloads == reloads;  // Wait for renderer acknowledgements.
        ContentUtilsJni.acknowledge();
        expect(current, false, "YaBrowser/");
        expect(privateTab, false, "YaBrowser/");
        assert current.reloads == reloads + 1 && hidden.reloads == 0;
        assert !normal.desktop && !privateProfile.desktop;
        hidden.show();
        assert hidden.reloads == 1;
        hidden.show();
        assert hidden.reloads == 1;

        select(Preset.YANDEX_DESKTOP);
        expect(current, true, "YaBrowser/");
        assert normal.desktop && privateProfile.desktop;
        // Menu changes the site mode while retaining the selected browser family.
        current.checkbox(false);
        expect(current, false, "YaBrowser/");
        assert ArgonUserAgent.getPreset() == Preset.YANDEX_DESKTOP;
        select(Preset.YANDEX_DESKTOP);
        expect(current, true, "YaBrowser/");
        current.checkbox(true);
        expect(current, true, "YaBrowser/");

        select(Preset.FIREFOX_DESKTOP);
        expect(current, true, "Firefox/");
        current.checkbox(false);
        expect(current, false, "Firefox/");
        select(Preset.CHROME_MOBILE);
        expect(current, false, "Chrome/");
        current.checkbox(true);
        expect(current, true, "Chrome/");

        // A superseded renderer reply must not reload tabs using an older choice.
        reloads = current.reloads;
        ArgonUserAgent.save(Preset.FIREFOX_DESKTOP, "");
        ArgonUserAgent.save(Preset.YANDEX_MOBILE, "");
        ContentUtilsJni.acknowledge();
        assert current.reloads == reloads;
        ContentUtilsJni.acknowledge();
        expect(current, false, "YaBrowser/");
        assert current.reloads == reloads + 1;

        // Preserve a custom string verbatim and keep its existing desktop-site mode.
        current.checkbox(true);
        ArgonUserAgent.save(Preset.CUSTOM, "  MyCustom/1.0  ");
        ContentUtilsJni.acknowledge();
        assert current.contents.navigation.desktop;
        assert current.contents.requestUserAgent().equals("MyCustom/1.0");
        current.checkbox(false);
        assert current.contents.requestUserAgent().equals("MyCustom/1.0");
        int pending = ContentUtilsJni.replies.size();
        ArgonUserAgent.save(Preset.CUSTOM, "Bad\r\nHeader");
        assert ContentUtilsJni.replies.size() == pending;

        // Closed tabs are not touched by delayed callbacks; dormant tabs are not thawed.
        Tab dormant = new Tab(normal, "https://dormant.example/");
        dormant.initialize();
        dormant.contents = null;
        int closedReloads = privateTab.reloads;
        ArgonUserAgent.save(Preset.YANDEX_DESKTOP, "");
        privateTab.destroy();
        ContentUtilsJni.acknowledge();
        assert privateTab.reloads == closedReloads;
        assert dormant.contents == null;
        expect(current, true, "YaBrowser/");

        // Later launches keep manually edited desktop-site defaults after migration.
        normal.desktop = false;
        normal.sites.clear();
        Tab restored = new Tab(normal, "https://restored.example/");
        restored.initialize();
        expect(restored, false, "YaBrowser/");
        assert !normal.desktop;

        // Privacy settings may be opened directly while no tabs exist.
        current.destroy();
        hidden.destroy();
        dormant.destroy();
        restored.destroy();
        ArgonUserAgentController.initialize();
        select(Preset.FIREFOX_DESKTOP);
        assert ArgonUserAgent.needsModeSync();
        Tab firstTab = new Tab(new Profile(false), "https://first.example/");
        firstTab.initialize();
        expect(firstTab, true, "Firefox/");
        assert !ArgonUserAgent.needsModeSync();
        System.out.println("PASS: live User-Agent, desktop checkbox, renderer replies and tab lifecycle");
    }
}
