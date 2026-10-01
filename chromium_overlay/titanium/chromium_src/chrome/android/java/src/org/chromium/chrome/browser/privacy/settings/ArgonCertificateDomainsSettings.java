// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.privacy.settings;

import android.app.AlertDialog;
import android.content.DialogInterface;
import android.os.Bundle;
import android.text.InputType;
import android.widget.EditText;

import androidx.preference.Preference;
import androidx.preference.PreferenceCategory;
import androidx.preference.PreferenceScreen;

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

import java.util.List;

/** Settings for extra DNS name constraints on Argon's built-in Russian root CA. */
@NullMarked
public final class ArgonCertificateDomainsSettings extends ChromeBaseSettingsFragment {
    private static final int ADD_SUCCESS = 0;
    private static final int ADD_INVALID = 1;
    private static final int ADD_PUBLIC_SUFFIX = 2;
    private static final int ADD_COVERED_BY_BUILT_IN = 3;
    private static final int ADD_DUPLICATE = 4;
    private static final int ADD_TOO_MANY = 5;

    private final SettableMonotonicObservableSupplier<String> mPageTitle =
            ObservableSuppliers.createMonotonic();
    private @Nullable PreferenceCategory mAdditionalDomainsCategory;

    @Override
    public void onCreatePreferences(
            @Nullable Bundle savedInstanceState, @Nullable String rootKey) {
        mPageTitle.set(getString(R.string.argon_certificates_title));

        PreferenceScreen screen = getPreferenceManager().createPreferenceScreen(requireContext());
        setPreferenceScreen(screen);

        Preference explanation = new Preference(requireContext());
        explanation.setSummary(R.string.argon_certificates_description);
        explanation.setSelectable(false);
        screen.addPreference(explanation);

        PreferenceCategory builtIn = new PreferenceCategory(requireContext());
        builtIn.setTitle(R.string.argon_certificates_built_in_domains);
        screen.addPreference(builtIn);
        addReadOnlyDomain(builtIn, ".ru");
        addReadOnlyDomain(builtIn, ".рф (.xn--p1ai)");
        addReadOnlyDomain(builtIn, ".su");

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

        refreshDomains();
    }

    @Override
    public void onResume() {
        super.onResume();
        refreshDomains();
    }

    private void addReadOnlyDomain(PreferenceCategory category, String domain) {
        Preference preference = new Preference(requireContext());
        preference.setTitle(domain);
        preference.setSelectable(false);
        category.addPreference(preference);
    }

    private void refreshDomains() {
        PreferenceCategory category = mAdditionalDomainsCategory;
        if (category == null) return;
        category.removeAll();

        List<String> domains = ArgonCertificateDomainsSettingsJni.get().getDomains(getProfile());
        if (domains.isEmpty()) {
            Preference empty = new Preference(requireContext());
            empty.setSummary(R.string.argon_certificates_no_additional_domains);
            empty.setSelectable(false);
            category.addPreference(empty);
            return;
        }
        for (String domain : domains) {
            Preference preference = new Preference(requireContext());
            preference.setTitle(domain);
            preference.setSummary(R.string.argon_certificates_tap_to_remove);
            preference.setOnPreferenceClickListener(
                    ignored -> {
                        showRemoveDomainDialog(domain);
                        return true;
                    });
            category.addPreference(preference);
        }
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
                                            int result =
                                                    ArgonCertificateDomainsSettingsJni.get()
                                                            .addDomain(
                                                                    getProfile(),
                                                                    input.getText().toString());
                                            if (result == ADD_SUCCESS) {
                                                refreshDomains();
                                                dialog.dismiss();
                                            } else {
                                                input.setError(getErrorMessage(result));
                                            }
                                        }));
        dialog.show();
    }

    private String getErrorMessage(int result) {
        return getString(
                switch (result) {
                    case ADD_PUBLIC_SUFFIX -> R.string.argon_certificates_error_public_suffix;
                    case ADD_COVERED_BY_BUILT_IN ->
                            R.string.argon_certificates_error_covered_by_built_in;
                    case ADD_DUPLICATE -> R.string.argon_certificates_error_duplicate;
                    case ADD_TOO_MANY -> R.string.argon_certificates_error_too_many;
                    case ADD_INVALID -> R.string.argon_certificates_error_invalid;
                    default -> R.string.argon_certificates_error_invalid;
                });
    }

    private void showRemoveDomainDialog(String domain) {
        new AlertDialog.Builder(requireContext())
                .setTitle(getString(R.string.argon_certificates_remove_domain, domain))
                .setMessage(R.string.argon_certificates_remove_domain_message)
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

        int addDomain(
                @JniType("Profile*") Profile profile,
                @JniType("std::string") String domain);

        boolean removeDomain(
                @JniType("Profile*") Profile profile,
                @JniType("std::string") String domain);
    }
}
