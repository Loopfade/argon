// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef NET_CERT_TITANIUM_RU_DOMAIN_RULES_H_
#define NET_CERT_TITANIUM_RU_DOMAIN_RULES_H_

#include <array>
#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace net {

inline constexpr size_t kTitaniumRussianRootMaxUserDomains = 100;
inline constexpr std::array<std::string_view, 3>
    kTitaniumRussianRootBuiltInZones = {".ru", ".xn--p1ai", ".su"};
using TitaniumRuBuiltInZoneStates =
    std::array<bool, kTitaniumRussianRootBuiltInZones.size()>;

// Arguments are normalized ASCII names. A zone has a leading dot and covers
// only names below that zone; an ordinary rule also covers its exact name.
inline bool IsTitaniumRussianRootDomainCoveredBy(
    std::string_view host,
    std::string_view rule) {
  const bool is_zone = rule.starts_with('.');
  if (is_zone) {
    rule.remove_prefix(1);
  }
  return !rule.empty() &&
         ((!is_zone && host == rule) ||
          (host.size() > rule.size() && host.ends_with(rule) &&
           host[host.size() - rule.size() - 1] == '.'));
}

// A saved domain is not its own overlap. Return every other covering rule so
// the UI can explain which rules must change before the entry is independent.
inline std::vector<std::string> GetTitaniumRussianRootCoveringRules(
    std::string_view host,
    const TitaniumRuBuiltInZoneStates& enabled_zones,
    const std::vector<std::string>& user_domains) {
  std::vector<std::string> rules;
  for (size_t i = 0; i < enabled_zones.size(); ++i) {
    if (enabled_zones[i] && IsTitaniumRussianRootDomainCoveredBy(
                                host, kTitaniumRussianRootBuiltInZones[i])) {
      rules.emplace_back(kTitaniumRussianRootBuiltInZones[i]);
    }
  }
  for (const std::string& domain : user_domains) {
    if (host != domain &&
        IsTitaniumRussianRootDomainCoveredBy(host, domain)) {
      rules.push_back(domain);
    }
  }
  return rules;
}

inline std::vector<std::string> GetTitaniumRussianRootPermittedDnsNames(
    const TitaniumRuBuiltInZoneStates& enabled_zones,
    const std::vector<std::string>& user_domains) {
  std::vector<std::string> names;
  for (size_t i = 0; i < enabled_zones.size(); ++i) {
    if (enabled_zones[i]) {
      names.emplace_back(kTitaniumRussianRootBuiltInZones[i]);
    }
  }
  size_t user_count = 0;
  for (const std::string& domain : user_domains) {
    if (user_count == kTitaniumRussianRootMaxUserDomains) {
      break;
    }
    names.push_back(domain);
    ++user_count;
  }
  return names;
}

}  // namespace net

#endif  // NET_CERT_TITANIUM_RU_DOMAIN_RULES_H_
