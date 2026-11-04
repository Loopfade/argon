// Copyright 2026 Argon contributors
// Use of this source code is governed by a GPL-2.0-only style license that can be
// found in the LICENSE file.

package org.chromium.chrome.browser.privacy.settings;

import android.content.Context;
import android.text.InputType;
import android.widget.EditText;
import android.widget.FrameLayout;

import androidx.appcompat.app.AlertDialog;
import androidx.preference.Preference;
import androidx.preference.PreferenceFragmentCompat;

import org.chromium.build.annotations.NullMarked;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.base.ArgonUserAgent;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;

@NullMarked
final class ArgonUserAgentPreference {
    private ArgonUserAgentPreference() {}

    static void bind(PreferenceFragmentCompat fragment) {
        Preference preference = fragment.findPreference("argon_user_agent");
        if (preference == null) return;

        updateSummary(preference);
        preference.setOnPreferenceClickListener(
                pref -> {
                    showPresets(pref);
                    return true;
                });
    }

    private static int label(Preset preset) {
        return switch (preset) {
            case DEFAULT -> R.string.argon_user_agent_default;
            case CHROME_MOBILE -> R.string.argon_user_agent_chrome_mobile;
            case CHROME_DESKTOP -> R.string.argon_user_agent_chrome_desktop;
            case FIREFOX_MOBILE -> R.string.argon_user_agent_firefox_mobile;
            case FIREFOX_DESKTOP -> R.string.argon_user_agent_firefox_desktop;
            case YANDEX_MOBILE -> R.string.argon_user_agent_yandex_mobile;
            case YANDEX_DESKTOP -> R.string.argon_user_agent_yandex_desktop;
            case CUSTOM -> R.string.argon_user_agent_custom;
        };
    }

    private static void updateSummary(Preference preference) {
        Context context = preference.getContext();
        String selected = context.getString(label(ArgonUserAgent.getPreset()));
        preference.setSummary(context.getString(R.string.argon_user_agent_summary, selected));
    }

    private static void showPresets(Preference preference) {
        Context context = preference.getContext();
        Preset[] presets = Preset.values();
        CharSequence[] choices = new CharSequence[presets.length];
        for (int i = 0; i < presets.length; ++i) {
            choices[i] = context.getString(label(presets[i]));
        }
        new AlertDialog.Builder(context)
                .setTitle(R.string.argon_user_agent_title)
                .setSingleChoiceItems(
                        choices,
                        ArgonUserAgent.getPreset().ordinal(),
                        (dialog, which) -> {
                            dialog.dismiss();
                            Preset preset = presets[which];
                            if (preset == Preset.CUSTOM) {
                                editCustom(preference);
                            } else {
                                ArgonUserAgent.save(preset, "");
                                updateSummary(preference);
                            }
                        })
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    private static void editCustom(Preference preference) {
        Context context = preference.getContext();
        EditText input = new EditText(context);
        input.setSingleLine();
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        input.setHint(R.string.argon_user_agent_title);
        input.setText(ArgonUserAgent.getCustom());
        input.setSelection(input.length());

        FrameLayout container = new FrameLayout(context);
        int margin = Math.round(24 * context.getResources().getDisplayMetrics().density);
        container.setPadding(margin, 0, margin, 0);
        container.addView(input);
        AlertDialog dialog =
                new AlertDialog.Builder(context)
                        .setTitle(R.string.argon_user_agent_custom)
                        .setView(container)
                        .setPositiveButton(android.R.string.ok, null)
                        .setNegativeButton(android.R.string.cancel, null)
                        .create();
        dialog.show();
        dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                        view -> {
                            String value = input.getText().toString();
                            if (!ArgonUserAgent.isValidCustom(value)) {
                                input.setError(context.getString(R.string.argon_user_agent_invalid));
                                return;
                            }
                            ArgonUserAgent.save(Preset.CUSTOM, value);
                            updateSummary(preference);
                            dialog.dismiss();
                        });
    }
}
