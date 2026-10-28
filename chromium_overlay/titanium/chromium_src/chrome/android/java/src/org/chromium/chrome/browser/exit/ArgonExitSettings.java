// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
package org.chromium.chrome.browser.exit;

import android.os.Bundle;

import androidx.preference.Preference;

import org.chromium.base.supplier.MonotonicObservableSupplier;
import org.chromium.base.supplier.ObservableSuppliers;
import org.chromium.base.supplier.SettableMonotonicObservableSupplier;
import org.chromium.build.annotations.NullMarked;
import org.chromium.build.annotations.Nullable;
import org.chromium.chrome.R;
import org.chromium.chrome.browser.profiles.ProfileManager;
import org.chromium.chrome.browser.settings.ChromeBaseSettingsFragment;
import org.chromium.components.browser_ui.settings.ChromeBaseCheckBoxPreference;
import org.chromium.components.browser_ui.settings.SettingsFragment;

/** Selection is independent of the time range used for manual browsing-data deletion. */
@NullMarked
public final class ArgonExitSettings extends ChromeBaseSettingsFragment {
    private final SettableMonotonicObservableSupplier<String> mTitle =
            ObservableSuppliers.createMonotonic();
    @Override
    public void onCreatePreferences(@Nullable Bundle saved, @Nullable String rootKey) {
        mTitle.set(getString(R.string.argon_exit_settings_title));
        String profile = ArgonExitBridgeJni.get().profileKey(
                ProfileManager.getLastUsedRegularProfile());
        var screen = getPreferenceManager().createPreferenceScreen(requireContext());
        setPreferenceScreen(screen);
        Preference description = new Preference(requireContext());
        description.setSummary(R.string.argon_exit_settings_description);
        description.setSelectable(false);
        screen.addPreference(description);
        int[] titles = {R.string.clear_history_title, R.string.clear_cookies_and_site_data_title,
                R.string.clear_cache_title, R.string.prefs_site_settings,
                R.string.clear_form_data_title, R.string.clear_passwords_title};
        for (int index = 0; index < titles.length; ++index) {
            final int bit = 1 << index;
            var row = new ChromeBaseCheckBoxPreference(requireContext());
            row.setKey("argon_exit_type_" + index);
            row.setPersistent(false);
            row.setTitle(titles[index]);
            row.setChecked((ArgonExitStore.selection(profile) & bit) != 0);
            row.setOnPreferenceChangeListener((preference, value) -> {
                if (ArgonExitCoordinator.isBlockingPages()) return false;
                int types = ArgonExitStore.selection(profile);
                ArgonExitStore.select(profile, (Boolean) value ? types | bit : types & ~bit);
                return true;
            });
            screen.addPreference(row);
        }
    }
    @Override
    public MonotonicObservableSupplier<String> getPageTitle() { return mTitle; }

    @Override
    public @SettingsFragment.AnimationType int getAnimationType() {
        return SettingsFragment.AnimationType.PROPERTY;
    }
}
