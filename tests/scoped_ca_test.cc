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

namespace {
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
  net::CheckTitaniumRussianRootConstraints({root}, &disabled_errors, {});
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
  net::CheckTitaniumRussianRootConstraints({other}, &ordinary_errors, {});
  Expect(!ordinary_errors.ContainsHighSeverityErrors(), "ordinary root unaffected");
  std::cout << "PASS: " << checks << " BoringSSL policy checks\n";
}
