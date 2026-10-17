#!/usr/bin/env python3
"""Apply Argon branding, build fixes and the scoped Russian CA.

The AdditionalCertificates integration is adapted from Ruthenium's
scripts/patch_chromium.py; see licenses/Ruthenium-BSD-3-Clause.txt.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path("chrome/browser/net/profile_network_context_service.cc")
VERIFIER = Path("net/cert/cert_verify_proc_builtin.cc")
GN = Path("net/BUILD.gn")
CHROME_NET_GN = Path("chrome/browser/net/BUILD.gn")
CHROME_ANDROID_GN = Path("chrome/android/BUILD.gn")
OMNIBOX_GN = Path("components/omnibox/browser/BUILD.gn")
MAIN_PREFERENCES = Path("chrome/android/java/res/xml/main_preferences.xml")
ABOUT_PREFERENCES = Path("chrome/android/java/res/xml/about_chrome_preferences.xml")
ABOUT_SETTINGS = Path(
    "chrome/android/java/src/org/chromium/chrome/browser/about_settings/"
    "AboutChromeSettings.java"
)
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
TITANIUM_ROOT_RESOURCE = Path(
    "titanium/chromium_src/chrome/android/java/res/raw/argon_root_ca.pem"
)
PROFILE_ANCHOR = """  auto additional_certificates =
      cert_verifier::mojom::AdditionalCertificates::New();"""
PROFILE_BLOCK = """
  // BEGIN TITANIUM_RU_SCOPED_ANCHOR
#if BUILDFLAG(IS_ANDROID)
  auto argon_permitted_dns_names = argon::GetRussianCaPermittedDnsNames(prefs);
  if (!argon_permitted_dns_names.empty()) {
    auto russian_root = cert_verifier::mojom::CertWithConstraints::New();
    russian_root->certificate =
        base::ToVector(base::span(net::kTitaniumRussianRootDer));
    russian_root->permitted_dns_names = std::move(argon_permitted_dns_names);
    additional_certificates->trust_anchors_with_additional_constraints.push_back(
        std::move(russian_root));
  }
#endif
  // END TITANIUM_RU_SCOPED_ANCHOR"""
PROFILE_PREF_REGISTRATION_ANCHOR = (
    "  registry->RegisterListPref(prefs::kCAHintCertificates);"
)
PROFILE_PREF_REGISTRATION_BLOCK = """
#if BUILDFLAG(IS_ANDROID)
  // BEGIN ARGON_RU_CA_DOMAIN_PREF_REGISTRATION
  argon::RegisterRussianCaPrefs(registry);
  // END ARGON_RU_CA_DOMAIN_PREF_REGISTRATION
#endif"""
PROFILE_PREF_OBSERVER_ANCHOR = """  pref_change_registrar_.Add(prefs::kCAHintCertificates,
                             schedule_update_cert_policy);"""
PROFILE_PREF_OBSERVER_BLOCK = """
#if BUILDFLAG(IS_ANDROID)
  // BEGIN ARGON_RU_CA_DOMAIN_PREF_OBSERVER
  pref_change_registrar_.Add(argon::prefs::kRussianCaAdditionalDomains,
                             schedule_update_cert_policy);
  for (const char* pref : argon::prefs::kRussianCaBuiltInZonePrefs) {
    pref_change_registrar_.Add(pref, schedule_update_cert_policy);
  }
  // END ARGON_RU_CA_DOMAIN_PREF_OBSERVER
#endif"""
VERIFIER_ANCHOR = "    CheckExtraConstraints(path->certs, &path->errors);"
VERIFIER_BLOCK = """
    // BEGIN TITANIUM_RU_DNS_AND_IP_CONSTRAINTS
#if BUILDFLAG(IS_ANDROID)
    bssl::Span<const std::string> titanium_permitted_dns_names;
    for (const auto& cert_with_constraints : *additional_constraints_) {
      if (cert_with_constraints.certificate &&
          IsTitaniumRussianRoot(*cert_with_constraints.certificate)) {
        titanium_permitted_dns_names =
            bssl::Span<const std::string>(
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
        include + '\n#include "chrome/browser/net/argon_ca_policy.h"',
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
        '\n    "cert/titanium_ru_domain_policy.h",'
        '\n    "cert/titanium_ru_domain_rules.h",',
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
        '\n    "argon_ca_policy.h",'
    )


def patch_chrome_android_gn(text: str) -> str:
    anchor = '  generate_jni("chrome_jni_headers") {\n    sources = ['
    addition = (
        '\n      "//titanium/chromium_src/chrome/android/java/src/org/'
        'chromium/chrome/browser/privacy/settings/'
        'ArgonCertificateDomainsSettings.java",'
    )
    return replace_once(text, anchor, anchor + addition)


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
        '\n  "java/res/layout/argon_certificate_domain_overlap_widget.xml",'
        '\n  "java/res/drawable/argon_certificate_overlap.xml",'
        '\n  "java/res/drawable/argon_certificate_more.xml",'
        '\n  "java/res/drawable/argon_certificate_add.xml",'
        '\n  "java/res/raw/argon_root_ca.pem",'
        '\n  "java/res/values/argon_about_strings.xml",'
        '\n  "java/res/values-ru/argon_about_strings.xml",'
        '\n  "java/res/values-ru/argon_widget_strings.xml",'
    )
    return replace_once(text, anchor, anchor + "\n" + addition)


def patch_titanium_android_cc_sources(text: str) -> str:
    anchor = "android_cc_ext_full_path_sources = ["
    addition = (
        '\n  "//chrome/browser/android/'
        'argon_certificate_domains_settings.cc",'
    )
    return replace_once(text, anchor, anchor + addition)


def patch_titanium_android_cc_deps(text: str) -> str:
    anchor = "android_cc_ext_full_path_deps = ["
    replacement = anchor + """
  "//base",
  "//chrome/browser/net",
  "//chrome/browser/profiles:profile",
  "//components/prefs",
  "//net",
  "//chrome/android:chrome_jni_headers","""
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


def patch_omnibox_gn(text: str) -> str:
    # searchbox_utils.cc includes vector_icons.h on every non-iOS build,
    # including Android with enable_vr=false. Keep location_bar's VR guard.
    anchor = '''  if ((!is_android || enable_vr) && !is_ios) {
    deps += [ ":vector_icons" ]
  }

  if (enable_extensions_core) {'''
    replacement = '''  # searchbox_utils.cc needs these generated icons even without VR.
  if (!is_ios) {
    deps += [ ":vector_icons" ]
  }

  if (enable_extensions_core) {'''
    return replace_once(text, anchor, replacement)


def patch_main_preferences(text: str) -> str:
    return replace_once(
        text, 'android:title="@string/prefs_about_chrome"',
        'android:title="@string/argon_about_title"',
    )


def patch_about_settings(text: str) -> str:
    return replace_once(
        text, 'mPageTitle.set(getString(R.string.prefs_about_chrome));',
        'mPageTitle.set(getString(R.string.argon_about_title));',
    )


def patch_about_preferences(text: str) -> str:
    anchor = '<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android">'
    replacement = '''<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:app="http://schemas.android.com/apk/res-auto">
    <org.chromium.components.browser_ui.settings.ChromeBasePreference
        android:key="argon_project_description"
        android:title="@string/argon_about_project_title"
        android:summary="@string/argon_about_description"
        android:selectable="false" />
    <org.chromium.components.browser_ui.settings.ChromeBasePreferenceCategory
        android:key="argon_project_links"
        android:title="@string/argon_about_links_title">
        <org.chromium.chrome.browser.about_settings.HyperlinkPreference
            android:key="argon_project_github"
            android:title="@string/argon_about_github_title"
            android:summary="@string/argon_about_github_url"
            app:url="@string/argon_about_github_url" />
        <org.chromium.chrome.browser.about_settings.HyperlinkPreference
            android:key="argon_project_issues"
            android:title="@string/argon_about_issues_title"
            android:summary="@string/argon_about_issues_summary"
            app:url="@string/argon_about_issues_url" />
        <org.chromium.chrome.browser.about_settings.HyperlinkPreference
            android:key="argon_project_upstream"
            android:title="@string/argon_about_upstream_title"
            android:summary="@string/argon_about_upstream_url"
            app:url="@string/argon_about_upstream_url" />
    </org.chromium.components.browser_ui.settings.ChromeBasePreferenceCategory>'''
    return replace_once(text, anchor, replacement)


def russian_translation_groups() -> list[dict]:
    return json.loads((ROOT / "res/translations/ru.json").read_text("utf-8"))


def russian_translation_entries(group):
    for name, values in group["messages"].items():
        for value in values if isinstance(values, list) else [values]:
            yield name, value


def russian_browser_name(text: str) -> str:
    # These are upstream services, not products maintained by this fork.
    for product in ("Web Store", "Root Store", "Root Program", "Enterprise Connectors"):
        text = text.replace("Titanium " + product, "Chrome " + product)
    text = text.replace("Titanium OS Flex", "ChromeOS Flex")
    text = re.sub(r"([Ии]нтернет-магазин(?:а|е|ом|у)? )Titanium\b", r"\1Chrome", text)
    return text.replace("Titanium", "Argon")


def presentable_message(message: ET.Element) -> str:
    # GRIT fingerprints placeholder names, not their implementation or examples.
    parts = [message.text or ""]
    for child in message:
        if child.tag != "ph":
            raise ValueError(f"Unexpected message markup: {child.tag}")
        parts.extend((child.attrib["name"].upper(), child.tail or ""))
    text = "".join(parts).strip()
    if text.startswith("'''"):
        text = text[3:]
    if text.endswith("'''"):
        text = text[:-3]
    return text.strip()


def russian_resource_bundles() -> list[dict]:
    return json.loads((ROOT / "res/translations/bundles.json").read_text("utf-8"))


def grit_message_id(text: str, meaning: str = "") -> str:
    # Chromium tools/grit/grit/extern/{FP,tclib}.py: signed MD5 fingerprint,
    # optional meaning, then the low 63 bits. This is a resource ID, not security.
    def fingerprint(value):
        unsigned = int.from_bytes(hashlib.md5(value.encode("utf-8")).digest()[:8], "big")
        return unsigned - (1 << 64) if unsigned & (1 << 63) else unsigned

    value = fingerprint(text)
    if meaning:
        value = fingerprint(meaning) + (value << 1) + (1 if value < 0 else 0)
    return str(value & 0x7fffffffffffffff)


def russian_translation_writes(src: Path) -> dict[Path, str]:
    """Extend Russian XTB catalogs, keeping other locales and existing IDs intact."""
    catalogs = {}
    additions = {}
    for group in russian_translation_groups():
        source = src / group["source"]
        messages = {}
        for message in ET.parse(source).iter("message"):
            messages.setdefault(message.attrib["name"], []).append(message)
        catalog = src / group["catalog"]
        if catalog not in catalogs:
            text = catalog.read_text("utf-8")
            tree = ET.fromstring(text)
            if tree.tag != "translationbundle" or tree.attrib.get("lang") != "ru":
                raise ValueError(f"Expected a Russian XTB catalog: {catalog}")
            catalogs[catalog] = text
            additions[catalog] = {
                element.attrib["id"]: element for element in tree
            }
        for name, translation in russian_translation_entries(group):
            candidates = messages.get(name, [])
            if not candidates:
                raise ValueError(f"Missing source message: {name}")
            candidates = [candidate for candidate in candidates if (
                presentable_message(candidate), candidate.attrib.get("meaning", "")
            ) == (translation["english"], translation["meaning"])]
            if not candidates:
                raise ValueError(f"Russian translation source changed: {name}")
            message = candidates[0]
            english = presentable_message(message)
            meaning = message.attrib.get("meaning", "")
            identifier = grit_message_id(english, meaning)
            element = ET.fromstring(
                f'<translation id="{identifier}">{translation["russian"]}</translation>'
            )
            if any(child.tag != "ph" or set(child.attrib) != {"name"}
                   or len(child) or child.text for child in element):
                raise ValueError(f"Invalid translation markup: {name}")
            if Counter(child.attrib["name"] for child in element) != Counter(
                child.attrib["name"] for child in message
            ):
                raise ValueError(f"Russian translation placeholders differ: {name}")
            existing = additions[catalog].get(identifier)
            if existing is not None and presentable_message(existing) != presentable_message(element):
                raise ValueError(f"Conflicting Russian translation: {name}")
            additions[catalog][identifier] = element
    # Vanadium's branding patches replace Chrome/Chromium in both languages,
    # but retain XTB IDs computed from the original English. Titanium's second
    # rename changes the IDs again. Reconnect those existing Russian messages.
    for bundle in russian_resource_bundles():
        catalog = src / bundle["catalog"]
        if catalog not in catalogs:
            catalogs[catalog] = catalog.read_text("utf-8")
            tree = ET.fromstring(catalogs[catalog])
            if tree.tag != "translationbundle" or tree.attrib.get("lang") != "ru":
                raise ValueError(f"Expected a Russian XTB catalog: {catalog}")
            additions[catalog] = {element.attrib["id"]: element for element in tree}
        for source in bundle["sources"]:
            for message in ET.parse(src / source).iter("message"):
                if (message.attrib.get("translateable") == "false"
                        or message.attrib.get("use_name_for_id") == "true"):
                    continue
                english = presentable_message(message)
                if "Titanium" not in english:
                    continue
                meaning = message.attrib.get("meaning", "")
                identifier = grit_message_id(english, meaning)
                if identifier in additions[catalog]:
                    continue
                candidates = [additions[catalog][old_id] for brand in (
                    "Chrome", "Chromium", "Google Chrome"
                ) if (old_id := grit_message_id(
                    english.replace("Titanium", brand), meaning
                )) in additions[catalog]]
                if not candidates:
                    continue
                if len({presentable_message(item) for item in candidates}) != 1:
                    raise ValueError(
                        f'Ambiguous branding translation: {message.attrib["name"]}'
                    )
                element = copy.deepcopy(candidates[0])
                element.attrib["id"] = identifier
                element.tail = None
                for child in element.iter():
                    if child.text:
                        child.text = russian_browser_name(child.text)
                    if child.tail:
                        child.tail = russian_browser_name(child.tail)
                additions[catalog][identifier] = element
    writes = {}
    for catalog, text in catalogs.items():
        existing = {element.attrib["id"] for element in ET.fromstring(text)}
        lines = ["  " + ET.tostring(element, encoding="unicode")
                 for identifier, element in additions[catalog].items()
                 if identifier not in existing]
        if text.count("</translationbundle>") != 1:
            raise ValueError(f"Expected one translation bundle: {catalog}")
        block = "\n".join(lines) + "\n" if lines else ""
        writes[catalog] = text.replace("</translationbundle>", block + "</translationbundle>")
    return writes


def apply(src: Path, product_name: bool = True) -> None:
    der = load_root()
    generated = ROOT / "chromium_overlay/net/cert/titanium_ru_root.h"
    if generated.read_text() != root_header(der):
        raise ValueError("Generated CA header differs from the pinned certificate")
    # Validate all inputs first. Never silently continue after a source change.
    writes = russian_translation_writes(src)
    writes.update({
        # Details shown by Android come from the exact PEM checked by load_root().
        src / TITANIUM_ROOT_RESOURCE: (
            ROOT / "certificates/russian_trusted_root_ca.pem"
        ).read_text("ascii"),
        src / PROFILE: patch_profile((src / PROFILE).read_text()),
        src / VERIFIER: patch_verifier((src / VERIFIER).read_text()),
        src / GN: patch_gn((src / GN).read_text()),
        src / CHROME_NET_GN: patch_chrome_net_gn(
            (src / CHROME_NET_GN).read_text()
        ),
        src / CHROME_ANDROID_GN: patch_chrome_android_gn(
            (src / CHROME_ANDROID_GN).read_text()
        ),
        src / OMNIBOX_GN: patch_omnibox_gn((src / OMNIBOX_GN).read_text()),
        src / MAIN_PREFERENCES: patch_main_preferences(
            (src / MAIN_PREFERENCES).read_text()
        ),
        src / ABOUT_PREFERENCES: patch_about_preferences(
            (src / ABOUT_PREFERENCES).read_text()
        ),
        src / ABOUT_SETTINGS: patch_about_settings(
            (src / ABOUT_SETTINGS).read_text()
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
    })
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
