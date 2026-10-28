// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef CHROME_BROWSER_NET_ARGON_CA_PREFS_H_
#define CHROME_BROWSER_NET_ARGON_CA_PREFS_H_

#include <array>

namespace argon::prefs {

inline constexpr char kRussianCaAdditionalDomains[] =
    "argon.russian_ca.additional_domains";
// Same order as net::kTitaniumRussianRootBuiltInZones. Each switch defaults to
// true, preserving existing installations without rewriting their domain list.
inline constexpr std::array<const char*, 3> kRussianCaBuiltInZonePrefs = {
    "argon.russian_ca.trust_ru", "argon.russian_ca.trust_rf",
    "argon.russian_ca.trust_su"};

}  // namespace argon::prefs

#endif  // CHROME_BROWSER_NET_ARGON_CA_PREFS_H_
