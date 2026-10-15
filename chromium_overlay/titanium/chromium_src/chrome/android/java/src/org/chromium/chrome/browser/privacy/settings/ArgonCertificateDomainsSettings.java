// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

package org.chromium.chrome.browser.privacy.settings;

import android.app.AlertDialog;
import android.content.DialogInterface;
import android.os.Bundle;
import android.text.Editable;
import android.text.InputType;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.text.style.ForegroundColorSpan;
import android.util.TypedValue;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ScrollView;
import android.widget.TextView;

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
import org.chromium.components.url_formatter.SchemeDisplay;
import org.chromium.components.url_formatter.UrlFormatter;
import org.chromium.url.GURL;

import java.io.IOException;
import java.io.InputStream;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import javax.security.auth.x500.X500Principal;

/** Settings for DNS name constraints on Argon's built-in Russian root CA. */
@NullMarked
public final class ArgonCertificateDomainsSettings extends ChromeBaseSettingsFragment {
    private static final int ADD_SUCCESS = 0;
    private static final int ADD_INVALID = 1;
    private static final int ADD_PUBLIC_SUFFIX = 2;
    private static final int ADD_COVERED = 3;
    private static final int ADD_DUPLICATE = 4;
    private static final int ADD_TOO_MANY = 5;
    private static final String[] ZONE_LABELS = {".ru", ".рф", ".su"};

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
        mExplanation = new ExplanationPreference();
        screen.addPreference(mExplanation);

        PreferenceCategory builtIn = new PreferenceCategory(requireContext());
        builtIn.setTitle(R.string.argon_certificates_built_in_domains);
        screen.addPreference(builtIn);
        mBuiltInZones.clear();
        for (int zone = 0; zone < ZONE_LABELS.length; zone++) {
            addBuiltInZone(builtIn, zone, ZONE_LABELS[zone]);
        }

        mAdditionalDomainsCategory = new PreferenceCategory(requireContext());
        mAdditionalDomainsCategory.setTitle(R.string.argon_certificates_additional_domains);
        screen.addPreference(mAdditionalDomainsCategory);

        Preference footer = new Preference(requireContext());
        footer.setSummary(R.string.argon_certificates_domains_footer);
        footer.setSelectable(false);
        screen.addPreference(footer);
    }

    @Override
    public void onResume() {
        super.onResume();
        refreshDomains();
    }

    private final class ExplanationPreference extends Preference {
        ExplanationPreference() {
            super(ArgonCertificateDomainsSettings.this.requireContext());
            setSingleLineTitle(false);
            setOnPreferenceClickListener(
                    ignored -> {
                        showCertificateDetails();
                        return true;
                    });
        }

        @Override
        public void onBindViewHolder(PreferenceViewHolder holder) {
            super.onBindViewHolder(holder);
            TextView summary = (TextView) holder.findViewById(android.R.id.summary);
            CharSequence text = getSummary();
            if (summary == null || text == null) return;
            String more = getString(R.string.argon_certificates_more);
            SpannableString styled = new SpannableString(text);
            if (text.toString().endsWith(more)) {
                styled.setSpan(
                        new ForegroundColorSpan(summary.getLinkTextColors().getDefaultColor()),
                        text.length() - more.length(),
                        text.length(),
                        Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            }
            summary.setText(styled);
        }
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

        List<String> enabledZones = new ArrayList<>();
        for (int i = 0; i < mBuiltInZones.size(); i++) {
            boolean enabled =
                    ArgonCertificateDomainsSettingsJni.get().isBuiltInZoneEnabled(getProfile(), i);
            SwitchPreferenceCompat preference = mBuiltInZones.get(i);
            preference.setChecked(enabled);
            preference.setSummary(
                    enabled
                            ? R.string.argon_certificates_zone_enabled
                            : R.string.argon_certificates_zone_disabled);
            if (enabled) enabledZones.add(ZONE_LABELS[i]);
        }

        List<String> domains = ArgonCertificateDomainsSettingsJni.get().getDomains(getProfile());
        Preference explanation = mExplanation;
        if (explanation != null) {
            int description;
            if (!enabledZones.isEmpty()) {
                explanation.setTitle(
                        getString(
                                R.string.argon_certificates_state_zones,
                                String.join(", ", enabledZones)));
                description = R.string.argon_certificates_description_zones;
            } else if (!domains.isEmpty()) {
                explanation.setTitle(R.string.argon_certificates_state_domains);
                description = R.string.argon_certificates_description_domains;
            } else {
                explanation.setTitle(R.string.argon_certificates_state_disabled);
                description = R.string.argon_certificates_description_disabled;
            }
            explanation.setSummary(
                    getString(description) + "\n\n" + getString(R.string.argon_certificates_more));
        }

        category.removeAll();
        Preference addDomain = new Preference(requireContext());
        addDomain.setTitle(R.string.argon_certificates_add_domain);
        addDomain.setIcon(R.drawable.argon_certificate_add);
        addDomain.setOnPreferenceClickListener(
                ignored -> {
                    showAddDomainDialog();
                    return true;
                });
        category.addPreference(addDomain);

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
            List<String> rules =
                    coveredDomains.contains(domain)
                            ? ArgonCertificateDomainsSettingsJni.get()
                                    .getCoveringRules(getProfile(), domain)
                            : List.of();
            category.addPreference(new DomainPreference(domain, rules));
        }
    }

    private final class DomainPreference extends Preference {
        private final String mDomain;
        private final boolean mCovered;

        DomainPreference(String domain, List<String> rules) {
            super(ArgonCertificateDomainsSettings.this.requireContext());
            mDomain = domain;
            mCovered = !rules.isEmpty();
            setTitle(displayDomain(domain));
            setSingleLineTitle(false);
            if (rules.isEmpty()) {
                setSummary(R.string.argon_certificates_domain_scope);
            } else if (rules.size() == 1) {
                String rule = rules.get(0);
                setSummary(
                        getString(
                                rule.startsWith(".")
                                        ? R.string.argon_certificates_covered_zone
                                        : R.string.argon_certificates_covered_domain,
                                displayRule(rule)));
            } else {
                setSummary(
                        getString(R.string.argon_certificates_covered_multiple, rules.size()));
            }
            setWidgetLayoutResource(R.layout.argon_certificate_domain_overlap_widget);
            setOnPreferenceClickListener(
                    ignored -> {
                        showDomainDetails(mDomain);
                        return true;
                    });
        }

        @Override
        public void onBindViewHolder(PreferenceViewHolder holder) {
            super.onBindViewHolder(holder);
            View warning = holder.findViewById(R.id.argon_certificate_overlap);
            if (warning != null) {
                warning.setVisibility(mCovered ? View.VISIBLE : View.GONE);
                String description =
                        getString(R.string.argon_certificates_overlap_title, displayDomain(mDomain));
                warning.setContentDescription(description);
                warning.setTooltipText(description);
                warning.setOnClickListener(ignored -> showDomainDetails(mDomain));
            }
            View actions = holder.findViewById(R.id.argon_certificate_actions);
            if (actions != null) {
                String description =
                        getString(R.string.argon_certificates_domain_actions, displayDomain(mDomain));
                actions.setContentDescription(description);
                actions.setTooltipText(description);
                actions.setOnClickListener(anchor -> showDomainActions(anchor, mDomain));
            }
        }
    }

    private static String displayDomain(String domain) {
        // Display safe IDNs in Unicode using Chromium's spoof checks. Keep the
        // original ASCII domain for all preference storage and JNI operations.
        return UrlFormatter.formatUrlForSecurityDisplay(
                new GURL("https://" + domain), SchemeDisplay.OMIT_HTTP_AND_HTTPS);
    }

    private String displayRule(String rule) {
        if (".xn--p1ai".equals(rule)) return ".рф";
        return rule.startsWith(".") ? rule : displayDomain(rule);
    }

    private String formatRuleNames(List<String> rules) {
        List<String> labels = new ArrayList<>();
        for (String rule : rules) labels.add(displayRule(rule));
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

    private void showDomainActions(View anchor, String domain) {
        PopupMenu menu = new PopupMenu(requireContext(), anchor);
        menu.getMenu().add(0, 0, 0, R.string.argon_certificates_more);
        menu.getMenu().add(0, 1, 1, R.string.argon_certificates_remove);
        menu.setOnMenuItemClickListener(
                item -> {
                    if (item.getItemId() == 1) {
                        showRemoveDomainDialog(domain);
                    } else {
                        showDomainDetails(domain);
                    }
                    return true;
                });
        menu.show();
    }

    private void showDomainDetails(String domain) {
        List<String> rules =
                ArgonCertificateDomainsSettingsJni.get().getCoveringRules(getProfile(), domain);
        String displayName = displayDomain(domain);
        String message =
                rules.isEmpty()
                        ? getString(R.string.argon_certificates_domain_details_message)
                        : getString(
                                R.string.argon_certificates_overlap_message,
                                formatRuleDescriptions(rules));
        if (!domain.equals(displayName)) {
            message += "\n\n" + getString(R.string.argon_certificates_domain_ascii, domain);
        }
        showDetailsDialog(
                getString(
                        rules.isEmpty()
                                ? R.string.argon_certificates_domain_details
                                : R.string.argon_certificates_overlap_title,
                        displayName),
                message);
    }

    private void showCertificateDetails() {
        String message = getString(R.string.argon_certificates_details_description);
        // Read the exact reviewed PEM packaged by apply_scoped_ca.py. No network,
        // duplicated expiry dates, or separate fingerprint constant is involved.
        try (InputStream input = getResources().openRawResource(R.raw.argon_root_ca)) {
            X509Certificate certificate =
                    (X509Certificate)
                            CertificateFactory.getInstance("X.509").generateCertificate(input);
            DateFormat date = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT);
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded());
            StringBuilder fingerprint = new StringBuilder();
            char[] hex = "0123456789ABCDEF".toCharArray();
            for (int i = 0; i < digest.length; i++) {
                if (i > 0) fingerprint.append(i % 8 == 0 ? '\n' : ':');
                int value = digest[i] & 0xff;
                fingerprint.append(hex[value >>> 4]).append(hex[value & 15]);
            }
            message +=
                    "\n\n"
                            + getString(R.string.argon_certificates_root_issuer)
                            + "\n"
                            + certificate.getIssuerX500Principal().getName(X500Principal.RFC1779)
                            + "\n\n"
                            + getString(R.string.argon_certificates_root_validity)
                            + "\n"
                            + date.format(certificate.getNotBefore())
                            + " — "
                            + date.format(certificate.getNotAfter())
                            + "\n\n"
                            + getString(R.string.argon_certificates_root_fingerprint)
                            + "\n"
                            + fingerprint;
        } catch (GeneralSecurityException | IOException exception) {
            message += "\n\n" + getString(R.string.argon_certificates_root_read_error);
        }
        showDetailsDialog(getString(R.string.argon_certificates_details_title), message);
    }

    private void showDetailsDialog(String title, String message) {
        AlertDialog dialog =
                new AlertDialog.Builder(requireContext())
                        .setTitle(title)
                        .setMessage(message)
                        .setPositiveButton(R.string.argon_certificates_close, null)
                        .show();
        TextView messageView = dialog.findViewById(android.R.id.message);
        if (messageView != null) messageView.setTextIsSelectable(true);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void showAddDomainDialog() {
        LinearLayout content = new LinearLayout(requireContext());
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(24), dp(12), dp(24), dp(8));

        TextView label = new TextView(requireContext());
        label.setText(R.string.argon_certificates_domain_label);
        content.addView(label);

        EditText input = new EditText(requireContext());
        input.setId(View.generateViewId());
        label.setLabelFor(input.getId());
        input.setSingleLine(true);
        input.setHint(R.string.argon_certificates_domain_hint);
        // A normal text keyboard allows Cyrillic; validation remains native.
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        input.setImeOptions(
                EditorInfo.IME_ACTION_DONE | EditorInfo.IME_FLAG_NO_PERSONALIZED_LEARNING);
        content.addView(input);

        TextView error = new TextView(requireContext());
        TypedValue errorColor = new TypedValue();
        if (requireContext().getTheme().resolveAttribute(android.R.attr.colorError, errorColor, true)) {
            error.setTextColor(
                    errorColor.resourceId == 0
                            ? errorColor.data
                            : getResources()
                                    .getColor(errorColor.resourceId, requireContext().getTheme()));
        }
        error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        error.setVisibility(View.GONE);
        content.addView(error);

        TextView help = new TextView(requireContext());
        help.setText(R.string.argon_certificates_domain_help);
        help.setPadding(0, dp(8), 0, 0);
        content.addView(help);

        ScrollView scroll = new ScrollView(requireContext());
        scroll.addView(content);

        AlertDialog dialog =
                new AlertDialog.Builder(requireContext())
                        .setTitle(R.string.argon_certificates_add_domain)
                        .setView(scroll)
                        .setNegativeButton(android.R.string.cancel, null)
                        .setPositiveButton(R.string.argon_certificates_add, null)
                        .create();
        dialog.setOnShowListener(
                ignored -> {
                    Button add = dialog.getButton(DialogInterface.BUTTON_POSITIVE);
                    Runnable update =
                            () -> {
                                String domain = input.getText().toString();
                                int result =
                                        ArgonCertificateDomainsSettingsJni.get()
                                                .validateDomain(getProfile(), domain);
                                add.setEnabled(result == ADD_SUCCESS);
                                String errorText =
                                        domain.trim().isEmpty() || result == ADD_SUCCESS
                                                ? ""
                                                : getErrorMessage(result, domain);
                                if (!TextUtils.equals(error.getText(), errorText)) {
                                    error.setText(errorText);
                                }
                                error.setVisibility(errorText.isEmpty() ? View.GONE : View.VISIBLE);
                            };
                    input.addTextChangedListener(
                            new TextWatcher() {
                                @Override
                                public void beforeTextChanged(
                                        CharSequence s, int start, int count, int after) {}

                                @Override
                                public void onTextChanged(
                                        CharSequence s, int start, int before, int count) {}

                                @Override
                                public void afterTextChanged(Editable value) {
                                    update.run();
                                }
                            });
                    add.setOnClickListener(
                            button -> {
                                int result =
                                        ArgonCertificateDomainsSettingsJni.get()
                                                .addDomain(getProfile(), input.getText().toString());
                                if (result == ADD_SUCCESS) {
                                    refreshDomains();
                                    dialog.dismiss();
                                } else {
                                    update.run();
                                }
                            });
                    input.setOnEditorActionListener(
                            (view, action, event) -> {
                                if (action != EditorInfo.IME_ACTION_DONE) return false;
                                if (add.isEnabled()) add.performClick();
                                return true;
                            });
                    update.run();
                    input.requestFocus();
                });
        dialog.show();
        Window window = dialog.getWindow();
        if (window != null) {
            window.setSoftInputMode(
                    WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_VISIBLE
                            | WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        }
    }

    private String getErrorMessage(int result, String domain) {
        if (result == ADD_COVERED) {
            List<String> rules =
                    ArgonCertificateDomainsSettingsJni.get()
                            .getCoveringRules(getProfile(), domain);
            return getString(R.string.argon_certificates_error_covered, formatRuleNames(rules));
        }
        if (result == ADD_INVALID) {
            String value = domain.trim();
            if (value.contains("*")) {
                return getString(R.string.argon_certificates_error_wildcard);
            }
            if (value.startsWith(".")
                    && ArgonCertificateDomainsSettingsJni.get()
                                    .validateDomain(getProfile(), value.substring(1))
                            == ADD_PUBLIC_SUFFIX) {
                return getString(R.string.argon_certificates_error_public_suffix);
            }
            if (value.contains("/")
                    || value.contains(":")
                    || value.contains("?")
                    || value.contains("#")) {
                GURL url = new GURL(value.contains("://") ? value : "https://" + value);
                if (url.isValid()) {
                    String host = url.getHost();
                    int hostResult =
                            ArgonCertificateDomainsSettingsJni.get()
                                    .validateDomain(getProfile(), host);
                    if (hostResult == ADD_SUCCESS
                            || hostResult == ADD_COVERED
                            || hostResult == ADD_DUPLICATE
                            || hostResult == ADD_TOO_MANY) {
                        return getString(
                                R.string.argon_certificates_error_domain_only, displayDomain(host));
                    }
                }
            }
        }
        return getString(
                switch (result) {
                    case ADD_PUBLIC_SUFFIX -> R.string.argon_certificates_error_public_suffix;
                    case ADD_DUPLICATE -> R.string.argon_certificates_error_duplicate;
                    case ADD_TOO_MANY -> R.string.argon_certificates_error_too_many;
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
                .setTitle(getString(R.string.argon_certificates_remove_domain, displayDomain(domain)))
                .setMessage(message)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(
                        R.string.argon_certificates_remove,
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

        int validateDomain(
                @JniType("Profile*") Profile profile, @JniType("std::string") String domain);

        int addDomain(
                @JniType("Profile*") Profile profile, @JniType("std::string") String domain);

        boolean removeDomain(
                @JniType("Profile*") Profile profile, @JniType("std::string") String domain);
    }
}
