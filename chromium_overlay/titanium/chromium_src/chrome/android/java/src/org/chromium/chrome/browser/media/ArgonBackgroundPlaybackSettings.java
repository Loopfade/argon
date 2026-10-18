// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.media;

import android.app.AlertDialog;
import android.os.Bundle;
import android.text.InputType;
import android.view.inputmethod.EditorInfo;
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

import java.util.ArrayList;
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
        explanation.setKey("argon_background_explanation");
        explanation.setTitle(store.policy.isEnabled() ? R.string.argon_background_state_enabled
                : R.string.argon_background_state_disabled);
        explanation.setSingleLineTitle(false);
        explanation.setSummary(getString(store.policy.isEnabled()
                ? R.string.argon_background_description : R.string.argon_background_description_disabled)
                + "\n\n" + getString(R.string.argon_background_more));
        explanation.setOnPreferenceClickListener(ignored -> {
            showDetails(this, getString(R.string.argon_background_title),
                    getString(R.string.argon_background_details_description));
            return true;
        });
        screen.addPreference(explanation);

        SwitchPreferenceCompat master = new SwitchPreferenceCompat(requireContext());
        master.setPersistent(false);
        master.setTitle(R.string.argon_background_enable);
        master.setSummary(R.string.argon_background_master_description);
        master.setChecked(store.policy.isEnabled());
        master.setOnPreferenceChangeListener((preference, value) -> {
            store.preferences.edit().putBoolean(ArgonBackgroundPlaybackStore.ENABLED, (Boolean) value).apply();
            store.reload();
            rebuild();
            return true;
        });
        screen.addPreference(master);

        PreferenceCategory sites = new PreferenceCategory(requireContext());
        sites.setTitle(R.string.argon_background_sites);
        screen.addPreference(sites);
        Preference add = new Preference(requireContext());
        add.setTitle(R.string.argon_background_add_site);
        add.setIcon(R.drawable.argon_certificate_add);
        add.setFragment(RuleEditor.class.getName());
        sites.addPreference(add);
        for (var rule : store.policy.getRules()) sites.addPreference(new RulePreference(rule));
        if (store.policy.getRules().isEmpty()) {
            Preference empty = new Preference(requireContext());
            empty.setSummary(R.string.argon_background_empty);
            empty.setSelectable(false);
            sites.addPreference(empty);
        }

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
            setSingleLineTitle(false);
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
                    var current = rules.get(i);
                    if (!current.host.equals(mRule.host)) continue;
                    // Another settings screen may have edited this rule since binding.
                    mRule = new ArgonBackgroundPlaybackPolicy.Rule(current.host, current.minutes, checked, current.subdomains);
                    rules.set(i, mRule);
                    store.save(rules);
                    rebuild();
                    break;
                }
            });
        }

        private String summary(ArgonBackgroundPlaybackPolicy.Rule rule) {
            return ruleSummary(ArgonBackgroundPlaybackSettings.this,
                    ArgonBackgroundPlaybackStore.get().policy, rule, rule.host);
        }
    }

    private static String displayHost(String host) {
        return UrlFormatter.formatUrlForSecurityDisplay(new GURL("https://" + host + "/"),
                SchemeDisplay.OMIT_HTTP_AND_HTTPS);
    }

    private static String duration(ChromeBaseSettingsFragment page, int minutes) {
        return minutes % 60 == 0
                ? page.getResources().getQuantityString(R.plurals.argon_background_hours, minutes / 60, minutes / 60)
                : page.getResources().getQuantityString(R.plurals.argon_background_minutes, minutes, minutes);
    }

    private static String scope(ChromeBaseSettingsFragment page, ArgonBackgroundPlaybackPolicy.Rule rule) {
        return page.getString(rule.subdomains ? R.string.argon_background_scope_subdomains
                : R.string.argon_background_scope_exact);
    }

    private static String ruleState(ChromeBaseSettingsFragment page, ArgonBackgroundPlaybackPolicy.Rule rule) {
        return page.getString(rule.enabled ? R.string.argon_background_rule_allowed : R.string.argon_background_rule_blocked);
    }

    private static String ruleSummary(ChromeBaseSettingsFragment page, ArgonBackgroundPlaybackPolicy policy,
            ArgonBackgroundPlaybackPolicy.Rule rule, String replacedHost) {
        String summary = page.getString(R.string.argon_background_rule_summary,
                ruleState(page, rule), duration(page, rule.minutes), scope(page, rule));
        int overlaps = policy.getOverlappingRules(rule, replacedHost).size();
        if (overlaps > 0) summary += "\n" + page.getString(R.string.argon_background_overlap_count, overlaps);
        if (!policy.isEnabled()) summary += "\n" + page.getString(R.string.argon_background_rule_inactive);
        return summary;
    }

    private static String ruleNames(ChromeBaseSettingsFragment page, List<ArgonBackgroundPlaybackPolicy.Rule> rules) {
        List<String> labels = new ArrayList<>();
        for (var rule : rules) {
            labels.add("• " + page.getString(R.string.argon_background_overlap_rule, displayHost(rule.host),
                    ruleState(page, rule), duration(page, rule.minutes), scope(page, rule)));
        }
        return String.join("\n", labels);
    }

    private static String ruleDetails(ChromeBaseSettingsFragment page, ArgonBackgroundPlaybackPolicy policy,
            ArgonBackgroundPlaybackPolicy.Rule rule, String replacedHost) {
        String message = page.getString(R.string.argon_background_rule_details_description,
                displayHost(rule.host), ruleState(page, rule), scope(page, rule), duration(page, rule.minutes));
        if (!policy.isEnabled()) message += "\n\n" + page.getString(R.string.argon_background_description_disabled);
        List<ArgonBackgroundPlaybackPolicy.Rule> overlaps = policy.getOverlappingRules(rule, replacedHost);
        message += "\n\n" + (overlaps.isEmpty() ? page.getString(R.string.argon_background_no_overlaps)
                : page.getString(R.string.argon_background_overlap_description, ruleNames(page, overlaps)));
        message += "\n\n" + page.getString(R.string.argon_background_rule_priority);
        if (!rule.host.equals(displayHost(rule.host))) {
            message += "\n\n" + page.getString(R.string.argon_background_domain_ascii, rule.host);
        }
        return message;
    }

    private static void showDetails(ChromeBaseSettingsFragment page, String title, String message) {
        new AlertDialog.Builder(page.requireContext()).setTitle(title).setMessage(message)
                .setPositiveButton(R.string.argon_background_close, null).show();
    }

    private static String removalDescription(ChromeBaseSettingsFragment page, String host) {
        var policy = ArgonBackgroundPlaybackStore.get().policy;
        String message = page.getString(R.string.argon_background_delete_description, displayHost(host));
        var covering = policy.getCoveringRules(host, host);
        message += "\n\n" + (covering.isEmpty()
                ? page.getString(R.string.argon_background_delete_uncovered, displayHost(host))
                : page.getString(R.string.argon_background_delete_covered, displayHost(host),
                        ruleNames(page, covering.subList(0, 1))));
        List<ArgonBackgroundPlaybackPolicy.Rule> children = new ArrayList<>();
        for (var rule : policy.getRules()) {
            if (!rule.host.equals(host) && rule.host.endsWith("." + host)) children.add(rule);
        }
        if (!children.isEmpty()) {
            message += "\n\n" + page.getString(R.string.argon_background_delete_children, ruleNames(page, children));
        }
        return message;
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
        private @Nullable Preference mDescription;
        private @Nullable Preference mSave;

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

            mDescription = new Preference(requireContext());
            mDescription.setTitle(R.string.argon_background_rule_details);
            mDescription.setSingleLineTitle(false);
            mDescription.setOnPreferenceClickListener(ignored -> {
                String domain = ArgonBackgroundPlaybackStore.normalizeHost(mHost);
                if (domain != null) {
                    var rule = new ArgonBackgroundPlaybackPolicy.Rule(domain, mMinutes, mEnabled, mSubdomains);
                    showDetails(this, getString(R.string.argon_background_rule_details_for, displayHost(domain)),
                            ruleDetails(this, ArgonBackgroundPlaybackStore.get().policy, rule, mOriginalHost));
                }
                return true;
            });
            screen.addPreference(mDescription);

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
                input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
                input.setSingleLine(true);
                input.setImeOptions(EditorInfo.IME_ACTION_DONE | EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
            });
            host.setOnPreferenceChangeListener((preference, value) -> {
                mHost = (String) value;
                host.setSummary(mHost);
                refreshDescription();
                return true;
            });
            screen.addPreference(host);
            screen.addPreference(toggle(R.string.argon_background_allow_site, mEnabled,
                    value -> { mEnabled = value; refreshDescription(); }));
            screen.addPreference(toggle(R.string.argon_background_subdomains, mSubdomains,
                    value -> { mSubdomains = value; refreshDescription(); }));

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
                refreshDescription();
                return true;
            });
            screen.addPreference(limit);
            Preference save = new Preference(requireContext());
            mSave = save;
            save.setTitle(R.string.argon_background_save);
            save.setOnPreferenceClickListener(preference -> { save(); return true; });
            screen.addPreference(save);
            if (!mOriginalHost.isEmpty()) {
                Preference remove = new Preference(requireContext());
                remove.setTitle(R.string.argon_background_delete);
                remove.setOnPreferenceClickListener(preference -> {
                    new AlertDialog.Builder(requireContext())
                            .setTitle(R.string.argon_background_delete)
                            .setMessage(removalDescription(this, mOriginalHost))
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
            refreshDescription();
        }

        @Override
        public void onResume() {
            super.onResume();
            ArgonBackgroundPlaybackStore.get().reload();
            refreshDescription();
        }

        private void refreshDescription() {
            Preference description = mDescription;
            Preference save = mSave;
            if (description == null || save == null) return;
            String host = ArgonBackgroundPlaybackStore.normalizeHost(mHost);
            int error = validationError(host);
            save.setEnabled(error == 0);
            save.setSummary(error == 0 ? getString(R.string.argon_background_save_description) : getString(error));
            description.setSelectable(error == 0);
            if (error != 0 || host == null) {
                description.setSummary(mHost.trim().isEmpty()
                        ? getString(R.string.argon_background_domain_hint) : getString(error));
                return;
            }
            var rule = new ArgonBackgroundPlaybackPolicy.Rule(host, mMinutes, mEnabled, mSubdomains);
            description.setSummary(ruleSummary(this, ArgonBackgroundPlaybackStore.get().policy, rule, mOriginalHost)
                    + "\n\n" + getString(R.string.argon_background_more));
        }

        private int validationError(@Nullable String host) {
            if (host == null) return R.string.argon_background_invalid_domain;
            var rules = ArgonBackgroundPlaybackStore.get().policy.getRules();
            rules.removeIf(rule -> rule.host.equals(mOriginalHost));
            for (var rule : rules) {
                if (rule.host.equals(host)) return R.string.argon_background_duplicate;
            }
            return rules.size() >= ArgonBackgroundPlaybackPolicy.MAX_SITES ? R.string.argon_background_too_many : 0;
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
            int error = validationError(host);
            if (error != 0 || host == null) { refreshDescription(); error(error); return; }
            var store = ArgonBackgroundPlaybackStore.get();
            var rules = store.policy.getRules();
            rules.removeIf(rule -> rule.host.equals(mOriginalHost));
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
