// Copyright 2026 Titanium RU contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef NET_CERT_TITANIUM_RU_CONSTRAINTS_H_
#define NET_CERT_TITANIUM_RU_CONSTRAINTS_H_

#include <memory>
#include <string>
#include <utility>

#include "base/containers/span.h"
#include "net/cert/titanium_ru_root.h"
#include "third_party/boringssl/src/pki/cert_errors.h"
#include "third_party/boringssl/src/pki/name_constraints.h"
#include "third_party/boringssl/src/pki/parsed_certificate.h"

namespace net {

inline bool IsTitaniumRussianRoot(const bssl::ParsedCertificate& certificate) {
  return certificate.der_cert() ==
         bssl::der::Input(kTitaniumRussianRootDer,
                          sizeof(kTitaniumRussianRootDer));
}

inline std::unique_ptr<bssl::NameConstraints>
CreateTitaniumRussianRootConstraints(
    base::span<const std::string> permitted_dns_names = {}) {
  bssl::GeneralNames permitted;
  if (permitted_dns_names.empty()) {
    permitted.dns_names = {".ru", ".xn--p1ai", ".su"};
  } else {
    for (const std::string& domain : permitted_dns_names) {
      permitted.dns_names.push_back(domain);
    }
  }
  // An explicitly constrained IP name type with ZERO permitted ranges rejects
  // every IPv4 and IPv6 SAN, including 0.0.0.0 and ::. Do not use dummy CIDRs:
  // even a /32 or /128 would permit one IP address.
  permitted.present_name_types = bssl::GENERAL_NAME_DNS_NAME |
                                 bssl::GENERAL_NAME_IP_ADDRESS;
  return bssl::NameConstraints::CreateFromPermittedSubtrees(std::move(permitted));
}

// Called for each candidate path before Chromium accepts it. This also applies
// if the same pinned root was independently installed in Android's trust store.
// Ordinary roots and Chromium's remaining certificate checks are untouched.
inline void CheckTitaniumRussianRootConstraints(
    const bssl::ParsedCertificateList& certificates,
    bssl::CertPathErrors* errors,
    base::span<const std::string> permitted_dns_names = {}) {
  if (certificates.empty() || !IsTitaniumRussianRoot(*certificates.back())) {
    return;
  }
  auto constraints =
      CreateTitaniumRussianRootConstraints(permitted_dns_names);
  const auto& leaf = certificates.front();
  constraints->IsPermittedCert(leaf->normalized_subject(),
                               leaf->subject_alt_names(),
                               errors->GetErrorsForCert(0));
}

}  // namespace net

#endif  // NET_CERT_TITANIUM_RU_CONSTRAINTS_H_
