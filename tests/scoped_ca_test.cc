// Exercise the production policy with Chromium's pinned BoringSSL, not a
// Python reimplementation of domain matching.
#include <array>
#include <cstdlib>
#include <iostream>
#include <vector>

#include <openssl/pool.h>
#include "net/cert/titanium_ru_constraints.h"
#include "net/cert/titanium_ru_domain_rules.h"
#include "third_party/boringssl/src/pki/cert_errors.h"

namespace net {
namespace x509_util {
// Adapter for Chromium's CRYPTO_BUFFER equality helper used by the unchanged
// generic constraint code. All name matching uses the real BoringSSL library.
bool CryptoBufferEqual(const CRYPTO_BUFFER* a, const CRYPTO_BUFFER* b) {
  return bssl::der::Input(CRYPTO_BUFFER_data(a), CRYPTO_BUFFER_len(a)) ==
         bssl::der::Input(CRYPTO_BUFFER_data(b), CRYPTO_BUFFER_len(b));
}
}  // namespace x509_util

struct TestIPAddress {
  std::array<uint8_t, 4> value{};
  bssl::Span<const uint8_t> bytes() const { return value; }
};
struct TestCIDR {
  TestIPAddress ip;
  TestIPAddress mask;
};
struct TestCertificateWithConstraints {
  std::shared_ptr<const bssl::ParsedCertificate> certificate;
  std::vector<std::string> permitted_dns_names;
  std::vector<TestCIDR> permitted_cidrs;
};

struct TestPathBuilderDelegate {
  const std::vector<TestCertificateWithConstraints>* additional_constraints_;
  std::string_view hostname_;
// Compile the production Android patch inside the pinned Chromium method.
#define BUILDFLAG(flag) 1
#include "scoped_ca_verifier.inc"
#undef BUILDFLAG
};
}  // namespace net

namespace {
#include "scoped_ca_leaf.inc"
int checks = 0;
const std::vector<std::string> kDefaultZones = {".ru", ".xn--p1ai", ".su"};
void Expect(bool value, const char* name) {
  ++checks;
  if (!value) {
    std::cerr << "FAIL: " << name << '\n';
    std::exit(1);
  }
}
bool Permits(const bssl::GeneralNames& names) {
  bssl::CertErrors errors;
  net::CreateTitaniumRussianRootConstraints(kDefaultZones)->IsPermittedCert(
      bssl::der::Input(), &names, &errors);
  return !errors.ContainsAnyErrorWithSeverity(bssl::CertError::SEVERITY_HIGH);
}

bool PermitsCandidate(
    const bssl::ParsedCertificateList& path,
    std::string_view hostname,
    const std::vector<net::TestCertificateWithConstraints>& constraints) {
  net::TestPathBuilderDelegate delegate{&constraints, hostname};
  bssl::CertPathErrors errors;
  delegate.CheckExtraConstraints(path, &errors);
  return !errors.ContainsHighSeverityErrors();
}
}  // namespace

int main() {
  auto policy = net::CreateTitaniumRussianRootConstraints(kDefaultZones);
  for (const char* host : {"bank.ru", "pay.bank.ru", "BANK.RU", "archive.su",
                           "xn--e1afmkfd.xn--p1ai", "sub.xn--e1afmkfd.xn--p1ai"}) {
    Expect(policy->IsPermittedDNSName(host), host);
  }
  for (const char* host : {"ru", "su", "xn--p1ai", "example.com", "example.org",
                           "bank.ru.example.com", "bank.su.attacker.net",
                           "bank.xn--p1ai.example.com", "notru", "bank.rf"}) {
    Expect(!policy->IsPermittedDNSName(host), host);
  }

  std::vector<std::string> dynamic_names = {
      ".ru", ".xn--p1ai", ".su", "example.com", "service.example.net"};
  auto dynamic_policy =
      net::CreateTitaniumRussianRootConstraints(dynamic_names);
  for (const char* host : {"example.com", "www.example.com",
                           "service.example.net", "api.service.example.net"}) {
    Expect(dynamic_policy->IsPermittedDNSName(host), host);
  }
  for (const char* host : {"com", "net", "notexample.com",
                           "example.com.attacker.net", "example.net"}) {
    Expect(!dynamic_policy->IsPermittedDNSName(host), host);
  }
  for (unsigned mask = 0; mask < 8; ++mask) {
    const net::TitaniumRuBuiltInZoneStates enabled = {
        (mask & 1) != 0, (mask & 2) != 0, (mask & 4) != 0};
    const auto names = net::GetTitaniumRussianRootPermittedDnsNames(enabled, {});
    auto switched_policy = net::CreateTitaniumRussianRootConstraints(names);
    Expect(switched_policy->IsPermittedDNSName("bank.ru") == enabled[0], ".ru switch");
    Expect(switched_policy->IsPermittedDNSName("site.xn--p1ai") == enabled[1], ".rf switch");
    Expect(switched_policy->IsPermittedDNSName("archive.su") == enabled[2], ".su switch");
    Expect(!switched_policy->IsPermittedDNSName("example.com"), "no implicit .com trust");
  }
  const auto disabled_policy = net::CreateTitaniumRussianRootConstraints({});
  Expect(!disabled_policy->IsPermittedDNSName("bank.ru"), "empty policy rejects .ru");
  Expect(!disabled_policy->IsPermittedDNSName("example.com"), "empty policy rejects .com");
  const std::vector<std::string> own_domains = {"bank.ru"};
  const auto own_policy = net::CreateTitaniumRussianRootConstraints(own_domains);
  Expect(own_policy->IsPermittedDNSName("bank.ru"), "own .ru domain");
  Expect(own_policy->IsPermittedDNSName("pay.bank.ru"), "own .ru subdomain");
  Expect(!own_policy->IsPermittedDNSName("other.ru"), "whole .ru remains disabled");
  for (const std::array<uint8_t, 4>& ip : {
           std::array<uint8_t, 4>{0, 0, 0, 0}, {127, 0, 0, 1},
           {10, 0, 0, 1}, {192, 0, 2, 1}, {255, 255, 255, 255}}) {
    Expect(!policy->IsPermittedIP(bssl::der::Input(ip.data(), ip.size())), "IPv4");
  }
  std::array<uint8_t, 16> ip6{};
  Expect(!policy->IsPermittedIP(bssl::der::Input(ip6.data(), ip6.size())), "IPv6 ::");
  ip6[15] = 1;
  Expect(!policy->IsPermittedIP(bssl::der::Input(ip6.data(), ip6.size())), "IPv6 ::1");
  ip6[0] = 0x20;
  ip6[1] = 0x01;
  Expect(!policy->IsPermittedIP(bssl::der::Input(ip6.data(), ip6.size())), "IPv6 public");

  bssl::GeneralNames names;
  names.present_name_types = bssl::GENERAL_NAME_DNS_NAME;
  names.dns_names = {"bank.ru", "other.su", "xn--e1afmkfd.xn--p1ai"};
  Expect(Permits(names), "all allowed SANs");
  names.dns_names.push_back("example.com");
  Expect(!Permits(names), "mixed allowed and disallowed DNS SANs");
  names.dns_names = {"bank.ru"};
  names.present_name_types |= bssl::GENERAL_NAME_IP_ADDRESS;
  names.ip_addresses.push_back(bssl::der::Input(ip6.data(), ip6.size()));
  Expect(!Permits(names), "allowed DNS plus IP SAN");
  names.dns_names.clear();
  names.present_name_types = bssl::GENERAL_NAME_IP_ADDRESS;
  Expect(!Permits(names), "IP-only SAN");

  auto buffer = bssl::UniquePtr<CRYPTO_BUFFER>(CRYPTO_BUFFER_new(
      net::kTitaniumRussianRootDer, sizeof(net::kTitaniumRussianRootDer), nullptr));
  auto root = bssl::ParsedCertificate::Create(std::move(buffer), {}, nullptr);
  Expect(root != nullptr, "pinned certificate parses");
  Expect(net::IsTitaniumRussianRoot(*root), "recognize exact root");
  bssl::CertPathErrors disabled_errors;
  net::CheckTitaniumRussianRootConstraints({root}, &disabled_errors, "bank.ru", {});
  Expect(disabled_errors.ContainsHighSeverityErrors(),
         "disabled pinned root rejected even without DNS/IP SANs");
  std::vector<uint8_t> changed(std::begin(net::kTitaniumRussianRootDer),
                               std::end(net::kTitaniumRussianRootDer));
  changed.back() ^= 1;
  auto other = bssl::ParsedCertificate::Create(
      bssl::UniquePtr<CRYPTO_BUFFER>(CRYPTO_BUFFER_new(changed.data(), changed.size(), nullptr)),
      {}, nullptr);
  Expect(other != nullptr, "changed certificate parses");
  Expect(!net::IsTitaniumRussianRoot(*other), "same subject is not the pinned root");
  bssl::CertPathErrors ordinary_errors;
  net::CheckTitaniumRussianRootConstraints({other}, &ordinary_errors, "bank.ru", {});
  Expect(!ordinary_errors.ContainsHighSeverityErrors(), "ordinary root unaffected");

  auto leaf = bssl::ParsedCertificate::Create(
      bssl::UniquePtr<CRYPTO_BUFFER>(CRYPTO_BUFFER_new(
          kMultiSanLeafDer, sizeof(kMultiSanLeafDer), nullptr)), {}, nullptr);
  Expect(leaf != nullptr, "multi-SAN regression certificate parses");
  Expect(leaf->subject_alt_names()->dns_names.size() == 3,
         "regression certificate contains three DNS SANs");
  Expect(leaf->subject_alt_names()->ip_addresses.size() == 1,
         "regression certificate contains an unrelated IP SAN");
  bssl::CertErrors old_errors;
  net::CreateTitaniumRussianRootConstraints(own_domains)->IsPermittedCert(
      leaf->normalized_subject(), leaf->subject_alt_names(), &old_errors);
  Expect(old_errors.ContainsAnyErrorWithSeverity(bssl::CertError::SEVERITY_HIGH),
         "old all-SAN restriction reproduces the failure for an allowed site");

  const bssl::ParsedCertificateList path{leaf, root};
  const std::vector<net::TestCertificateWithConstraints> scoped = {
      {root, own_domains, {}}};
  for (const char* host : {"bank.ru", "www.bank.ru", "BANK.RU."}) {
    Expect(PermitsCandidate(path, host, scoped),
           "requested hostname allowed despite unrelated DNS and IP SANs");
  }
  for (const char* host : {"other.com", "notbank.ru", "bank.ru.attacker.com",
                           "192.0.2.1", "0.0.0.0", "::", "::1", "", "*.bank.ru"}) {
    Expect(!PermitsCandidate(path, host, scoped),
           "same certificate does not authorize unlisted names or IP literals");
  }
  Expect(PermitsCandidate(path, "bank.ru", {{root, kDefaultZones, {}}}),
         "zone rule checks the requested hostname, not other SANs");
  Expect(!PermitsCandidate(path, "bank.ru", {}),
         "system-installed pinned root cannot bypass disabled profile trust");
  Expect(!PermitsCandidate(path, "bank.ru", {{root, {}, {}}}),
         "empty explicit constraints reject the pinned root");
  Expect(PermitsCandidate({leaf, other}, "bank.ru", {}),
         "ordinary trust anchors are not subject to Argon hostname rules");
  Expect(!PermitsCandidate({leaf, other}, "bank.ru", {{other, own_domains, {}}}),
         "ordinary roots retain Chromium's generic all-SAN constraints");

  bssl::CertPathErrors existing_errors;
  existing_errors.GetErrorsForCert(0)->AddError(
      bssl::cert_errors::kValidityFailedNotAfter);
  net::TestPathBuilderDelegate delegate{&scoped, "bank.ru"};
  delegate.CheckExtraConstraints(path, &existing_errors);
  Expect(existing_errors.ContainsHighSeverityErrors(),
         "allowing the hostname does not clear existing path-validation errors");

  Expect(net::CanPoolTitaniumRussianRootConnection(
             root->cert_buffer(), "bank.ru", "bank.ru"),
         "same-host reuse remains allowed");
  for (const char* host : {"other.com", "www.bank.ru", "192.0.2.1"}) {
    Expect(!net::CanPoolTitaniumRussianRootConnection(
               root->cert_buffer(), "bank.ru", host),
           "HTTP/2 and HTTP/3 aliases require a new certificate verification");
  }
  Expect(net::CanPoolTitaniumRussianRootConnection(
             other->cert_buffer(), "bank.ru", "other.com"),
         "ordinary roots retain cross-host pooling");
  std::cout << "PASS: " << checks << " BoringSSL policy checks\n";
}
