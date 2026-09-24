// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include <algorithm>
#include <string>
#include <vector>

#include "base/android/jni_string.h"
#include "base/values.h"
#include "chrome/android/chrome_jni_headers/ArgonCertificateDomainsSettings_jni.h"
#include "chrome/browser/net/argon_ca_prefs.h"
#include "chrome/browser/profiles/profile.h"
#include "components/prefs/pref_service.h"
#include "components/prefs/scoped_user_pref_update.h"
#include "net/cert/titanium_ru_domain_policy.h"

namespace {

enum AddDomainResult {
  kSuccess = 0,
  kInvalid = 1,
  kPublicSuffix = 2,
  kCoveredByBuiltIn = 3,
  kDuplicate = 4,
  kTooMany = 5,
};

std::vector<std::string> GetNormalizedDomains(Profile* profile) {
  std::vector<std::string> domains;
  for (const base::Value& value : profile->GetPrefs()->GetList(
           argon::prefs::kRussianCaAdditionalDomains)) {
    if (!value.is_string()) {
      continue;
    }
    std::string normalized;
    if (net::NormalizeTitaniumRussianRootDomain(value.GetString(),
                                                 &normalized) !=
        net::TitaniumRuDomainValidationResult::kValid) {
      continue;
    }
    if (std::find(domains.begin(), domains.end(), normalized) ==
        domains.end()) {
      domains.push_back(std::move(normalized));
    }
    if (domains.size() == net::kTitaniumRussianRootMaxUserDomains) {
      break;
    }
  }
  std::sort(domains.begin(), domains.end());
  return domains;
}

void WriteDomains(Profile* profile, const std::vector<std::string>& domains) {
  ScopedListPrefUpdate update(
      profile->GetPrefs(), argon::prefs::kRussianCaAdditionalDomains);
  update->clear();
  for (const std::string& domain : domains) {
    update->Append(domain);
  }
}

static std::vector<std::string>
JNI_ArgonCertificateDomainsSettings_GetDomains(JNIEnv* env,
                                                Profile* profile) {
  return GetNormalizedDomains(profile);
}

static jint JNI_ArgonCertificateDomainsSettings_AddDomain(
    JNIEnv* env,
    Profile* profile,
    const std::string& input) {
  std::string normalized;
  const auto validation =
      net::NormalizeTitaniumRussianRootDomain(input, &normalized);
  switch (validation) {
    case net::TitaniumRuDomainValidationResult::kInvalid:
      return kInvalid;
    case net::TitaniumRuDomainValidationResult::kPublicSuffix:
      return kPublicSuffix;
    case net::TitaniumRuDomainValidationResult::kCoveredByBuiltIn:
      return kCoveredByBuiltIn;
    case net::TitaniumRuDomainValidationResult::kValid:
      break;
  }

  std::vector<std::string> domains = GetNormalizedDomains(profile);
  if (std::find(domains.begin(), domains.end(), normalized) != domains.end()) {
    return kDuplicate;
  }
  if (domains.size() >= net::kTitaniumRussianRootMaxUserDomains) {
    return kTooMany;
  }
  domains.push_back(std::move(normalized));
  std::sort(domains.begin(), domains.end());
  WriteDomains(profile, domains);
  return kSuccess;
}

static jboolean JNI_ArgonCertificateDomainsSettings_RemoveDomain(
    JNIEnv* env,
    Profile* profile,
    const std::string& input) {
  std::string normalized;
  if (net::NormalizeTitaniumRussianRootDomain(input, &normalized) !=
      net::TitaniumRuDomainValidationResult::kValid) {
    return false;
  }
  std::vector<std::string> domains = GetNormalizedDomains(profile);
  const auto it = std::find(domains.begin(), domains.end(), normalized);
  if (it == domains.end()) {
    return false;
  }
  domains.erase(it);
  WriteDomains(profile, domains);
  return true;
}

}  // namespace

DEFINE_JNI(ArgonCertificateDomainsSettings)
