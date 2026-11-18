"""Reject co-installation collisions in aapt2's final, merged APK manifest."""
from pathlib import Path
import sys
import unittest
from apk_identity_fixture import manifest_dump

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from apk_identity import ANDROID, ANDROID_URI, manifest_from_dump, verify_manifest


class ApkIdentityTests(unittest.TestCase):
    package = "app.titaniumru.browser.test"
    production = "app.titaniumru.browser"
    identity = {"application_id": package, "version_code": 1000000001}

    def test_package_scoped_providers_permissions_processes_and_push(self):
        result = verify_manifest(manifest_dump(), self.identity, self.production)
        self.assertEqual(result["application_id"], self.package)
        self.assertEqual(result["version_code"], 1000000001)
        self.assertEqual(len(result["provider_authorities"]), 4)
        self.assertTrue(result["declares_firebase_messaging_service"])
        self.assertFalse(result["shared_uid"])
        self.assertEqual(result["verified_app_links"], [])

    def test_collision_in_one_provider_alias_is_rejected(self):
        text = manifest_dump().replace(f";{self.package}.browser;", f";{self.production}.browser;")
        with self.assertRaisesRegex(ValueError, "authority"):
            verify_manifest(text, self.identity, self.production)

    def test_androidx_content_pip_still_rejects_shared_task_affinities(self):
        scoped = self.package + ".androidx.core.pip.contentpip.isolated"
        for legacy in (False, True):
            source = manifest_dump(legacy=legacy)
            verify_manifest(source, self.identity, self.production)
            for affinity in ("androidx.core.pip.contentpip.isolated",
                             self.production + ".androidx.core.pip.contentpip.isolated"):
                with self.subTest(legacy=legacy, affinity=affinity), \
                     self.assertRaisesRegex(ValueError, "taskAffinity"):
                    verify_manifest(source.replace(scoped, affinity), self.identity, self.production)

    def test_duplicate_authority_is_rejected(self):
        text = manifest_dump().replace(f"{self.package}.ChromeBrowserProvider;", f"{self.package}.FileProvider;")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            verify_manifest(text, self.identity, self.production)

    def test_shared_uid_unscoped_process_task_and_permission_are_rejected(self):
        cases = [
            manifest_dump().replace("    E: permission", '    A: android:sharedUserId="android.uid.shared"\n    E: permission', 1),
            manifest_dump().replace('=":sandboxed_process0"', '="org.chromium.shared"'),
            manifest_dump().replace(f'="{self.package}.ChromeTabbedActivity2"', f'="{self.production}"'),
            manifest_dump().replace(f'="{self.package}.permission.CHILD_SERVICE"', f'="{self.production}.permission.CHILD_SERVICE"'),
            manifest_dump().replace(f'="{self.package}.permission.C2D_MESSAGE"', f'="{self.production}.permission.C2D_MESSAGE"'),
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                verify_manifest(text, self.identity, self.production)

    def test_shared_custom_uri_and_unregistered_verified_links_are_rejected(self):
        for text in (manifest_dump().replace('="http"', '="googlechrome"'),
                     manifest_dump().replace("        E: intent-filter (line=9)",
                         '        E: intent-filter (line=9)\n          A: android:autoVerify=(type 0x12)0xffffffff')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                verify_manifest(text, self.identity, self.production)

    def test_wrong_package_version_or_unresolved_authority_cannot_pass(self):
        for text in (manifest_dump(self.production), manifest_dump(version_code=123),
                     manifest_dump().replace(f'="{self.package}.FileProvider"', '="${applicationId}.FileProvider"')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                verify_manifest(text, self.identity, self.production)

    def test_legacy_package_and_permission_adoption_cannot_pass(self):
        for tag in ("original-package", "adopt-permissions"):
            text = manifest_dump().replace("    E: permission (line=2)",
                f'    E: {tag} (line=2)\n      A: android:name="org.chromium.chrome"\n    E: permission (line=2)')
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "adopt"):
                verify_manifest(text, self.identity, self.production)

    def test_parser_reads_sdk_typed_integer_and_fails_on_missing_manifest(self):
        root = manifest_from_dump(manifest_dump(legacy=True))
        self.assertEqual(root.get(ANDROID + "versionCode"), "1000000001")
        for text in ("", "E: application", "E: manifest\nE: manifest"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                manifest_from_dump(text)

    def test_sdk_uri_attributes_and_legacy_prefix_have_the_same_identity(self):
        modern = verify_manifest(manifest_dump(), self.identity, self.production)
        legacy = verify_manifest(manifest_dump(legacy=True), self.identity, self.production)
        self.assertEqual(modern, legacy)
        self.assertEqual(modern["version_code"], 1000000001)
        self.assertTrue(modern["declares_firebase_messaging_service"])

    def test_compiled_integer_with_raw_annotation_uses_the_compiled_value(self):
        for value in ('1000000001 (Raw: "1000000001")',
                      '0x3b9aca01 (Raw: "1000000001")',
                      '(type 0x10)0x3b9aca01 (Raw: "1000000001")'):
            text = manifest_dump().replace(
                f'{ANDROID_URI}:versionCode(0x0101021b)=1000000001',
                f'{ANDROID_URI}:versionCode(0x0101021b)={value}')
            with self.subTest(value=value):
                self.assertEqual(verify_manifest(text, self.identity, self.production)["version_code"],
                                 1000000001)
        text = manifest_dump().replace(
            f'{ANDROID_URI}:versionCode(0x0101021b)=1000000001',
            f'{ANDROID_URI}:versionCode(0x0101021b)=2 (Raw: "1000000001")')
        with self.assertRaisesRegex(ValueError, "does not match"):
            verify_manifest(text, self.identity, self.production)

    def test_missing_zero_and_wrong_namespace_version_codes_still_fail(self):
        source = manifest_dump()
        for text in (source.replace(f'    A: {ANDROID_URI}:versionCode(0x0101021b)=1000000001\n', ''),
                     source.replace('versionCode(0x0101021b)=1000000001', 'versionCode(0x0101021b)=0'),
                     source.replace(f'{ANDROID_URI}:versionCode', 'http://other.example:versionCode')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "versionCode"):
                verify_manifest(text, self.identity, self.production)
