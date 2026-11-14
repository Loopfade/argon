// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.externalnav;

import android.app.AlertDialog;
import android.os.Bundle;
import android.text.InputType;
import android.view.inputmethod.EditorInfo;
import android.widget.Toast;

import androidx.preference.EditTextPreference;
import androidx.preference.ListPreference;
import androidx.preference.Preference;
import androidx.preference.PreferenceCategory;

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

/** Settings > Privacy and security > Security > Open links in apps. The allow list starts empty. */
@NullMarked
public final class ArgonExternalNavigationSettings extends ChromeBaseSettingsFragment {
    private final SettableMonotonicObservableSupplier<String> mTitle = ObservableSuppliers.createMonotonic();

    @Override
    public void onCreatePreferences(@Nullable Bundle saved, @Nullable String rootKey) {
        mTitle.set(getString(R.string.argon_external_title));
        rebuild();
    }

    @Override
    public void onResume() {
        super.onResume();
        rebuild();
    }

    private void rebuild() {
        var policy = ArgonExternalNavigationPolicy.get();
        var screen = getPreferenceManager().createPreferenceScreen(requireContext());
        setPreferenceScreen(screen);
        ListPreference mode = new ListPreference(requireContext());
        mode.setKey("argon_external_mode");
        mode.setPersistent(false);
        mode.setTitle(R.string.argon_external_mode);
        mode.setDialogTitle(R.string.argon_external_mode);
        mode.setNegativeButtonText(android.R.string.cancel);
        mode.setEntries(new CharSequence[] {getString(R.string.argon_external_allow),
                getString(R.string.argon_external_allow_list), getString(R.string.argon_external_block)});
        mode.setEntryValues(new CharSequence[] {"0", "1", "2"});
        mode.setValue(Integer.toString(policy.getMode()));
        int summary = switch (policy.getMode()) {
            case ArgonExternalNavigationPolicy.ALLOW -> R.string.argon_external_allow_description;
            case ArgonExternalNavigationPolicy.ALLOW_LIST -> R.string.argon_external_list_description;
            default -> R.string.argon_external_block_description;
        };
        mode.setSummary(mode.getEntry() + "\n" + getString(summary));
        mode.setOnPreferenceChangeListener((preference, value) -> {
            policy.setMode(Integer.parseInt((String) value));
            rebuild();
            return true;
        });
        screen.addPreference(mode);

        PreferenceCategory sites = new PreferenceCategory(requireContext());
        sites.setTitle(R.string.argon_external_sites);
        screen.addPreference(sites);
        Preference add = new Preference(requireContext());
        add.setKey("argon_external_add");
        add.setTitle(R.string.argon_external_add_site);
        add.setIcon(R.drawable.argon_certificate_add);
        add.setFragment(SiteEditor.class.getName());
        add.setEnabled(policy.getSites().size() < ArgonExternalNavigationPolicy.MAX_SITES);
        if (!add.isEnabled()) add.setSummary(R.string.argon_external_limit);
        sites.addPreference(add);

        for (String site : policy.getSites()) {
            Preference row = new Preference(requireContext());
            row.setKey("argon_external_site_" + site);
            row.setTitle(displayHost(site));
            row.setSingleLineTitle(false);
            row.setSummary(R.string.argon_external_site_scope);
            row.setOnPreferenceClickListener(preference -> {
                new AlertDialog.Builder(requireContext())
                        .setTitle(R.string.argon_external_remove)
                        .setMessage(getString(R.string.argon_external_remove_description, displayHost(site)))
                        .setNegativeButton(android.R.string.cancel, null)
                        .setPositiveButton(R.string.argon_external_remove, (dialog, which) -> {
                            policy.removeSite(site);
                            rebuild();
                        }).show();
                return true;
            });
            sites.addPreference(row);
        }
        Preference help = new Preference(requireContext());
        help.setSelectable(false);
        help.setSummary(getString(policy.getSites().isEmpty()
                ? R.string.argon_external_empty : R.string.argon_external_saved_list)
                + "\n\n" + getString(R.string.argon_external_details));
        screen.addPreference(help);
    }

    private static String displayHost(String host) {
        return UrlFormatter.formatUrlForSecurityDisplay(new GURL("https://" + host + "/"),
                SchemeDisplay.OMIT_HTTP_AND_HTTPS);
    }

    @Override
    public MonotonicObservableSupplier<String> getPageTitle() {
        return mTitle;
    }

    @Override
    public @SettingsFragment.AnimationType int getAnimationType() {
        return SettingsFragment.AnimationType.PROPERTY;
    }

    /** Preview the whole-site scope before explicitly saving it. */
    public static final class SiteEditor extends ChromeBaseSettingsFragment {
        private final SettableMonotonicObservableSupplier<String> mTitle = ObservableSuppliers.createMonotonic();
        private String mInput = "";
        private @Nullable Preference mPreview;
        private @Nullable Preference mSave;

        @Override
        public void onCreatePreferences(@Nullable Bundle saved, @Nullable String rootKey) {
            mTitle.set(getString(R.string.argon_external_add_site));
            if (saved != null) mInput = saved.getString("site", "");
            var screen = getPreferenceManager().createPreferenceScreen(requireContext());
            setPreferenceScreen(screen);
            EditTextPreference input = new EditTextPreference(requireContext());
            input.setKey("argon_external_site_input");
            input.setPersistent(false);
            input.setTitle(R.string.argon_external_domain);
            input.setDialogTitle(R.string.argon_external_domain);
            input.setDialogMessage(R.string.argon_external_domain_hint);
            input.setDialogLayoutResource(R.layout.preference_dialog_edittext);
            input.setPositiveButtonText(android.R.string.ok);
            input.setNegativeButtonText(android.R.string.cancel);
            input.setText(mInput);
            input.setSummary(mInput.isEmpty() ? getString(R.string.argon_external_domain_hint) : mInput);
            input.setOnBindEditTextListener(edit -> {
                edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI
                        | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
                edit.setSingleLine(true);
                edit.setImeOptions(EditorInfo.IME_ACTION_DONE | EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
            });
            input.setOnPreferenceChangeListener((preference, value) -> {
                mInput = (String) value;
                input.setSummary(mInput.isEmpty() ? getString(R.string.argon_external_domain_hint) : mInput);
                updatePreview();
                return true;
            });
            screen.addPreference(input);
            mPreview = new Preference(requireContext());
            mPreview.setSelectable(false);
            screen.addPreference(mPreview);
            mSave = new Preference(requireContext());
            mSave.setKey("argon_external_save");
            mSave.setTitle(R.string.argon_external_save);
            mSave.setOnPreferenceClickListener(preference -> {
                if (ArgonExternalNavigationPolicy.get().addSite(mInput)) {
                    requireActivity().getOnBackPressedDispatcher().onBackPressed();
                } else {
                    Toast.makeText(requireContext(), R.string.argon_external_save_failed, Toast.LENGTH_LONG).show();
                    updatePreview();
                }
                return true;
            });
            screen.addPreference(mSave);
            updatePreview();
        }

        private void updatePreview() {
            if (mPreview == null || mSave == null) return;
            String host = ArgonExternalNavigationPolicy.normalizeSite(mInput);
            var policy = ArgonExternalNavigationPolicy.get();
            boolean duplicate = host != null && policy.getSites().contains(host);
            boolean full = policy.getSites().size() >= ArgonExternalNavigationPolicy.MAX_SITES;
            mSave.setEnabled(host != null && !duplicate && !full);
            if (host == null) {
                mPreview.setSummary(mInput.trim().isEmpty()
                        ? R.string.argon_external_domain_hint : R.string.argon_external_invalid_site);
            } else {
                String preview = getString(R.string.argon_external_preview, displayHost(host));
                if (!host.equals(displayHost(host))) preview += "\n" + host;
                if (duplicate) preview += "\n" + getString(R.string.argon_external_duplicate);
                if (full) preview += "\n" + getString(R.string.argon_external_limit);
                preview += "\n\n" + getString(R.string.argon_external_saved_list);
                mPreview.setSummary(preview);
            }
        }

        @Override
        public void onSaveInstanceState(Bundle out) {
            super.onSaveInstanceState(out);
            out.putString("site", mInput);
        }

        @Override
        public MonotonicObservableSupplier<String> getPageTitle() {
            return mTitle;
        }

        @Override
        public @SettingsFragment.AnimationType int getAnimationType() {
            return SettingsFragment.AnimationType.PROPERTY;
        }
    }
}
