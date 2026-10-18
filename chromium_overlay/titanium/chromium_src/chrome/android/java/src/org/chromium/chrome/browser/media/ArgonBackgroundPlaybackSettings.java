// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.media;

import android.app.AlertDialog;
import android.os.Bundle;
import android.text.InputType;
import android.widget.Toast;

import androidx.appcompat.widget.SwitchCompat;
import androidx.preference.EditTextPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceCategory;
import androidx.preference.PreferenceScreen;
import androidx.preference.PreferenceViewHolder;
import androidx.preference.SeekBarPreference;
import androidx.preference.SwitchPreferenceCompat;

import org.chromium.base.supplier.MonotonicObservableSupplier;
import org.chromium.base.supplier.ObservableSuppliers;
import org.chromium.base.supplier.SettableMonotonicObservableSupplier;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.settings.ChromeBaseSettingsFragment;
import org.chromium.components.browser_ui.settings.SettingsFragment;
import org.chromium.components.url_formatter.SchemeDisplay;
import org.chromium.components.url_formatter.UrlFormatter;
import org.chromium.url.GURL;

import java.util.List;

/** Settings > Background playback. There are no preconfigured sites. */
@NullMarked
public final class ArgonBackgroundPlaybackSettings extends ChromeBaseSettingsFragment {
    private final SettableMonotonicObservableSupplier<String> mTitle = ObservableSuppliers.createMonotonic();

    @Override
    public void onCreatePreferences(@Nullable Bundle saved, @Nullable String rootKey) {
        mTitle.set(getString(R.string.argon_background_title));
        rebuild();
    }

    @Override
    public void onResume() {
        super.onResume();
        ArgonBackgroundPlaybackStore.get().reload();
        rebuild();
    }

    private void rebuild() {
        var store = ArgonBackgroundPlaybackStore.get();
        PreferenceScreen screen = getPreferenceManager().createPreferenceScreen(requireContext());
        setPreferenceScreen(screen);
        Preference explanation = new Preference(requireContext());
        explanation.setSummary(R.string.argon_background_description);
        explanation.setSelectable(false);
        screen.addPreference(explanation);

        SwitchPreferenceCompat master = new SwitchPreferenceCompat(requireContext());
        master.setPersistent(false);
        master.setTitle(R.string.argon_background_enable);
        master.setSummary(R.string.argon_background_master_description);
        master.setChecked(store.policy.isEnabled());
        master.setOnPreferenceChangeListener((preference, value) -> {
            store.preferences.edit().putBoolean(ArgonBackgroundPlaybackStore.ENABLED, (Boolean) value).apply();
            store.reload();
            return true;
        });
        screen.addPreference(master);

        PreferenceCategory sites = new PreferenceCategory(requireContext());
        sites.setTitle(R.string.argon_background_sites);
        screen.addPreference(sites);
        for (var rule : store.policy.getRules()) sites.addPreference(new RulePreference(rule));
        if (store.policy.getRules().isEmpty()) {
            Preference empty = new Preference(requireContext());
            empty.setSummary(R.string.argon_background_empty);
            empty.setSelectable(false);
            sites.addPreference(empty);
        }
        Preference add = new Preference(requireContext());
        add.setTitle(R.string.argon_background_add_site);
        add.setIcon(R.drawable.argon_certificate_add);
        add.setFragment(RuleEditor.class.getName());
        sites.addPreference(add);

        Preference help = new Preference(requireContext());
        help.setSummary(R.string.argon_background_reset_description);
        help.setSelectable(false);
        screen.addPreference(help);
    }

    private final class RulePreference extends Preference {
        private ArgonBackgroundPlaybackPolicy.Rule mRule;

        RulePreference(ArgonBackgroundPlaybackPolicy.Rule rule) {
            super(ArgonBackgroundPlaybackSettings.this.requireContext());
            mRule = rule;
            setKey("argon_background_site_" + rule.host);
            setTitle(displayHost(rule.host));
            setSummary(summary(rule));
            setWidgetLayoutResource(R.layout.argon_background_rule_switch);
            setFragment(RuleEditor.class.getName());
            getExtras().putString("host", rule.host);
        }

        @Override
        public void onBindViewHolder(PreferenceViewHolder holder) {
            super.onBindViewHolder(holder);
            SwitchCompat toggle = (SwitchCompat) holder.findViewById(R.id.argon_background_rule_enabled);
            if (toggle == null) return;
            toggle.setOnCheckedChangeListener(null);
            toggle.setChecked(mRule.enabled);
            toggle.setContentDescription(getString(R.string.argon_background_toggle_site, displayHost(mRule.host)));
            toggle.setOnCheckedChangeListener((button, checked) -> {
                var store = ArgonBackgroundPlaybackStore.get();
                List<ArgonBackgroundPlaybackPolicy.Rule> rules = store.policy.getRules();
                for (int i = 0; i < rules.size(); i++) {
                    if (!rules.get(i).host.equals(mRule.host)) continue;
                    mRule = new ArgonBackgroundPlaybackPolicy.Rule(mRule.host, mRule.minutes, checked, mRule.subdomains);
                    rules.set(i, mRule);
                    store.save(rules);
                    setSummary(summary(mRule));
                    break;
                }
            });
        }

        private String summary(ArgonBackgroundPlaybackPolicy.Rule rule) {
            String duration = duration(rule.minutes);
            return getString(rule.enabled ? R.string.argon_background_limit
                    : R.string.argon_background_limit_disabled, duration);
        }
    }

    private static String displayHost(String host) {
        return UrlFormatter.formatUrlForSecurityDisplay(new GURL("https://" + host + "/"),
                SchemeDisplay.OMIT_HTTP_AND_HTTPS);
    }

    private String duration(int minutes) {
        return minutes % 60 == 0
                ? getResources().getQuantityString(R.plurals.argon_background_hours, minutes / 60, minutes / 60)
                : getResources().getQuantityString(R.plurals.argon_background_minutes, minutes, minutes);
    }

    @Override
    public MonotonicObservableSupplier<String> getPageTitle() {
        return mTitle;
    }

    @Override
    public @SettingsFragment.AnimationType int getAnimationType() {
        return SettingsFragment.AnimationType.PROPERTY;
    }

    /** Separate native screen; changes are committed together by Save. */
    public static final class RuleEditor extends ChromeBaseSettingsFragment {
        private final SettableMonotonicObservableSupplier<String> mTitle = ObservableSuppliers.createMonotonic();
        private String mOriginalHost = "";
        private String mHost = "";
        private int mMinutes = 60;
        private boolean mEnabled = true;
        private boolean mSubdomains = true;

        @Override
        public void onCreatePreferences(@Nullable Bundle saved, @Nullable String rootKey) {
            mTitle.set(getString(R.string.argon_background_rule_title));
            Bundle args = getArguments();
            mOriginalHost = args == null ? "" : args.getString("host", "");
            for (var rule : ArgonBackgroundPlaybackStore.get().policy.getRules()) {
                if (rule.host.equals(mOriginalHost)) {
                    mHost = displayHost(rule.host);
                    mMinutes = rule.minutes;
                    mEnabled = rule.enabled;
                    mSubdomains = rule.subdomains;
                }
            }
            if (saved != null) {
                mHost = saved.getString("domain", mHost);
                mMinutes = saved.getInt("minutes", mMinutes);
                mEnabled = saved.getBoolean("enabled", mEnabled);
                mSubdomains = saved.getBoolean("subdomains", mSubdomains);
            }
            mMinutes = Math.max(1, Math.min(ArgonBackgroundPlaybackPolicy.MAX_MINUTES, mMinutes));
            PreferenceScreen screen = getPreferenceManager().createPreferenceScreen(requireContext());
            setPreferenceScreen(screen);

            EditTextPreference host = new EditTextPreference(requireContext());
            host.setKey("argon_background_domain");
            host.setPersistent(false);
            host.setTitle(R.string.argon_background_domain);
            host.setDialogTitle(R.string.argon_background_domain);
            host.setDialogMessage(R.string.argon_background_domain_hint);
            // Chromium's PreferenceTheme has no EditTextPreference dialog style.
            host.setDialogLayoutResource(R.layout.preference_dialog_edittext);
            host.setPositiveButtonText(android.R.string.ok);
            host.setNegativeButtonText(android.R.string.cancel);
            host.setText(mHost);
            host.setSummary(mHost.isEmpty() ? getString(R.string.argon_background_domain_hint) : mHost);
            host.setOnBindEditTextListener(input -> {
                input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
                input.setSingleLine(true);
            });
            host.setOnPreferenceChangeListener((preference, value) -> {
                mHost = (String) value;
                host.setSummary(mHost);
                return true;
            });
            screen.addPreference(host);
            screen.addPreference(toggle(R.string.argon_background_allow_site, mEnabled,
                    value -> mEnabled = value));
            screen.addPreference(toggle(R.string.argon_background_subdomains, mSubdomains,
                    value -> mSubdomains = value));

            SeekBarPreference limit = new SeekBarPreference(requireContext());
            // Chromium's PreferenceTheme does not define seekBarPreferenceStyle.
            // The generic row lacks seekbar_value and crashes during AndroidX binding.
            // chrome_java exposes dependency resources through org.chromium.chrome.R.
            limit.setLayoutResource(R.layout.preference_widget_seekbar_material);
            limit.setPersistent(false);
            limit.setTitle(R.string.argon_background_time_limit);
            limit.setSummary(R.string.argon_background_time_description);
            limit.setMin(1);
            limit.setMax(ArgonBackgroundPlaybackPolicy.MAX_MINUTES);
            limit.setAdjustable(true);
            limit.setShowSeekBarValue(true);
            limit.setValue(mMinutes);
            limit.setOnPreferenceChangeListener((preference, value) -> {
                mMinutes = (Integer) value;
                return true;
            });
            screen.addPreference(limit);
            Preference save = new Preference(requireContext());
            save.setTitle(R.string.argon_background_save);
            save.setOnPreferenceClickListener(preference -> { save(); return true; });
            screen.addPreference(save);
            if (!mOriginalHost.isEmpty()) {
                Preference remove = new Preference(requireContext());
                remove.setTitle(R.string.argon_background_delete);
                remove.setOnPreferenceClickListener(preference -> {
                    new AlertDialog.Builder(requireContext())
                            .setTitle(R.string.argon_background_delete)
                            .setMessage(getString(R.string.argon_background_delete_description, displayHost(mOriginalHost)))
                            .setNegativeButton(android.R.string.cancel, null)
                            .setPositiveButton(R.string.argon_background_delete, (dialog, which) -> {
                                var store = ArgonBackgroundPlaybackStore.get();
                                var rules = store.policy.getRules();
                                rules.removeIf(rule -> rule.host.equals(mOriginalHost));
                                store.save(rules);
                                close();
                            }).show();
                    return true;
                });
                screen.addPreference(remove);
            }
        }

        private interface BooleanChange { void accept(boolean value); }

        private Preference toggle(int title, boolean checked, BooleanChange onChange) {
            SwitchPreferenceCompat preference = new SwitchPreferenceCompat(requireContext());
            preference.setPersistent(false);
            preference.setTitle(title);
            preference.setChecked(checked);
            preference.setOnPreferenceChangeListener((ignored, value) -> {
                onChange.accept((Boolean) value);
                return true;
            });
            return preference;
        }

        private void save() {
            String host = ArgonBackgroundPlaybackStore.normalizeHost(mHost);
            if (host == null) { error(R.string.argon_background_invalid_domain); return; }
            var store = ArgonBackgroundPlaybackStore.get();
            var rules = store.policy.getRules();
            rules.removeIf(rule -> rule.host.equals(mOriginalHost));
            for (var rule : rules) {
                if (rule.host.equals(host)) { error(R.string.argon_background_duplicate); return; }
            }
            if (rules.size() >= ArgonBackgroundPlaybackPolicy.MAX_SITES) {
                error(R.string.argon_background_too_many); return;
            }
            rules.add(new ArgonBackgroundPlaybackPolicy.Rule(host, mMinutes, mEnabled, mSubdomains));
            store.save(rules);
            close();
        }

        private void error(int message) {
            Toast.makeText(requireContext(), message, Toast.LENGTH_LONG).show();
        }

        private void close() {
            requireActivity().getOnBackPressedDispatcher().onBackPressed();
        }

        @Override
        public void onSaveInstanceState(Bundle out) {
            super.onSaveInstanceState(out);
            out.putString("domain", mHost);
            out.putInt("minutes", mMinutes);
            out.putBoolean("enabled", mEnabled);
            out.putBoolean("subdomains", mSubdomains);
        }

        @Override
        public MonotonicObservableSupplier<String> getPageTitle() { return mTitle; }

        @Override
        public @SettingsFragment.AnimationType int getAnimationType() {
            return SettingsFragment.AnimationType.PROPERTY;
        }
    }
}
