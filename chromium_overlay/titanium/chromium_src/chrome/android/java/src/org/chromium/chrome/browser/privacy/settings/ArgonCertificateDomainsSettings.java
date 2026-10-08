// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.privacy.settings;

import android.app.AlertDialog;
import android.content.DialogInterface;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.EditText;

import androidx.preference.Preference;
import androidx.preference.PreferenceCategory;
import androidx.preference.PreferenceScreen;
import androidx.preference.PreferenceViewHolder;
import androidx.preference.SwitchPreferenceCompat;

import org.jni_zero.JniType;
import org.jni_zero.NativeMethods;

import org.chromium.base.supplier.MonotonicObservableSupplier;
import org.chromium.base.supplier.ObservableSuppliers;
import org.chromium.base.supplier.SettableMonotonicObservableSupplier;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.profiles.Profile;
import org.chromium.chrome.browser.settings.ChromeBaseSettingsFragment;
import org.chromium.components.browser_ui.settings.SettingsFragment;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Settings for DNS name constraints on Argon's built-in Russian root CA. */
@NullMarked
public final class ArgonCertificateDomainsSettings extends ChromeBaseSettingsFragment {
    private static final int ADD_SUCCESS = 0;
    private static final int ADD_INVALID = 1;
    private static final int ADD_PUBLIC_SUFFIX = 2;
    private static final int ADD_COVERED = 3;
    private static final int ADD_DUPLICATE = 4;
    private static final int ADD_TOO_MANY = 5;

    private final SettableMonotonicObservableSupplier<String> mPageTitle =
            ObservableSuppliers.createMonotonic();
    private final List<SwitchPreferenceCompat> mBuiltInZones = new ArrayList<>();
    private @Nullable Preference mExplanation;
    private @Nullable PreferenceCategory mAdditionalDomainsCategory;

    @Override
    public void onCreatePreferences(
            @Nullable Bundle savedInstanceState, @Nullable String rootKey) {
        mPageTitle.set(getString(R.string.argon_certificates_title));

        PreferenceScreen screen = getPreferenceManager().createPreferenceScreen(requireContext());
        setPreferenceScreen(screen);

        mExplanation = new Preference(requireContext());
        mExplanation.setSelectable(false);
        screen.addPreference(mExplanation);

        PreferenceCategory builtIn = new PreferenceCategory(requireContext());
        builtIn.setTitle(R.string.argon_certificates_built_in_domains);
        screen.addPreference(builtIn);
        mBuiltInZones.clear();
        addBuiltInZone(builtIn, 0, ".ru");
        addBuiltInZone(builtIn, 1, ".рф (.xn--p1ai)");
        addBuiltInZone(builtIn, 2, ".su");

        mAdditionalDomainsCategory = new PreferenceCategory(requireContext());
        mAdditionalDomainsCategory.setTitle(R.string.argon_certificates_additional_domains);
        screen.addPreference(mAdditionalDomainsCategory);

        Preference addDomain = new Preference(requireContext());
        addDomain.setTitle(R.string.argon_certificates_add_domain);
        addDomain.setOnPreferenceClickListener(
                preference -> {
                    showAddDomainDialog();
                    return true;
                });
        screen.addPreference(addDomain);
    }

    @Override
    public void onResume() {
        super.onResume();
        refreshDomains();
    }

    private void addBuiltInZone(PreferenceCategory category, int zone, String title) {
        SwitchPreferenceCompat preference = new SwitchPreferenceCompat(requireContext());
        // Store switches in Chromium's profile prefs, not Android SharedPreferences.
        preference.setPersistent(false);
        preference.setTitle(title);
        preference.setOnPreferenceChangeListener(
                (ignored, newValue) -> {
                    boolean enabled = Boolean.TRUE.equals(newValue);
                    if (!ArgonCertificateDomainsSettingsJni.get()
                            .setBuiltInZoneEnabled(getProfile(), zone, enabled)) {
                        return false;
                    }
                    refreshDomains();
                    return true;
                });
        mBuiltInZones.add(preference);
        category.addPreference(preference);
    }

    private void refreshDomains() {
        PreferenceCategory category = mAdditionalDomainsCategory;
        if (category == null) return;

        boolean anyZoneEnabled = false;
        for (int i = 0; i < mBuiltInZones.size(); i++) {
            boolean enabled =
                    ArgonCertificateDomainsSettingsJni.get().isBuiltInZoneEnabled(getProfile(), i);
            SwitchPreferenceCompat preference = mBuiltInZones.get(i);
            preference.setChecked(enabled);
            preference.setSummary(
                    enabled
                            ? R.string.argon_certificates_zone_enabled
                            : R.string.argon_certificates_zone_disabled);
            anyZoneEnabled |= enabled;
        }

        List<String> domains = ArgonCertificateDomainsSettingsJni.get().getDomains(getProfile());
        Preference explanation = mExplanation;
        if (explanation != null) {
            String summary = getString(R.string.argon_certificates_description);
            if (!anyZoneEnabled && domains.isEmpty()) {
                summary += "\n\n" + getString(R.string.argon_certificates_root_disabled);
            }
            explanation.setSummary(summary);
        }

        category.removeAll();
        if (domains.isEmpty()) {
            Preference empty = new Preference(requireContext());
            empty.setSummary(R.string.argon_certificates_no_additional_domains);
            empty.setSelectable(false);
            category.addPreference(empty);
            return;
        }
        Set<String> coveredDomains =
                new HashSet<>(
                        ArgonCertificateDomainsSettingsJni.get()
                                .getCoveredDomains(getProfile(), domains));
        for (String domain : domains) {
            category.addPreference(new DomainPreference(domain, coveredDomains.contains(domain)));
        }
    }

    private final class DomainPreference extends Preference {
        private final String mDomain;

        DomainPreference(String domain, boolean covered) {
            super(ArgonCertificateDomainsSettings.this.requireContext());
            mDomain = domain;
            setTitle(domain);
            setSummary(
                    covered
                            ? R.string.argon_certificates_redundant_summary
                            : R.string.argon_certificates_tap_to_remove);
            if (covered) {
                setWidgetLayoutResource(R.layout.argon_certificate_domain_overlap_widget);
            }
            setOnPreferenceClickListener(
                    ignored -> {
                        showRemoveDomainDialog(mDomain);
                        return true;
                    });
        }

        @Override
        public void onBindViewHolder(PreferenceViewHolder holder) {
            super.onBindViewHolder(holder);
            View warning = holder.findViewById(R.id.argon_certificate_overlap);
            if (warning != null) {
                String description =
                        getString(R.string.argon_certificates_overlap_details, mDomain);
                warning.setContentDescription(description);
                warning.setTooltipText(description);
                warning.setOnClickListener(ignored -> showOverlapDialog(mDomain));
            }
        }
    }

    private String displayRule(String rule) {
        return ".xn--p1ai".equals(rule) ? ".рф (.xn--p1ai)" : rule;
    }

    private String formatRuleNames(List<String> rules) {
        List<String> labels = new ArrayList<>();
        for (String rule : rules) {
            labels.add(displayRule(rule));
        }
        return String.join(", ", labels);
    }

    private String formatRuleDescriptions(List<String> rules) {
        List<String> labels = new ArrayList<>();
        for (String rule : rules) {
            int resource =
                    rule.startsWith(".")
                            ? R.string.argon_certificates_overlap_zone_source
                            : R.string.argon_certificates_overlap_domain_source;
            labels.add("• " + getString(resource, displayRule(rule)));
        }
        return String.join("\n", labels);
    }

    private void showOverlapDialog(String domain) {
        List<String> rules =
                ArgonCertificateDomainsSettingsJni.get().getCoveringRules(getProfile(), domain);
        if (rules.isEmpty()) {
            refreshDomains();
            return;
        }
        new AlertDialog.Builder(requireContext())
                .setTitle(getString(R.string.argon_certificates_overlap_title, domain))
                .setMessage(
                        getString(
                                R.string.argon_certificates_overlap_message,
                                domain,
                                formatRuleDescriptions(rules)))
                .setPositiveButton(android.R.string.ok, null)
                .show();
    }

    private void showAddDomainDialog() {
        EditText input = new EditText(requireContext());
        input.setSingleLine(true);
        input.setHint(R.string.argon_certificates_domain_hint);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);

        AlertDialog dialog =
                new AlertDialog.Builder(requireContext())
                        .setTitle(R.string.argon_certificates_add_domain)
                        .setView(input)
                        .setNegativeButton(android.R.string.cancel, null)
                        .setPositiveButton(android.R.string.ok, null)
                        .create();
        dialog.setOnShowListener(
                ignored ->
                        dialog.getButton(DialogInterface.BUTTON_POSITIVE)
                                .setOnClickListener(
                                        button -> {
                                            String domain = input.getText().toString();
                                            int result =
                                                    ArgonCertificateDomainsSettingsJni.get()
                                                            .addDomain(getProfile(), domain);
                                            if (result == ADD_SUCCESS) {
                                                refreshDomains();
                                                dialog.dismiss();
                                            } else {
                                                input.setError(getErrorMessage(result, domain));
                                            }
                                        }));
        dialog.show();
    }

    private String getErrorMessage(int result, String domain) {
        if (result == ADD_COVERED) {
            List<String> rules =
                    ArgonCertificateDomainsSettingsJni.get().getCoveringRules(getProfile(), domain);
            return getString(
                    R.string.argon_certificates_error_covered, formatRuleNames(rules));
        }
        return getString(
                switch (result) {
                    case ADD_PUBLIC_SUFFIX -> R.string.argon_certificates_error_public_suffix;
                    case ADD_DUPLICATE -> R.string.argon_certificates_error_duplicate;
                    case ADD_TOO_MANY -> R.string.argon_certificates_error_too_many;
                    case ADD_INVALID -> R.string.argon_certificates_error_invalid;
                    default -> R.string.argon_certificates_error_invalid;
                });
    }

    private void showRemoveDomainDialog(String domain) {
        List<String> rules =
                ArgonCertificateDomainsSettingsJni.get().getCoveringRules(getProfile(), domain);
        String message =
                rules.isEmpty()
                        ? getString(R.string.argon_certificates_remove_domain_message)
                        : getString(
                                R.string.argon_certificates_remove_covered_domain_message,
                                formatRuleDescriptions(rules));
        new AlertDialog.Builder(requireContext())
                .setTitle(getString(R.string.argon_certificates_remove_domain, domain))
                .setMessage(message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(
                        android.R.string.ok,
                        (dialog, which) -> {
                            ArgonCertificateDomainsSettingsJni.get()
                                    .removeDomain(getProfile(), domain);
                            refreshDomains();
                        })
                .show();
    }

    @Override
    public MonotonicObservableSupplier<String> getPageTitle() {
        return mPageTitle;
    }

    @Override
    public @SettingsFragment.AnimationType int getAnimationType() {
        return SettingsFragment.AnimationType.PROPERTY;
    }

    @NativeMethods
    interface Natives {
        @JniType("std::vector<std::string>")
        List<String> getDomains(@JniType("Profile*") Profile profile);

        @JniType("std::vector<std::string>")
        List<String> getCoveredDomains(
                @JniType("Profile*") Profile profile,
                @JniType("std::vector<std::string>") List<String> domains);

        boolean isBuiltInZoneEnabled(@JniType("Profile*") Profile profile, int zone);

        boolean setBuiltInZoneEnabled(
                @JniType("Profile*") Profile profile, int zone, boolean enabled);

        @JniType("std::vector<std::string>")
        List<String> getCoveringRules(
                @JniType("Profile*") Profile profile,
                @JniType("std::string") String domain);

        int addDomain(
                @JniType("Profile*") Profile profile,
                @JniType("std::string") String domain);

        boolean removeDomain(
                @JniType("Profile*") Profile profile,
                @JniType("std::string") String domain);
    }
}
