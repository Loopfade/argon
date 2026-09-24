// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef NET_CERT_TITANIUM_RU_DOMAIN_POLICY_H_
#define NET_CERT_TITANIUM_RU_DOMAIN_POLICY_H_

#include <cstddef>
#include <string>
#include <string_view>

#include "base/strings/strcat.h"
#include "base/strings/string_util.h"
#include "net/base/registry_controlled_domains/registry_controlled_domain.h"
#include "url/gurl.h"

namespace net {

inline constexpr size_t kTitaniumRussianRootMaxUserDomains = 100;

enum class TitaniumRuDomainValidationResult {
  kValid = 0,
  kInvalid = 1,
  kPublicSuffix = 2,
  kCoveredByBuiltIn = 3,
};

inline bool IsTitaniumRussianRootBuiltInDomain(std::string_view host) {
  for (std::string_view suffix : {"ru", "xn--p1ai", "su"}) {
    if (host == suffix ||
        (host.size() > suffix.size() && host.ends_with(suffix) &&
         host[host.size() - suffix.size() - 1] == '.')) {
      return true;
    }
  }
  return false;
}

inline bool IsValidTitaniumRussianRootDnsHostname(std::string_view host) {
  size_t label_start = 0;
  while (label_start < host.size()) {
    const size_t dot = host.find('.', label_start);
    const size_t label_end =
        dot == std::string_view::npos ? host.size() : dot;
    const size_t label_length = label_end - label_start;
    if (label_length == 0 || label_length > 63 || host[label_start] == '-' ||
        host[label_end - 1] == '-') {
      return false;
    }
    for (size_t i = label_start; i < label_end; ++i) {
      const char c = host[i];
      if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-')) {
        return false;
      }
    }
    if (dot == std::string_view::npos) {
      return true;
    }
    label_start = dot + 1;
  }
  return false;
}

// Produces a lower-case ASCII (A-label) hostname suitable for a BoringSSL DNS
// name constraint. A hostname without a leading dot permits the exact name and
// its subdomains. Public and private registry suffixes are rejected.
inline TitaniumRuDomainValidationResult NormalizeTitaniumRussianRootDomain(
    std::string_view input,
    std::string* normalized) {
  normalized->clear();
  std::string candidate(base::TrimWhitespaceASCII(input, base::TRIM_ALL));
  if (candidate.empty() || candidate.size() > 253 || candidate.front() == '.' ||
      candidate.find_first_of("*/\\:@?#[]%") != std::string::npos) {
    return TitaniumRuDomainValidationResult::kInvalid;
  }
  if (candidate.back() == '.') {
    candidate.pop_back();
  }
  if (candidate.empty() || candidate.front() == '.' || candidate.back() == '.') {
    return TitaniumRuDomainValidationResult::kInvalid;
  }

  const GURL url(base::StrCat({"https://", candidate, "/"}));
  if (!url.is_valid() || !url.SchemeIs("https") || !url.has_host() ||
      url.HostIsIPAddress() || !url.port().empty() || url.path() != "/" ||
      url.has_query() || url.has_ref()) {
    return TitaniumRuDomainValidationResult::kInvalid;
  }

  const std::string host(url.host());
  if (host.empty() || host.size() > 253 ||
      !IsValidTitaniumRussianRootDnsHostname(host)) {
    return TitaniumRuDomainValidationResult::kInvalid;
  }
  if (IsTitaniumRussianRootBuiltInDomain(host)) {
    return TitaniumRuDomainValidationResult::kCoveredByBuiltIn;
  }

  namespace registries = registry_controlled_domains;
  if (registries::HostIsRegistryIdentifier(
          host, registries::INCLUDE_PRIVATE_REGISTRIES)) {
    return TitaniumRuDomainValidationResult::kPublicSuffix;
  }
  if (!registries::HostHasRegistryControlledDomain(
          host, registries::EXCLUDE_UNKNOWN_REGISTRIES,
          registries::INCLUDE_PRIVATE_REGISTRIES)) {
    return TitaniumRuDomainValidationResult::kInvalid;
  }

  *normalized = host;
  return TitaniumRuDomainValidationResult::kValid;
}

}  // namespace net

#endif  // NET_CERT_TITANIUM_RU_DOMAIN_POLICY_H_
