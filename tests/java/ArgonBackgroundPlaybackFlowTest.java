// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.media;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.widget.SeekBar;
import android.widget.Toast;

import androidx.appcompat.widget.SwitchCompat;
import androidx.preference.EditTextPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceScreen;
import androidx.preference.SeekBarPreference;

import org.chromium.base.ContextUtils;
import org.chromium.chrome.R;
import org.chromium.chrome.PlaybackTestResources;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.tabmodel.TabModelSelector;
import org.chromium.content_public.browser.MediaSession;
import org.chromium.content_public.browser.WebContents;
import org.json.JSONArray;

import java.util.ArrayList;
import java.util.List;

/** Exercises real editor -> JSON store -> preference notification -> live controller. */
public final class ArgonBackgroundPlaybackFlowTest {
    private static void check(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static ArgonBackgroundPlaybackStore fresh(boolean enabled) throws Exception {
        check(Handler.pendingCount() == 0, "Previous scenario leaked scheduled work");
        check(ContextUtils.context.preferences.listeners.isEmpty(), "Previous scenario leaked a preference listener");
        var field = ArgonBackgroundPlaybackStore.class.getDeclaredField("sInstance");
        field.setAccessible(true);
        field.set(null, null);
        ContextUtils.context.preferences.values.clear();
        if (enabled) ContextUtils.context.preferences.edit().putBoolean("enabled", true).apply();
        ContextUtils.context.power.interactive = true;
        ContextUtils.context.keyguard.locked = false;
        SystemClock.now = 0;
        return ArgonBackgroundPlaybackStore.get();
    }

    private static Preference row(PreferenceScreen screen, int title) {
        for (Preference preference : screen.rows) {
            if (preference.getTitleId() == title) return preference;
            if (preference instanceof PreferenceScreen group) {
                Preference found = row(group, title);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static void bind(PreferenceScreen screen) {
        for (Preference preference : screen.rows) {
            preference.bind();
            if (preference instanceof PreferenceScreen group) bind(group);
        }
    }

    private static ArgonBackgroundPlaybackSettings settings() {
        var screen = new ArgonBackgroundPlaybackSettings();
        screen.onCreatePreferences(null, null);
        screen.onResume();
        bind(screen.getPreferenceScreen());
        return screen;
    }

    private static ArgonBackgroundPlaybackSettings.RuleEditor open(String host) throws Exception {
        var screen = settings();
        Preference link = host.isEmpty()
                ? row(screen.getPreferenceScreen(), R.string.argon_background_add_site)
                : site(screen.getPreferenceScreen(), host);
        var editor = (ArgonBackgroundPlaybackSettings.RuleEditor)
                Class.forName(link.getFragment()).getConstructor().newInstance();
        editor.setArguments(link.getExtras());
        editor.onCreatePreferences(null, null);
        bind(editor.getPreferenceScreen());
        return editor;
    }

    private static Preference site(PreferenceScreen screen, String host) {
        for (Preference preference : screen.rows) {
            if (host.equals(preference.getExtras().getString("host", ""))) return preference;
            if (preference instanceof PreferenceScreen group) {
                Preference found = site(group, host);
                if (found != null) return found;
            }
        }
        return null;
    }

    private static void domain(ArgonBackgroundPlaybackSettings.RuleEditor editor, String value, boolean accept) {
        var host = (EditTextPreference) row(editor.getPreferenceScreen(), R.string.argon_background_domain);
        host.openDialog().setText(value);
        host.closeDialog(accept);
    }

    private static void minutes(ArgonBackgroundPlaybackSettings.RuleEditor editor, int value) {
        var limit = (SeekBarPreference) row(editor.getPreferenceScreen(), R.string.argon_background_time_limit);
        ((SeekBar) limit.boundHolder.findViewById(R.id.seekbar)).userProgress(value - limit.getMin());
        check(limit.getValue() == value, "Actual AndroidX seekbar did not accept the selected minute");
    }

    private static void save(ArgonBackgroundPlaybackSettings.RuleEditor editor) {
        row(editor.getPreferenceScreen(), R.string.argon_background_save).click();
    }

    private static void add(String host, int limit) throws Exception {
        var editor = open("");
        check(!row(editor.getPreferenceScreen(), R.string.argon_background_save).isEnabled(),
                "An empty editor must not offer to apply an invalid rule");
        domain(editor, host, true);
        minutes(editor, limit);
        save(editor);
        check(editor.requireActivity().backs == 1, "Successful Save must return to the site list");
    }

    private static String saved() {
        return ContextUtils.context.preferences.getString("rules", "[]");
    }

    private static void editorAndPersistence(boolean enabled) throws Exception {
        var store = fresh(enabled);
        check(store.policy.getRules().isEmpty(), "New installation must have no site rules");
        var editor = open("");
        domain(editor, "cancelled.example", false);
        check(((EditTextPreference) row(editor.getPreferenceScreen(), R.string.argon_background_domain))
                .getText().isEmpty(), "Cancel must not accept the domain");
        domain(editor, "  VIDEO.EXAMPLE.  ", true);
        minutes(editor, 120);
        check(store.policy.getRules().isEmpty() && saved().equals("[]"), "Input and slider changes must remain a draft");
        save(editor);
        check(store.policy.getRules().size() == 1, "Saving a new domain must persist exactly one rule");
        var json = new JSONArray(saved()).getJSONObject(0);
        check(json.getString("host").equals("video.example") && json.getInt("minutes") == 120
                && json.getBoolean("enabled") && json.getBoolean("subdomains"), "Saved JSON lost editor fields");
        check(store.policy.isEnabled() == enabled, "Saving must preserve the master switch");

        String before = saved();
        editor = open("video.example");
        domain(editor, "abandoned.example", true);
        minutes(editor, 1);
        editor.requireActivity().onBackPressed();
        check(saved().equals(before), "Back from the editor must discard the whole draft");
        editor = open("video.example");
        domain(editor, "renamed.example", true);
        minutes(editor, 1);
        row(editor.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        row(editor.getPreferenceScreen(), R.string.argon_background_subdomains).change(false);
        Bundle state = new Bundle();
        editor.onSaveInstanceState(state);
        var restored = new ArgonBackgroundPlaybackSettings.RuleEditor();
        restored.setArguments(editor.getArguments());
        restored.onCreatePreferences(state, null);
        bind(restored.getPreferenceScreen());
        check(((EditTextPreference) row(restored.getPreferenceScreen(), R.string.argon_background_domain))
                .openDialog().getText().toString().equals("renamed.example"), "Recreated editor lost its domain draft");
        ((EditTextPreference) row(restored.getPreferenceScreen(), R.string.argon_background_domain)).closeDialog(false);
        save(restored);
        store.reload();
        var rule = store.policy.getRules().get(0);
        check(store.policy.getRules().size() == 1 && rule.host.equals("renamed.example") && rule.minutes == 1
                && !rule.enabled && !rule.subdomains, "Restored editor did not replace the original rule atomically");
        check(store.policy.isEnabled() == enabled, "Edit must preserve the master mode");
        check(site(settings().getPreferenceScreen(), "renamed.example") != null,
                "Returning to settings must show the saved rule");

        before = saved();
        editor = open("renamed.example");
        row(editor.getPreferenceScreen(), R.string.argon_background_delete).click();
        check(saved().equals(before), "Opening delete confirmation must not delete the rule");
        check(AlertDialog.negative == null, "Cancel must have no delete action");
        AlertDialog.positive.accept(null, 0);
        check(store.policy.getRules().isEmpty() && new JSONArray(saved()).length() == 0,
                "Confirmed deletion must reach persistent storage");
        check(store.policy.isEnabled() == enabled, "Delete must preserve the master switch");
    }

    private static void validationAndCapacity(boolean enabled) throws Exception {
        var store = fresh(enabled);
        add("first.example", 1);
        add("second.example", 2);
        for (String value : new String[] {"", " ", "https://first.example", "first.example/path",
                "user@first.example", "first.example:443", "*.example", "localhost", "first.example?x",
                "first.example#x", "first%2eexample", "first\\example", "bad domain.example",
                ".first.example", "first..example", "first.example..", "127.0.0.1", "127.1",
                "[::1]", "co.uk", "github.io", "blogspot.com"}) {
            String before = saved();
            int writes = ContextUtils.context.preferences.writes;
            var editor = open("first.example");
            domain(editor, value, true);
            check(!row(editor.getPreferenceScreen(), R.string.argon_background_save).isEnabled(),
                    "Invalid input must disable Save before attempting to apply it: " + value);
            save(editor);
            check(Toast.lastMessage == R.string.argon_background_invalid_domain
                    && editor.requireActivity().backs == 0 && saved().equals(before)
                    && ContextUtils.context.preferences.writes == writes, "Invalid input changed the saved list: " + value);
        }
        for (String original : new String[] {"", "first.example"}) {
            String before = saved();
            var editor = open(original);
            domain(editor, "SECOND.EXAMPLE.", true);
            check(!row(editor.getPreferenceScreen(), R.string.argon_background_save).isEnabled(),
                    "A duplicate must disable Save before attempting to apply it");
            save(editor);
            check(Toast.lastMessage == R.string.argon_background_duplicate && saved().equals(before)
                    && editor.requireActivity().backs == 0, "Duplicate creation/rename must preserve both rules");
        }
        var full = new ArrayList<ArgonBackgroundPlaybackPolicy.Rule>();
        for (int i = 0; i < 100; i++) full.add(new ArgonBackgroundPlaybackPolicy.Rule("site" + i + ".example", 1, true, true));
        store.save(full);
        String before = saved();
        var editor = open("");
        domain(editor, "overflow.example", true);
        save(editor);
        check(Toast.lastMessage == R.string.argon_background_too_many && saved().equals(before),
                "The 101st site must be rejected without modifying storage");
        editor = open("site0.example");
        domain(editor, "replacement.example", true);
        save(editor);
        check(store.policy.getRules().size() == 100 && store.policy.getRules().stream()
                .anyMatch(r -> r.host.equals("replacement.example")), "A full list must still allow editing");
        check(store.policy.isEnabled() == enabled, "Validation and capacity checks must preserve master mode");
        for (int value : new int[] {-1, 0, 121, Integer.MAX_VALUE}) {
            Bundle malformed = new Bundle();
            malformed.putInt("minutes", value);
            var restored = new ArgonBackgroundPlaybackSettings.RuleEditor();
            Bundle arguments = new Bundle();
            arguments.putString("host", "replacement.example");
            restored.setArguments(arguments);
            restored.onCreatePreferences(malformed, null);
            bind(restored.getPreferenceScreen());
            check(((SeekBarPreference) row(restored.getPreferenceScreen(), R.string.argon_background_time_limit))
                    .getValue() == (value <= 0 ? 1 : 120), "Restored duration must be clamped before AndroidX binding");
            save(restored);
        }
    }

    private static void storeReload() throws Exception {
        var store = fresh(false);
        add("ПРИМЕР.РФ", 60);
        check(store.policy.getRules().get(0).host.equals("xn--e1afmkfd.xn--p1ai"), "Unicode input was not stored canonically");
        var field = ArgonBackgroundPlaybackStore.class.getDeclaredField("sInstance");
        field.setAccessible(true);
        field.set(null, null);
        store = ArgonBackgroundPlaybackStore.get();
        check(store.policy.getRules().size() == 1 && !store.policy.isEnabled(), "Recreating the store lost persisted settings");
        for (String malformed : new String[] {"not json", "[", "[{}]", "[null]", "[1]",
                "[{\"host\":\"valid.example\",\"minutes\":1,\"enabled\":true},{}]"}) {
            store.preferences.edit().putString("rules", malformed).apply();
            store.reload();
            check(store.policy.getRules().isEmpty(), "Malformed rule storage must fail closed: " + malformed);
        }
        store.preferences.edit().putString("rules", "[{\"host\":\"FIRST.EXAMPLE\",\"minutes\":1,\"enabled\":true},"
                + "{\"host\":\"first.example.\",\"minutes\":2,\"enabled\":true},"
                + "{\"host\":\"bad.example\",\"minutes\":121,\"enabled\":true},"
                + "{\"host\":\"legacy.example\",\"minutes\":1}]").apply();
        store.reload();
        check(store.policy.getRules().size() == 2 && !store.policy.getRules().get(0).subdomains
                && !store.policy.getRules().get(1).enabled, "Reload must deduplicate, reject invalid limits and default missing switches off");
        var oversized = new JSONArray();
        for (int i = 0; i < 110; i++) oversized.put(new org.json.JSONObject()
                .put("host", "site" + i + ".example").put("minutes", 1).put("enabled", true));
        store.preferences.edit().putString("rules", oversized.toString()).apply();
        store.reload();
        check(store.policy.getRules().size() == 100, "Reload must cap an oversized stored list at 100 sites");
    }

    private static void staleListSwitch(boolean enabled) throws Exception {
        var store = fresh(enabled);
        add("switch.example", 2);
        Preference staleRow = site(settings().getPreferenceScreen(), "switch.example");
        var editor = open("switch.example");
        minutes(editor, 90);
        row(editor.getPreferenceScreen(), R.string.argon_background_subdomains).change(false);
        save(editor);
        var toggle = (SwitchCompat) staleRow.boundHolder.findViewById(R.id.argon_background_rule_enabled);
        toggle.setChecked(false);
        var rule = store.policy.getRules().get(0);
        check(rule.minutes == 90 && !rule.subdomains && !rule.enabled && store.policy.isEnabled() == enabled,
                "A previously bound list switch must only change enabled, preserving newer duration and subdomain edits");
    }

    private static String text(int id, Object... args) {
        return PlaybackTestResources.getString(id, args);
    }

    private static void descriptionsAndOverlaps(boolean enabled) throws Exception {
        var store = fresh(enabled);
        var overview = settings();
        row(overview.getPreferenceScreen(), enabled ? R.string.argon_background_state_enabled
                : R.string.argon_background_state_disabled).click();
        check(AlertDialog.lastMessage.equals(text(R.string.argon_background_details_description)),
                "The overview block must explain the entire mode in the selected language");
        add("example.test", 60);
        add("video.example.test", 1);
        var child = open("video.example.test");
        row(child.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        row(child.getPreferenceScreen(), R.string.argon_background_subdomains).change(false);
        save(child);
        add("news.example.test", 30);
        var parentSummary = site(settings().getPreferenceScreen(), "example.test").getSummary().toString();
        check(parentSummary.contains(text(R.string.argon_background_scope_subdomains))
                && parentSummary.contains(text(R.string.argon_background_overlap_count, 2)),
                "The parent row must explain scope and both child intersections");
        var childSummary = site(settings().getPreferenceScreen(), "video.example.test").getSummary().toString();
        check(childSummary.contains(text(R.string.argon_background_scope_exact))
                && childSummary.contains(text(R.string.argon_background_rule_blocked))
                && childSummary.contains(text(R.string.argon_background_overlap_count, 1)),
                "The child row must explain its exact disabled exception and one parent intersection");
        check(childSummary.contains(text(R.string.argon_background_rule_inactive)) == !enabled,
                "Saved rule descriptions must show whether the master mode is inactive");
        child = open("video.example.test");
        row(child.getPreferenceScreen(), R.string.argon_background_rule_details).click();
        check(AlertDialog.lastMessage.contains("• example.test")
                && !AlertDialog.lastMessage.contains("• news.example.test")
                && AlertDialog.lastMessage.contains(text(R.string.argon_background_rule_priority)),
                "A child explanation must list its covering parent and priority, without unrelated siblings");
        check(!store.policy.isSiteEnabled("video.example.test")
                && store.policy.isSiteEnabled("www.video.example.test"),
                "The runtime result must match the described exact-host exception");
        var parent = open("example.test");
        row(parent.getPreferenceScreen(), R.string.argon_background_subdomains).change(false);
        row(parent.getPreferenceScreen(), R.string.argon_background_rule_details).click();
        check(AlertDialog.lastMessage.contains(text(R.string.argon_background_no_overlaps)),
                "The draft explanation must update when subdomain coverage is turned off");
        domain(parent, "fresh.example.test", true);
        row(parent.getPreferenceScreen(), R.string.argon_background_rule_details).click();
        check(AlertDialog.lastMessage.contains(text(R.string.argon_background_no_overlaps)),
                "A rename preview must exclude the old parent that Save will replace");
        check(store.policy.getRules().stream().anyMatch(r -> r.host.equals("example.test")),
                "Changing a preview must not mutate the stored parent rule");
        child = open("video.example.test");
        row(child.getPreferenceScreen(), R.string.argon_background_delete).click();
        check(AlertDialog.lastMessage.contains("• example.test")
                && AlertDialog.lastMessage.contains(text(R.string.argon_background_rule_allowed)),
                "Deleting a disabled exception must explain that the allowed parent will take over");
        AlertDialog.positive.accept(null, 0);
        check(store.policy.isSiteEnabled("video.example.test"), "The documented deletion fallback was not applied");
        add("section.example.test", 10);
        var middle = open("section.example.test");
        row(middle.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        save(middle);
        add("deep.section.example.test", 1);
        var deep = open("deep.section.example.test");
        row(deep.getPreferenceScreen(), R.string.argon_background_delete).click();
        check(AlertDialog.lastMessage.contains("• section.example.test")
                && !AlertDialog.lastMessage.contains("• example.test")
                && AlertDialog.lastMessage.contains(text(R.string.argon_background_rule_blocked)),
                "Deletion must explain the closest disabled fallback, rather than a broader allowed rule");
        var draft = open("");
        domain(draft, "tenant.github.io", true);
        check(row(draft.getPreferenceScreen(), R.string.argon_background_save).isEnabled(),
                "A specific tenant below a private registry must remain addable");
        domain(draft, "EXAMPLE.TEST。", true);
        check(!row(draft.getPreferenceScreen(), R.string.argon_background_save).isEnabled(),
                "Canonical duplicates must be rejected in the draft preview");
        row(overview.getPreferenceScreen(), R.string.argon_background_enable).change(!enabled);
        check(row(overview.getPreferenceScreen(), !enabled ? R.string.argon_background_state_enabled
                : R.string.argon_background_state_disabled) != null,
                "The overview status must update immediately with the master switch");
    }

    private static Tab playing(TabModelSelector selector, String url) {
        var contents = new WebContents();
        var session = new MediaSession();
        session.controllable = true;
        contents.session = session;
        var tab = new Tab(contents);
        tab.url = url;
        tab.interactable = false;
        selector.tabs.add(tab);
        return tab;
    }

    private static void play(Tab tab) {
        var session = tab.contents.session;
        session.suspended = false;
        for (var observer : List.copyOf(session.observers)) observer.mediaSessionStateChanged(true, false);
        Handler.runPending();
    }

    private static void screenEvent() {
        for (var receiver : List.copyOf(ContextUtils.context.receivers)) receiver.onReceive(ContextUtils.context, new Intent());
        Handler.runPending();
    }

    private static void liveApplication() throws Exception {
        var store = fresh(false);
        add("video.example", 1);
        add("private.video.example", 1);
        var privateRule = open("private.video.example");
        row(privateRule.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        save(privateRule);
        Activity activity = new Activity();
        TabModelSelector selector = new TabModelSelector();
        Tab main = playing(selector, "https://video.example/watch");
        main.interactable = true;
        ContextUtils.context.power.interactive = false;
        Tab child = playing(selector, "https://www.video.example/watch");
        Tab specific = playing(selector, "https://private.video.example/");
        Tab denied = playing(selector, "https://video.example.attacker.test/");
        Tab http = playing(selector, "http://video.example/");
        Tab privateTab = playing(selector, "https://video.example/");
        privateTab.incognito = true;
        ArgonBackgroundPlaybackController.attach(activity, selector);
        Handler.runPending();
        check(main.contents.pauses == 0 && denied.contents.pauses == 0, "Disabled mode must retain normal browser behavior");
        SystemClock.now = 1_000;
        row(settings().getPreferenceScreen(), R.string.argon_background_enable).change(true);
        check(store.policy.isEnabled() && main.contents.pauses == 0 && child.contents.pauses == 0,
                "Enabling the master switch must immediately apply stored site permissions");
        check(denied.contents.pauses == 1 && http.contents.pauses == 1 && specific.contents.pauses == 1
                && privateTab.contents.pauses == 0,
                "Domain boundaries, HTTPS, specific disabled rules and incognito exclusions were not enforced");
        check(main.contents.scripts.get(main.contents.scripts.size() - 1).contains("window[slot](true)"), "Allowed site did not enable the compatibility shim");
        SystemClock.now = 60_000;
        Handler.runPending();
        check(main.contents.pauses == 0 && child.contents.pauses == 0, "Playback stopped before the selected deadline");
        SystemClock.now = 61_000;
        Handler.runPending();
        check(main.contents.pauses == 1 && child.contents.pauses == 1, "Tabs/subdomains must share the exact deadline");
        play(main);
        check(main.contents.pauses == 2, "Play from a notification must not bypass an expired budget");
        ContextUtils.context.power.interactive = true;
        ContextUtils.context.keyguard.locked = true;
        activity.focused = true;
        main.interactable = true;
        screenEvent();
        play(main);
        check(main.contents.pauses == 3, "Waking a locked screen must not renew the budget");
        ContextUtils.context.keyguard.locked = false;
        activity.focused = false;
        screenEvent();
        play(main);
        check(main.contents.pauses == 4, "Unlocking into another app must not renew the budget");
        activity.focused = true;
        screenEvent();
        check(main.contents.pauses == 4, "Returning to a site must not automatically start playback");
        play(main);
        check(main.contents.pauses == 4, "An unlocked, focused site visit must renew the budget");
        activity.focused = false;
        main.interactable = false;
        screenEvent();
        var editor = open("video.example");
        row(editor.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        save(editor);
        check(main.contents.pauses == 5, "Disabling a saved rule must immediately pause background playback");
        check(main.contents.scripts.get(main.contents.scripts.size() - 1).contains("window[slot](false)"), "Disabling a rule must reverse its visibility override");
        int pauses = main.contents.pauses;
        row(settings().getPreferenceScreen(), R.string.argon_background_enable).change(false);
        check(main.contents.pauses == pauses + 1, "Turning the mode off must pause its background session once");
        pauses = main.contents.pauses;
        play(main);
        check(main.contents.pauses == pauses, "Disabled mode must stop intercepting future Play actions");
        ArgonBackgroundPlaybackController.detach(activity);
    }

    private static void editLiveBudget() throws Exception {
        var store = fresh(true);
        add("budget.example", 2);
        Activity activity = new Activity();
        activity.focused = false;
        TabModelSelector selector = new TabModelSelector();
        Tab tab = playing(selector, "https://budget.example/");
        ArgonBackgroundPlaybackController.attach(activity, selector);
        Handler.runPending();
        SystemClock.now = 30_000;
        Preference quickRule = site(settings().getPreferenceScreen(), "budget.example");
        var toggle = (SwitchCompat) quickRule.boundHolder.findViewById(R.id.argon_background_rule_enabled);
        check(toggle.isChecked(), "The list switch must show the stored rule state");
        toggle.setChecked(false);
        check(!store.policy.getRules().get(0).enabled && tab.contents.pauses == 1,
                "The list switch must persist and apply a disabled rule immediately");
        toggle.setChecked(true);
        play(tab);
        check(tab.contents.pauses == 1, "Re-enabling the list switch must retain an unexpired budget");
        SystemClock.now = 60_001;
        var editor = open("budget.example");
        minutes(editor, 1);
        save(editor);
        check(tab.contents.pauses == 2, "Shortening a live limit must use the original start time and stop immediately");
        editor = open("budget.example");
        row(editor.getPreferenceScreen(), R.string.argon_background_delete).click();
        AlertDialog.positive.accept(null, 0);
        add("budget.example", 1);
        play(tab);
        check(tab.contents.pauses == 3, "Deleting/re-adding a rule must not renew an expired live budget");
        check(store.policy.isEnabled(), "Live rule edits changed the master mode");
        ArgonBackgroundPlaybackController.detach(activity);
    }

    private static void canonicalDomain() throws Exception {
        fresh(true);
        check("video.example".equals(ArgonBackgroundPlaybackStore.normalizeHost("VIDEO.EXAMPLE。")),
                "Unicode and ASCII root dots must use the same stored domain");
        add("video.example", 1);
        Activity activity = new Activity();
        activity.focused = false;
        TabModelSelector selector = new TabModelSelector();
        Tab tab = playing(selector, "https://video.example./");
        ArgonBackgroundPlaybackController.attach(activity, selector);
        Handler.runPending();
        check(tab.contents.pauses == 0, "An HTTPS URL with a DNS root dot must match the saved canonical rule");
        ArgonBackgroundPlaybackController.detach(activity);
    }

    private static void multipleWindowsAndDeletion() throws Exception {
        fresh(true);
        add("windows.example", 1);
        Activity firstActivity = new Activity(), secondActivity = new Activity();
        firstActivity.focused = secondActivity.focused = false;
        TabModelSelector firstSelector = new TabModelSelector(), secondSelector = new TabModelSelector();
        Tab first = playing(firstSelector, "https://windows.example/");
        Tab second = playing(secondSelector, "https://www.windows.example/");
        ArgonBackgroundPlaybackController.attach(firstActivity, firstSelector);
        Handler.runPending();
        SystemClock.now = 20_000;
        ArgonBackgroundPlaybackController.attach(secondActivity, secondSelector);
        Handler.runPending();
        var editor = open("windows.example");
        row(editor.getPreferenceScreen(), R.string.argon_background_delete).click();
        AlertDialog.positive.accept(null, 0);
        check(first.contents.pauses == 1 && second.contents.pauses == 1,
                "Deleting a live rule must immediately pause its sessions in every window");
        add("windows.example", 1);
        check(first.contents.pauses == 1 && second.contents.pauses == 1,
                "Re-adding a rule must not start suspended players automatically");
        play(first);
        play(second);
        SystemClock.now = 60_000;
        Handler.runPending();
        check(first.contents.pauses == 2 && second.contents.pauses == 2,
                "Separate windows and delete/re-add must share the original site deadline");
        ArgonBackgroundPlaybackController.detach(firstActivity);
        play(second);
        check(second.contents.pauses == 3 && ContextUtils.context.preferences.listeners.size() == 1,
                "Closing one window must retain deadline enforcement in the other");
        ArgonBackgroundPlaybackController.detach(secondActivity);
    }

    private static void liveOverlapReassignment() throws Exception {
        fresh(true);
        add("overlap.test", 1);
        Activity activity = new Activity();
        activity.focused = false;
        TabModelSelector selector = new TabModelSelector();
        Tab tab = playing(selector, "https://video.overlap.test/");
        ArgonBackgroundPlaybackController.attach(activity, selector);
        Handler.runPending();
        SystemClock.now = 60_000;
        Handler.runPending();
        check(tab.contents.pauses == 1, "The original parent budget must expire");
        add("video.overlap.test", 1);
        play(tab);
        check(tab.contents.pauses == 2, "Adding a more specific rule must not give a live site a new minute");
        var child = open("video.overlap.test");
        row(child.getPreferenceScreen(), R.string.argon_background_delete).click();
        AlertDialog.positive.accept(null, 0);
        play(tab);
        check(tab.contents.pauses == 3, "Falling back to the parent must not restart the original minute");
        SystemClock.now = 61_000;
        activity.focused = true;
        tab.interactable = true;
        selector.observer.onInteractabilityChanged(tab, true);
        add("video.overlap.test", 1);
        activity.focused = false;
        tab.interactable = false;
        selector.observer.onInteractabilityChanged(tab, false);
        play(tab);
        check(tab.contents.pauses == 3, "A genuine site visit must reset both current and removed covering budgets");
        SystemClock.now = 121_000;
        Handler.runPending();
        check(tab.contents.pauses == 4, "The renewed site budget must still enforce its deadline");
        ArgonBackgroundPlaybackController.detach(activity);
    }

    public static void main(String[] args) throws Exception {
        try {
            new androidx.preference.SeekBarPreference(new android.content.Context()).bind();
            throw new AssertionError("The generic theme fixture must reproduce the missing seekbar value");
        } catch (NullPointerException expected) {
            check(expected.getStackTrace()[0].getClassName().equals("androidx.preference.SeekBarPreference"),
                    "The negative control must fail inside the real AndroidX seekbar");
        }
        try {
            new EditTextPreference(new android.content.Context()).openDialog();
            throw new AssertionError("The generic theme fixture must reproduce the missing dialog EditText");
        } catch (IllegalStateException expected) {
            check(expected.getMessage().contains("@android:id/edit")
                    && expected.getStackTrace()[0].getClassName().equals("androidx.preference.EditTextPreferenceDialogFragmentCompat"),
                    "The negative control must reproduce the log exception inside the real AndroidX dialog");
        }
        for (boolean russian : new boolean[] {false, true}) {
            PlaybackTestResources.russian = russian;
            for (boolean enabled : new boolean[] {false, true}) {
                editorAndPersistence(enabled);
                validationAndCapacity(enabled);
                staleListSwitch(enabled);
                descriptionsAndOverlaps(enabled);
            }
        }
        System.out.println("PASS: real AndroidX input, cancel, slider, restore, save/delete, validation, scope descriptions and overlaps in both modes and languages");
        storeReload();
        System.out.println("PASS: production JSON storage, restart, canonical domains and corrupt data");
        liveApplication();
        editLiveBudget();
        multipleWindowsAndDeletion();
        liveOverlapReassignment();
        System.out.println("PASS: saved rules reach live media sessions, exact deadlines, screen locks, rule edits and master switches");
        canonicalDomain();
        System.out.println("PASS: canonical domains match between input, persistent rules and page URLs");
        check(Handler.pendingCount() == 0 && ContextUtils.context.receivers.isEmpty()
                && ContextUtils.context.preferences.listeners.isEmpty(), "Flow checks left active controller work");
    }
}
