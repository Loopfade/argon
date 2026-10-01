import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("patch", ROOT / "scripts/apply_scoped_ca.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class PatchTests(unittest.TestCase):
    def profile(self):
        return (
            '#include "chrome/browser/net/profile_network_context_service.h"\n'
            '#include "net/cert/asn1_util.h"\n'
            'void observe() {\n'
            + patch.PROFILE_PREF_OBSERVER_ANCHOR
            + '\n}\nvoid register_prefs() {\n'
            + patch.PROFILE_PREF_REGISTRATION_ANCHOR
            + '\n}\nvoid policy() {\n'
            + patch.PROFILE_ANCHOR
            + '\n}\n'
        )

    def test_idempotent(self):
        for transform, source in [
            (patch.patch_profile, self.profile()),
            (patch.patch_verifier, '#include "net/cert/time_conversions.h"\n' + patch.VERIFIER_ANCHOR),
            (
                patch.patch_gn,
                '    "cert/cert_verify_proc_builtin.h",\n'
                '    "cert/cert_verify_proc_builtin_unittest.cc",',
            ),
            (
                patch.patch_chrome_net_gn,
                '    "profile_network_context_service.h",',
            ),
            (
                patch.patch_titanium_chrome_java_sources,
                '  "java/src/org/chromium/chrome/browser/privacy/settings/'
                'PrivacySettingsExt.java",',
            ),
            (
                patch.patch_chrome_android_gn,
                '  generate_jni("chrome_jni_headers") {\n    sources = [',
            ),
            (
                patch.patch_titanium_chrome_resources,
                '  "java/res/xml/privacy_preferences_ext.xml",',
            ),
            (
                patch.patch_titanium_android_cc_sources,
                "android_cc_ext_full_path_sources = [\n]",
            ),
            (
                patch.patch_titanium_android_cc_deps,
                "android_cc_ext_full_path_deps = [\n]",
            ),
            (
                patch.patch_titanium_privacy_preferences,
                "<PreferenceScreen>\n</PreferenceScreen>",
            ),
        ]:
            with self.subTest(transform=transform.__name__):
                value = transform(source)
                self.assertEqual(value, transform(value))

    def test_profile_patch_avoids_unsafe_pointer_arithmetic(self):
        value = patch.patch_profile(self.profile())

        self.assertIn(
            "base::ToVector(base::span(net::kTitaniumRussianRootDer))",
            value,
        )
        self.assertNotIn(
            "kTitaniumRussianRootDer + sizeof",
            value,
        )

    def test_profile_patch_registers_observes_and_reads_list_pref(self):
        value = patch.patch_profile(self.profile())

        self.assertIn("RegisterListPref(argon::prefs::kRussianCaAdditionalDomains)", value)
        self.assertIn("pref_change_registrar_.Add(argon::prefs::kRussianCaAdditionalDomains", value)
        self.assertIn("prefs->GetList(argon::prefs::kRussianCaAdditionalDomains)", value)

    def test_verifier_uses_per_instance_dynamic_constraints(self):
        value = patch.patch_verifier(
            '#include "net/cert/time_conversions.h"\n' + patch.VERIFIER_ANCHOR
        )

        self.assertIn("*additional_constraints_", value)
        self.assertIn("titanium_permitted_dns_names", value)
        self.assertIn("IsTitaniumRussianRoot", value)

    def test_android_settings_integration_is_scoped_to_titanium_extensions(self):
        java = patch.patch_titanium_chrome_java_sources(
            '  "java/src/org/chromium/chrome/browser/privacy/settings/'
            'PrivacySettingsExt.java",'
        )
        xml = patch.patch_titanium_privacy_preferences(
            "<PreferenceScreen>\n</PreferenceScreen>"
        )

        self.assertIn("ArgonCertificateDomainsSettings.java", java)
        self.assertIn("ArgonCertificateDomainsSettings", xml)

    def test_android_settings_jni_uses_single_chrome_target(self):
        chrome_android_gn = patch.patch_chrome_android_gn(
            '  generate_jni("chrome_jni_headers") {\n    sources = ['
        )
        java_source = (
            "//titanium/chromium_src/chrome/android/java/src/org/chromium/"
            "chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java"
        )
        self.assertEqual(chrome_android_gn.count(java_source), 1)
        self.assertEqual(
            chrome_android_gn.count('generate_jni("chrome_jni_headers")'), 1
        )

        cc_deps = patch.patch_titanium_android_cc_deps(
            "android_cc_ext_full_path_deps = [\n]"
        )
        self.assertEqual(cc_deps.count("//chrome/android:chrome_jni_headers"), 1)
        self.assertNotIn("argon_certificate_domains_jni_headers", cc_deps)

        cc_sources = patch.patch_titanium_android_cc_sources(
            "android_cc_ext_full_path_sources = [\n]"
        )
        self.assertIn(
            '"//chrome/browser/android/argon_certificate_domains_settings.cc"',
            cc_sources,
        )
        self.assertNotIn(
            '"argon_certificate_domains_settings.cc"',
            cc_sources,
        )

        obsolete_target = (
            ROOT
            / "chromium_overlay/titanium/chromium_src/chrome/browser/android/BUILD.gn"
        )
        self.assertFalse(obsolete_target.exists())

        native_source = (
            ROOT
            / "chromium_overlay/chrome/browser/android/argon_certificate_domains_settings.cc"
        ).read_text()
        self.assertIn(
            "chrome/android/chrome_jni_headers/"
            "ArgonCertificateDomainsSettings_jni.h",
            native_source,
        )
        self.assertIn(
            "\nDEFINE_JNI(ArgonCertificateDomainsSettings)\n",
            native_source,
        )

    def test_missing_or_duplicate_anchor_fails(self):
        for source in ["", self.profile() + self.profile()]:
            with self.assertRaises(ValueError):
                patch.patch_profile(source)

    def test_modified_constraint_cannot_be_accepted_as_already_patched(self):
        bad = patch.patch_profile(self.profile()).replace('".ru"', '".com"')
        with self.assertRaises(ValueError):
            patch.patch_profile(bad)

    def test_modified_ip_guard_cannot_be_accepted_as_already_patched(self):
        source = '#include "net/cert/time_conversions.h"\n' + patch.VERIFIER_ANCHOR
        bad = patch.patch_verifier(source).replace(
            '    CheckTitaniumRussianRootConstraints(\n'
            '        path->certs, &path->errors, titanium_permitted_dns_names);',
            '',
        )
        with self.assertRaises(ValueError):
            patch.patch_verifier(bad)

    def test_root_header_has_reviewed_bytes(self):
        self.assertEqual(
            (ROOT / "chromium_overlay/net/cert/titanium_ru_root.h").read_text(),
            patch.root_header(patch.load_root()),
        )

    def test_modified_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "certificates").mkdir()
            for name in ["ministry-ca-lock.json", "russian_trusted_root_ca.pem"]:
                data = (ROOT / "certificates" / name).read_text()
                if name.endswith(".pem"):
                    lines = data.splitlines()
                    lines[1] = ("A" if lines[1][0] != "A" else "B") + lines[1][1:]
                    data = "\n".join(lines) + "\n"
                (directory / "certificates" / name).write_text(data)
            original = patch.ROOT
            try:
                patch.ROOT = directory
                with self.assertRaises(ValueError):
                    patch.load_root()
            finally:
                patch.ROOT = original


if __name__ == "__main__":
    unittest.main()
