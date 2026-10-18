// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.media;

import android.os.Bundle;
import android.text.InputType;
import android.widget.EditText;

import androidx.preference.EditTextPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceScreen;
import androidx.preference.SeekBarPreference;

import org.chromium.chrome.R;

/** Host regression for the real editor's add/edit paths and AndroidX row contract. */
public final class ArgonBackgroundPlaybackSettingsTest {
    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static Preference row(PreferenceScreen screen, int title) {
        for (Preference preference : screen.rows) {
            if (preference.getTitleId() == title) return preference;
            if (preference instanceof PreferenceScreen) {
                Preference nested = row((PreferenceScreen) preference, title);
                if (nested != null) return nested;
            }
        }
        return null;
    }

    private static void bind(PreferenceScreen screen) {
        for (Preference preference : screen.rows) preference.bind();
    }

    public static void main(String[] args) throws Exception {
        // Prove that the adapter reproduces the reported AndroidX failure.
        try {
            new SeekBarPreference(new android.content.Context()).bind();
            throw new AssertionError("An unstyled SeekBarPreference must reject the generic row");
        } catch (NullPointerException expected) {
            // Chromium's generic preference row has no seekbar_value.
        }
        try {
            new EditTextPreference(new android.content.Context()).openDialog();
            throw new AssertionError("An unstyled domain dialog must reject the missing EditText");
        } catch (IllegalStateException expected) {
            check(expected.getMessage().contains("@android:id/edit"), "Reproduce the reported dialog error");
        }
        for (boolean mode : new boolean[] {false, true}) exerciseEditor(mode);
        System.out.println("PASS: rule editor binding and saving");
    }

    private static void exerciseEditor(boolean mode) throws Exception {
        var store = ArgonBackgroundPlaybackStore.get();
        store.policy.configure(mode, java.util.List.of());
        var settings = new ArgonBackgroundPlaybackSettings();
        settings.onCreatePreferences(null, null);
        Preference add = row(settings.getPreferenceScreen(), R.string.argon_background_add_site);
        var editor = (ArgonBackgroundPlaybackSettings.RuleEditor)
                Class.forName(add.getFragment()).getDeclaredConstructor().newInstance();
        editor.onCreatePreferences(null, null);
        bind(editor.getPreferenceScreen());
        SeekBarPreference limit = (SeekBarPreference)
                row(editor.getPreferenceScreen(), R.string.argon_background_time_limit);
        check(limit.getMin() == 1 && limit.getMax() == 120 && limit.getValue() == 60,
                "The new site must retain its default and range in minutes");
        EditTextPreference host = (EditTextPreference)
                row(editor.getPreferenceScreen(), R.string.argon_background_domain);
        EditText input = host.openDialog();
        check(input.getInputType() == (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS)
                && input.isSingleLine(), "The domain dialog must apply its input settings");
        input.setText("discarded.example");
        host.closeDialog(false);
        check(host.getText().isEmpty() && store.policy.getRules().isEmpty(),
                "Cancelling the domain dialog must leave the draft and store unchanged");
        host.openDialog().setText("video.example");
        host.closeDialog(true);
        check(host.getText().equals("video.example") && store.policy.getRules().isEmpty(),
                "Confirming the domain must update only the draft until Save");
        limit.change(120);
        row(editor.getPreferenceScreen(), R.string.argon_background_save).click();
        var rule = store.policy.getRules().get(0);
        check(rule.host.equals("video.example") && rule.minutes == 120 && rule.enabled,
                "Adding a site must save the chosen limit");
        check(store.policy.isEnabled() == mode, "Saving a site must preserve the master mode");

        // Exercise the same screen via the saved site's own fragment/extras.
        settings.onResume();
        Preference site = row(settings.getPreferenceScreen(), R.string.argon_background_sites);
        Preference existing = ((PreferenceScreen) site).rows.stream()
                .filter(p -> "video.example".equals(p.getExtras().getString("host", "")))
                .findFirst().orElseThrow();
        editor = (ArgonBackgroundPlaybackSettings.RuleEditor)
                Class.forName(existing.getFragment()).getDeclaredConstructor().newInstance();
        editor.setArguments(existing.getExtras());
        editor.onCreatePreferences(null, null);
        bind(editor.getPreferenceScreen());
        limit = (SeekBarPreference) row(editor.getPreferenceScreen(), R.string.argon_background_time_limit);
        check(limit.getValue() == 120, "Editing must show the saved limit");
        host = (EditTextPreference) row(editor.getPreferenceScreen(), R.string.argon_background_domain);
        check(host.openDialog().getText().toString().equals("video.example"),
                "Editing must prefill the stored domain");
        host.closeDialog(false);
        host.openDialog().setText("renamed.example");
        host.closeDialog(true);
        limit.change(1);
        row(editor.getPreferenceScreen(), R.string.argon_background_allow_site).change(false);
        row(editor.getPreferenceScreen(), R.string.argon_background_subdomains).change(false);
        Bundle saved = new Bundle();
        editor.onSaveInstanceState(saved);
        var restored = new ArgonBackgroundPlaybackSettings.RuleEditor();
        restored.setArguments(existing.getExtras());
        restored.onCreatePreferences(saved, null);
        bind(restored.getPreferenceScreen());
        host = (EditTextPreference) row(restored.getPreferenceScreen(), R.string.argon_background_domain);
        check(host.openDialog().getText().toString().equals("renamed.example"),
                "Restoring an editor must preserve the confirmed domain draft");
        host.closeDialog(false);
        row(restored.getPreferenceScreen(), R.string.argon_background_save).click();
        check(store.policy.getRules().size() == 1, "Editing must replace the original rule");
        rule = store.policy.getRules().get(0);
        check(rule.host.equals("renamed.example") && rule.minutes == 1 && !rule.enabled && !rule.subdomains,
                "Restored edits must save the limit and switches together");
        check(store.policy.isEnabled() == mode, "Editing a site must preserve the master mode");
    }
}
