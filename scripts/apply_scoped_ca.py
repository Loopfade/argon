#!/usr/bin/env python3
"""Apply Argon branding and the pinned Russian CA with DNS/IP constraints.

The AdditionalCertificates integration is adapted from Ruthenium's
scripts/patch_chromium.py; see licenses/Ruthenium-BSD-3-Clause.txt.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path("chrome/browser/net/profile_network_context_service.cc")
VERIFIER = Path("net/cert/cert_verify_proc_builtin.cc")
GN = Path("net/BUILD.gn")
CHROME_NET_GN = Path("chrome/browser/net/BUILD.gn")
CHROME_ANDROID_GN = Path("chrome/android/BUILD.gn")
TITANIUM_CHROME_JAVA_SOURCES = Path(
    "titanium/chromium_src/chrome/android/chrome_java_ext_sources.gni"
)
TITANIUM_CHROME_RESOURCES = Path(
    "titanium/chromium_src/chrome/android/chrome_app_java_resources_ext_sources.gni"
)
TITANIUM_ANDROID_CC_SOURCES = Path(
    "titanium/chromium_src/chrome/browser/android/android_cc_ext_sources.gni"
)
TITANIUM_ANDROID_CC_DEPS = Path(
    "titanium/chromium_src/chrome/browser/android/android_cc_ext_deps.gni"
)
TITANIUM_PRIVACY_PREFERENCES = Path(
    "titanium/chromium_src/chrome/android/java/res/xml/privacy_preferences_ext.xml"
)
PROFILE_ANCHOR = """  auto additional_certificates =
      cert_verifier::mojom::AdditionalCertificates::New();"""
PROFILE_BLOCK = """
  // BEGIN TITANIUM_RU_SCOPED_ANCHOR
#if BUILDFLAG(IS_ANDROID)
  auto russian_root = cert_verifier::mojom::CertWithConstraints::New();
  russian_root->certificate =
      base::ToVector(base::span(net::kTitaniumRussianRootDer));
  russian_root->permitted_dns_names = {".ru", ".xn--p1ai", ".su"};
  for (const base::Value& value :
       prefs->GetList(argon::prefs::kRussianCaAdditionalDomains)) {
    if (russian_root->permitted_dns_names.size() >=
        3 + net::kTitaniumRussianRootMaxUserDomains) {
      break;
    }
    if (!value.is_string()) {
      continue;
    }
    std::string normalized_domain;
    if (net::NormalizeTitaniumRussianRootDomain(value.GetString(),
                                                 &normalized_domain) ==
        net::TitaniumRuDomainValidationResult::kValid) {
      russian_root->permitted_dns_names.push_back(
          std::move(normalized_domain));
    }
  }
  additional_certificates->trust_anchors_with_additional_constraints.push_back(
      std::move(russian_root));
#endif
  // END TITANIUM_RU_SCOPED_ANCHOR"""
PROFILE_PREF_REGISTRATION_ANCHOR = (
    "  registry->RegisterListPref(prefs::kCAHintCertificates);"
)
PROFILE_PREF_REGISTRATION_BLOCK = """
#if BUILDFLAG(IS_ANDROID)
  // BEGIN ARGON_RU_CA_DOMAIN_PREF_REGISTRATION
  registry->RegisterListPref(argon::prefs::kRussianCaAdditionalDomains);
  // END ARGON_RU_CA_DOMAIN_PREF_REGISTRATION
#endif"""
PROFILE_PREF_OBSERVER_ANCHOR = """  pref_change_registrar_.Add(prefs::kCAHintCertificates,
                             schedule_update_cert_policy);"""
PROFILE_PREF_OBSERVER_BLOCK = """
#if BUILDFLAG(IS_ANDROID)
  // BEGIN ARGON_RU_CA_DOMAIN_PREF_OBSERVER
  pref_change_registrar_.Add(argon::prefs::kRussianCaAdditionalDomains,
                             schedule_update_cert_policy);
  // END ARGON_RU_CA_DOMAIN_PREF_OBSERVER
#endif"""
VERIFIER_ANCHOR = "    CheckExtraConstraints(path->certs, &path->errors);"
VERIFIER_BLOCK = """
    // BEGIN TITANIUM_RU_DNS_AND_IP_CONSTRAINTS
#if BUILDFLAG(IS_ANDROID)
    base::span<const std::string> titanium_permitted_dns_names;
    for (const auto& cert_with_constraints : *additional_constraints_) {
      if (cert_with_constraints.certificate &&
          IsTitaniumRussianRoot(*cert_with_constraints.certificate)) {
        titanium_permitted_dns_names =
            base::span<const std::string>(
                cert_with_constraints.permitted_dns_names);
        break;
      }
    }
    CheckTitaniumRussianRootConstraints(
        path->certs, &path->errors, titanium_permitted_dns_names);
#endif
    // END TITANIUM_RU_DNS_AND_IP_CONSTRAINTS"""


def load_root() -> bytes:
    lock = json.loads((ROOT / "certificates/ministry-ca-lock.json").read_text())
    pem = (ROOT / "certificates/russian_trusted_root_ca.pem").read_text("ascii")
    match = re.fullmatch(
        r"\s*-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+)"
        r"-----END CERTIFICATE-----\s*", pem
    )
    if not match:
        raise ValueError("Expected exactly one PEM certificate and no other data")
    der = base64.b64decode("".join(match[1].split()), validate=True)
    if hashlib.sha256(der).hexdigest() != lock["der_sha256"]:
        raise ValueError("Russian CA fingerprint does not match the reviewed pin")
    return der


def root_header(der: bytes) -> str:
    data = "\n".join(
        "    " + ", ".join(f"0x{x:02x}" for x in der[i:i + 12]) + ","
        for i in range(0, len(der), 12)
    )
    return f"""// Generated from certificates/russian_trusted_root_ca.pem. Do not edit.
// DER SHA-256: {hashlib.sha256(der).hexdigest()}
// Source: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
#ifndef NET_CERT_TITANIUM_RU_ROOT_H_
#define NET_CERT_TITANIUM_RU_ROOT_H_
#include <cstdint>
namespace net {{
inline constexpr uint8_t kTitaniumRussianRootDer[] = {{
{data}
}};
}}  // namespace net
#endif  // NET_CERT_TITANIUM_RU_ROOT_H_
"""


def replace_once(text: str, before: str, after: str) -> str:
    if text.count(after) == 1:
        # Reject duplicated old anchors alongside an apparently applied patch.
        if text.replace(after, "").count(before):
            raise ValueError(f"Ambiguous already-patched anchor: {before!r}")
        return text
    if text.count(before) != 1:
        raise ValueError(f"Expected exactly one source anchor: {before!r}")
    return text.replace(before, after, 1)


def patch_profile(text: str) -> str:
    if "TITANIUM_RU_SCOPED_ANCHOR" in text and text.count(PROFILE_BLOCK) != 1:
        raise ValueError("Incomplete or modified scoped-anchor patch")
    for marker, block in [
        ("ARGON_RU_CA_DOMAIN_PREF_REGISTRATION", PROFILE_PREF_REGISTRATION_BLOCK),
        ("ARGON_RU_CA_DOMAIN_PREF_OBSERVER", PROFILE_PREF_OBSERVER_BLOCK),
    ]:
        if marker in text and text.count(block) != 1:
            raise ValueError(f"Incomplete or modified profile patch: {marker}")
    include = '#include "chrome/browser/net/profile_network_context_service.h"'
    text = replace_once(
        text,
        include,
        include + '\n#include "chrome/browser/net/argon_ca_prefs.h"',
    )
    include = '#include "net/cert/asn1_util.h"'
    text = replace_once(
        text,
        include,
        include + '\n#include "net/cert/titanium_ru_domain_policy.h"'
        + '\n#include "net/cert/titanium_ru_root.h"',
    )
    text = replace_once(text, PROFILE_ANCHOR, PROFILE_ANCHOR + PROFILE_BLOCK)
    text = replace_once(
        text,
        PROFILE_PREF_REGISTRATION_ANCHOR,
        PROFILE_PREF_REGISTRATION_ANCHOR + PROFILE_PREF_REGISTRATION_BLOCK,
    )
    return replace_once(
        text,
        PROFILE_PREF_OBSERVER_ANCHOR,
        PROFILE_PREF_OBSERVER_ANCHOR + PROFILE_PREF_OBSERVER_BLOCK,
    )


def patch_verifier(text: str) -> str:
    if "TITANIUM_RU_DNS_AND_IP_CONSTRAINTS" in text and text.count(VERIFIER_BLOCK) != 1:
        raise ValueError("Incomplete or modified verifier patch")
    include = '#include "net/cert/time_conversions.h"'
    text = replace_once(text, include,
                        include + '\n#include "net/cert/titanium_ru_constraints.h"')
    return replace_once(text, VERIFIER_ANCHOR, VERIFIER_ANCHOR + VERIFIER_BLOCK)


def patch_gn(text: str) -> str:
    anchor = '    "cert/cert_verify_proc_builtin.h",'
    text = replace_once(
        text,
        anchor,
        anchor + '\n    "cert/titanium_ru_root.h",'
        '\n    "cert/titanium_ru_constraints.h",'
        '\n    "cert/titanium_ru_domain_policy.h",',
    )
    test_anchor = '    "cert/cert_verify_proc_builtin_unittest.cc",'
    return replace_once(
        text,
        test_anchor,
        test_anchor + '\n    "cert/titanium_ru_domain_policy_unittest.cc",',
    )


def patch_chrome_net_gn(text: str) -> str:
    anchor = '    "profile_network_context_service.h",'
    return replace_once(
        text, anchor, anchor + '\n    "argon_ca_prefs.h",'
    )


def patch_chrome_android_gn(text: str) -> str:
    anchor = (
        '      "java/src/org/chromium/chrome/browser/privacy/settings/'
        'PrivacyPreferencesManagerImpl.java",'
    )
    addition = (
        '      "//titanium/chromium_src/chrome/android/java/src/org/chromium/'
        'chrome/browser/privacy/settings/ArgonCertificateDomainsSettings.java",'
    )
    return replace_once(text, anchor, anchor + "\n" + addition)


def patch_titanium_chrome_java_sources(text: str) -> str:
    anchor = (
        '  "java/src/org/chromium/chrome/browser/privacy/settings/'
        'PrivacySettingsExt.java",'
    )
    addition = (
        '  "java/src/org/chromium/chrome/browser/privacy/settings/'
        'ArgonCertificateDomainsSettings.java",'
    )
    return replace_once(text, anchor, anchor + "\n" + addition)


def patch_titanium_chrome_resources(text: str) -> str:
    anchor = '  "java/res/xml/privacy_preferences_ext.xml",'
    addition = (
        '  "java/res/values/argon_certificate_domains_strings.xml",\n'
        '  "java/res/values-ru/argon_certificate_domains_strings.xml",'
    )
    return replace_once(text, anchor, anchor + "\n" + addition)


def patch_titanium_android_cc_sources(text: str) -> str:
    anchor = """android_cc_ext_rel_path_sources = [
]"""
    replacement = """android_cc_ext_rel_path_sources = [
  "argon_certificate_domains_settings.cc",
]"""
    return replace_once(text, anchor, replacement)


def patch_titanium_android_cc_deps(text: str) -> str:
    anchor = "android_cc_ext_full_path_deps = ["
    replacement = anchor + """
  "//base",
  "//chrome/android:jni_headers",
  "//chrome/browser/net",
  "//chrome/browser/profiles:profile",
  "//components/prefs",
  "//net","""
    return replace_once(text, anchor, replacement)


def patch_titanium_privacy_preferences(text: str) -> str:
    anchor = "</PreferenceScreen>"
    addition = """    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_certificates"
        android:title="@string/argon_certificates_title"
        android:summary="@string/argon_certificates_summary"
        android:fragment="org.chromium.chrome.browser.privacy.settings.ArgonCertificateDomainsSettings" />
"""
    return replace_once(text, anchor, addition + anchor)


def apply(src: Path, product_name: bool = True) -> None:
    der = load_root()
    generated = ROOT / "chromium_overlay/net/cert/titanium_ru_root.h"
    if generated.read_text() != root_header(der):
        raise ValueError("Generated CA header differs from the pinned certificate")
    # Validate all inputs first. Never silently continue after a source change.
    writes = {
        src / PROFILE: patch_profile((src / PROFILE).read_text()),
        src / VERIFIER: patch_verifier((src / VERIFIER).read_text()),
        src / GN: patch_gn((src / GN).read_text()),
        src / CHROME_NET_GN: patch_chrome_net_gn(
            (src / CHROME_NET_GN).read_text()
        ),
        src / CHROME_ANDROID_GN: patch_chrome_android_gn(
            (src / CHROME_ANDROID_GN).read_text()
        ),
        src / TITANIUM_CHROME_JAVA_SOURCES: patch_titanium_chrome_java_sources(
            (src / TITANIUM_CHROME_JAVA_SOURCES).read_text()
        ),
        src / TITANIUM_CHROME_RESOURCES: patch_titanium_chrome_resources(
            (src / TITANIUM_CHROME_RESOURCES).read_text()
        ),
        src / TITANIUM_ANDROID_CC_SOURCES: patch_titanium_android_cc_sources(
            (src / TITANIUM_ANDROID_CC_SOURCES).read_text()
        ),
        src / TITANIUM_ANDROID_CC_DEPS: patch_titanium_android_cc_deps(
            (src / TITANIUM_ANDROID_CC_DEPS).read_text()
        ),
        src / TITANIUM_PRIVACY_PREFERENCES: patch_titanium_privacy_preferences(
            (src / TITANIUM_PRIVACY_PREFERENCES).read_text()
        ),
    }
    for source in (ROOT / "chromium_overlay").rglob("*"):
        if not source.is_file():
            continue
        target = src / source.relative_to(ROOT / "chromium_overlay")
        value = source.read_text()
        if target.exists() and target.read_text() != value:
            raise ValueError(f"Refusing to overwrite an unexpected overlay: {target}")
        writes[target] = value
    if product_name:
        channel = src / "chrome/android/java/res_titanium_base/values/channel_constants.xml"
        text = channel.read_text()
        text = replace_once(text, '>Titanium</string>', '>Argon</string>')
        writes[channel] = text
    for target, value in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-src", type=Path)
    parser.add_argument("--generate-header", action="store_true")
    args = parser.parse_args()
    if args.generate_header:
        target = ROOT / "chromium_overlay/net/cert/titanium_ru_root.h"
        target.write_text(root_header(load_root()))
    elif args.chromium_src:
        apply(args.chromium_src.resolve())
    else:
        parser.error("Use --chromium-src PATH or --generate-header")


if __name__ == "__main__":
    main()
