// Copyright 2026 Argon contributors
// Use of this source code is governed by a GPL-2.0-only style license that can be
// found in the LICENSE file.

package org.chromium.chrome.browser.privacy.settings;

import android.content.Context;
import android.text.InputType;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.Toast;

import androidx.appcompat.app.AlertDialog;
import androidx.lifecycle.Lifecycle;
import androidx.lifecycle.LifecycleEventObserver;
import androidx.preference.Preference;
import androidx.preference.PreferenceFragmentCompat;

import org.chromium.build.annotations.NullMarked;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.base.ArgonUserAgent;
import org.chromium.chrome.browser.base.ArgonUserAgent.Preset;
import org.chromium.chrome.browser.lifetime.ApplicationLifetime;

@NullMarked
final class ArgonUserAgentPreference {
    private ArgonUserAgentPreference() {}

    static void bind(PreferenceFragmentCompat fragment) {
        Preference preference = fragment.findPreference("argon_user_agent");
        if (preference == null) return;

        Preference restart = fragment.findPreference("argon_user_agent_restart");
        if (restart == null) return;
        updateSummary(preference, restart);
        fragment.getLifecycle().addObserver((LifecycleEventObserver) (source, event) -> {
            if (event == Lifecycle.Event.ON_RESUME) updateSummary(preference, restart);
        });
        restart.setOnPreferenceClickListener(pref -> {
            showRestart(preference, restart);
            return true;
        });
        preference.setOnPreferenceClickListener(
                pref -> {
                    showPresets(pref, restart);
                    return true;
                });
    }

    private static int label(Preset preset) {
        return switch (preset) {
            case DEFAULT -> R.string.argon_user_agent_default;
            case FIREFOX -> R.string.argon_user_agent_firefox;
            case YANDEX -> R.string.argon_user_agent_yandex;
            case CUSTOM -> R.string.argon_user_agent_custom;
        };
    }

    private static void updateSummary(Preference preference, Preference restart) {
        Context context = preference.getContext();
        String selected = context.getString(label(ArgonUserAgent.getPreset()));
        String active = context.getString(label(ArgonUserAgent.getActivePreset()));
        boolean pending = ArgonUserAgent.hasPendingChange();
        if (pending && ArgonUserAgent.getActivePreset() == Preset.CUSTOM
                && ArgonUserAgent.getPreset() == Preset.CUSTOM) {
            preference.setSummary(context.getString(R.string.argon_user_agent_custom_pending));
        } else {
            preference.setSummary(pending
                    ? context.getString(R.string.argon_user_agent_pending, active, selected)
                    : context.getString(R.string.argon_user_agent_summary, active));
        }
        restart.setVisible(pending);
    }

    private static void saved(Preference preference, Preference restart) {
        updateSummary(preference, restart);
        if (ArgonUserAgent.hasPendingChange()) showRestart(preference, restart);
    }

    private static void showRestart(Preference preference, Preference restart) {
        if (!ArgonUserAgent.hasPendingChange()) return;
        Context context = preference.getContext();
        new AlertDialog.Builder(context)
                .setTitle(R.string.argon_user_agent_restart)
                .setMessage(R.string.argon_user_agent_restart_message)
                .setPositiveButton(R.string.argon_user_agent_restart_now, (dialog, which) -> {
                    if (!ArgonUserAgent.hasPendingChange()) return;
                    if (ArgonUserAgent.flushForRestart()) {
                        ApplicationLifetime.terminate(true);
                    } else {
                        Toast.makeText(context, R.string.argon_user_agent_save_failed,
                                Toast.LENGTH_LONG).show();
                    }
                })
                .setNegativeButton(R.string.argon_user_agent_later, null)
                .setNeutralButton(R.string.argon_user_agent_undo, (dialog, which) -> {
                    ArgonUserAgent.discardPendingChange();
                    updateSummary(preference, restart);
                })
                .show();
    }

    private static void showPresets(Preference preference, Preference restart) {
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
                                editCustom(preference, restart);
                            } else {
                                ArgonUserAgent.save(preset, "");
                                saved(preference, restart);
                            }
                        })
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    private static void editCustom(Preference preference, Preference restart) {
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
                            dialog.dismiss();
                            saved(preference, restart);
                        });
    }
}
