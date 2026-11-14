// Copyright 2026 Titanium RU contributors
// SPDX-License-Identifier: GPL-2.0-only

#ifndef NET_CERT_TITANIUM_RU_CONSTRAINTS_H_
#define NET_CERT_TITANIUM_RU_CONSTRAINTS_H_

#include <memory>
#include <string>
#include <string_view>
#include <utility>

#include <openssl/pool.h>
#include <openssl/span.h>

#include "net/cert/titanium_ru_root.h"
#include "third_party/boringssl/src/pki/cert_errors.h"
#include "third_party/boringssl/src/pki/common_cert_errors.h"
#include "third_party/boringssl/src/pki/name_constraints.h"
#include "third_party/boringssl/src/pki/parsed_certificate.h"

namespace net {

inline bool IsTitaniumRussianRoot(const CRYPTO_BUFFER* certificate) {
  return certificate &&
         bssl::der::Input(CRYPTO_BUFFER_data(certificate),
                          CRYPTO_BUFFER_len(certificate)) ==
         bssl::der::Input(kTitaniumRussianRootDer,
                          sizeof(kTitaniumRussianRootDer));
}

inline bool IsTitaniumRussianRoot(const bssl::ParsedCertificate& certificate) {
  return IsTitaniumRussianRoot(certificate.cert_buffer());
}

// HTTP/2 and HTTP/3 pooling does not run certificate verification for the new
// hostname. Require a separate handshake for this host-scoped trust anchor.
inline bool CanPoolTitaniumRussianRootConnection(
    const CRYPTO_BUFFER* verified_root,
    std::string_view old_hostname,
    std::string_view new_hostname) {
  return old_hostname == new_hostname || !IsTitaniumRussianRoot(verified_root);
}

inline std::unique_ptr<bssl::NameConstraints>
CreateTitaniumRussianRootConstraints(
    bssl::Span<const std::string> permitted_dns_names) {
  bssl::GeneralNames permitted;
  for (const std::string& domain : permitted_dns_names) {
    permitted.dns_names.push_back(domain);
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
// The rule scopes trust to the requested hostname, not every SAN in a shared
// certificate. Chromium still verifies that this hostname matches the leaf,
// and checks signatures, validity, revocation and certificate name constraints.
inline void CheckTitaniumRussianRootConstraints(
    const bssl::ParsedCertificateList& certificates,
    bssl::CertPathErrors* errors,
    std::string_view hostname,
    bssl::Span<const std::string> permitted_dns_names) {
  if (certificates.empty() || !IsTitaniumRussianRoot(*certificates.back())) {
    return;
  }
  // Empty means disabled, including when the exact root is installed in the
  // Android trust store. DNS rules never authorize an IP-literal connection.
  if (permitted_dns_names.empty() || hostname.empty() ||
      hostname.find_first_of("*:") != std::string_view::npos ||
      !CreateTitaniumRussianRootConstraints(permitted_dns_names)
           ->IsPermittedDNSName(hostname)) {
    errors->GetErrorsForCert(0)->AddError(
        bssl::cert_errors::kNotPermittedByNameConstraints);
  }
}

}  // namespace net

#endif  // NET_CERT_TITANIUM_RU_CONSTRAINTS_H_
