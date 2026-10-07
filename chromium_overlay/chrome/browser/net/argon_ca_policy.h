// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef CHROME_BROWSER_NET_ARGON_CA_POLICY_H_
#define CHROME_BROWSER_NET_ARGON_CA_POLICY_H_

#include <algorithm>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "base/values.h"
#include "chrome/browser/net/argon_ca_prefs.h"
#include "components/prefs/pref_registry_simple.h"
#include "components/prefs/pref_service.h"
#include "components/prefs/scoped_user_pref_update.h"
#include "net/cert/titanium_ru_domain_policy.h"

namespace argon {

enum class AddRussianCaDomainResult {
  kSuccess = 0,
  kInvalid = 1,
  kPublicSuffix = 2,
  kCovered = 3,
  kDuplicate = 4,
  kTooMany = 5,
};

inline void RegisterRussianCaPrefs(PrefRegistrySimple* registry) {
  registry->RegisterListPref(prefs::kRussianCaAdditionalDomains);
  for (const char* pref : prefs::kRussianCaBuiltInZonePrefs) {
    registry->RegisterBooleanPref(pref, true);
  }
}

inline net::TitaniumRuBuiltInZoneStates GetRussianCaBuiltInZoneStates(
    const PrefService* pref_service) {
  net::TitaniumRuBuiltInZoneStates states;
  static_assert(net::kTitaniumRussianRootBuiltInZones.size() ==
                prefs::kRussianCaBuiltInZonePrefs.size());
  for (size_t i = 0; i < states.size(); ++i) {
    states[i] = pref_service->GetBoolean(prefs::kRussianCaBuiltInZonePrefs[i]);
  }
  return states;
}

// Reading and removing saved entries must not depend on the active zone
// switches. Otherwise a covered entry can disappear and be lost on a write.
inline std::vector<std::string> GetRussianCaAdditionalDomains(
    const PrefService* pref_service) {
  std::vector<std::string> domains;
  for (const base::Value& value :
       pref_service->GetList(prefs::kRussianCaAdditionalDomains)) {
    if (!value.is_string()) {
      continue;
    }
    std::string normalized;
    if (net::NormalizeTitaniumRussianRootDomain(value.GetString(),
                                                &normalized) !=
        net::TitaniumRuDomainValidationResult::kValid) {
      continue;
    }
    if (std::find(domains.begin(), domains.end(), normalized) == domains.end()) {
      domains.push_back(std::move(normalized));
    }
    if (domains.size() == net::kTitaniumRussianRootMaxUserDomains) {
      break;
    }
  }
  std::sort(domains.begin(), domains.end());
  return domains;
}

inline std::vector<std::string> GetRussianCaPermittedDnsNames(
    const PrefService* pref_service) {
  return net::GetTitaniumRussianRootPermittedDnsNames(
      GetRussianCaBuiltInZoneStates(pref_service),
      GetRussianCaAdditionalDomains(pref_service));
}

inline std::vector<std::string> GetRussianCaCoveringRules(
    const PrefService* pref_service,
    std::string_view input) {
  std::string normalized;
  if (net::NormalizeTitaniumRussianRootDomain(input, &normalized) !=
      net::TitaniumRuDomainValidationResult::kValid) {
    return {};
  }
  return net::GetTitaniumRussianRootCoveringRules(
      normalized, GetRussianCaBuiltInZoneStates(pref_service),
      GetRussianCaAdditionalDomains(pref_service));
}

inline void WriteRussianCaDomains(PrefService* pref_service,
                                 const std::vector<std::string>& domains) {
  ScopedListPrefUpdate update(pref_service, prefs::kRussianCaAdditionalDomains);
  update->clear();
  for (const std::string& domain : domains) {
    update->Append(domain);
  }
}

inline AddRussianCaDomainResult AddRussianCaDomain(PrefService* pref_service,
                                                  std::string_view input) {
  std::string normalized;
  switch (net::NormalizeTitaniumRussianRootDomain(input, &normalized)) {
    case net::TitaniumRuDomainValidationResult::kInvalid:
      return AddRussianCaDomainResult::kInvalid;
    case net::TitaniumRuDomainValidationResult::kPublicSuffix:
      return AddRussianCaDomainResult::kPublicSuffix;
    case net::TitaniumRuDomainValidationResult::kValid:
      break;
  }
  std::vector<std::string> domains = GetRussianCaAdditionalDomains(pref_service);
  if (std::find(domains.begin(), domains.end(), normalized) != domains.end()) {
    return AddRussianCaDomainResult::kDuplicate;
  }
  if (!net::GetTitaniumRussianRootCoveringRules(
           normalized, GetRussianCaBuiltInZoneStates(pref_service), domains)
           .empty()) {
    return AddRussianCaDomainResult::kCovered;
  }
  if (domains.size() >= net::kTitaniumRussianRootMaxUserDomains) {
    return AddRussianCaDomainResult::kTooMany;
  }
  domains.push_back(std::move(normalized));
  std::sort(domains.begin(), domains.end());
  WriteRussianCaDomains(pref_service, domains);
  return AddRussianCaDomainResult::kSuccess;
}

inline bool RemoveRussianCaDomain(PrefService* pref_service,
                                  std::string_view input) {
  std::string normalized;
  if (net::NormalizeTitaniumRussianRootDomain(input, &normalized) !=
      net::TitaniumRuDomainValidationResult::kValid) {
    return false;
  }
  std::vector<std::string> domains = GetRussianCaAdditionalDomains(pref_service);
  const auto it = std::find(domains.begin(), domains.end(), normalized);
  if (it == domains.end()) {
    return false;
  }
  domains.erase(it);
  WriteRussianCaDomains(pref_service, domains);
  return true;
}

}  // namespace argon

#endif  // CHROME_BROWSER_NET_ARGON_CA_POLICY_H_
