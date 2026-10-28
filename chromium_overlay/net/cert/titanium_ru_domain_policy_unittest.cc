// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include "net/cert/titanium_ru_domain_policy.h"

#include <string>
#include <vector>

#include "testing/gtest/include/gtest/gtest.h"

namespace net {
namespace {

TEST(TitaniumRuDomainPolicyTest, NormalizesDnsNames) {
  std::string normalized;
  EXPECT_EQ(TitaniumRuDomainValidationResult::kValid,
            NormalizeTitaniumRussianRootDomain("  EXAMPLE.COM.  ",
                                                &normalized));
  EXPECT_EQ("example.com", normalized);

  EXPECT_EQ(TitaniumRuDomainValidationResult::kValid,
            NormalizeTitaniumRussianRootDomain(
                "\xD0\xBF\xD1\x80\xD0\xB8\xD0\xBC\xD0\xB5\xD1\x80.com",
                &normalized));
  EXPECT_EQ("xn--e1afmkfd.com", normalized);
}

TEST(TitaniumRuDomainPolicyTest, RejectsPublicAndPrivateSuffixes) {
  std::string normalized;
  for (const char* input : {"com", ".com", "co.uk", "blogspot.com"}) {
    const auto result =
        NormalizeTitaniumRussianRootDomain(input, &normalized);
    EXPECT_NE(TitaniumRuDomainValidationResult::kValid, result) << input;
  }
}

TEST(TitaniumRuDomainPolicyTest, RejectsNonHostInput) {
  std::string normalized;
  for (const char* input : {"*", "*.example.com", "https://example.com",
                            "example.com/path", "example.com:443",
                            "127.0.0.1", "localhost", "example.invalid",
                            "bad_name.com", "example..com", "-example.com"}) {
    EXPECT_EQ(TitaniumRuDomainValidationResult::kInvalid,
              NormalizeTitaniumRussianRootDomain(input, &normalized))
        << input;
  }
}

TEST(TitaniumRuDomainPolicyTest, ConcreteRussianDomainsCanBeSaved) {
  std::string normalized;
  for (const char* input : {"bank.ru", "site.xn--p1ai", "archive.su"}) {
    EXPECT_EQ(TitaniumRuDomainValidationResult::kValid,
              NormalizeTitaniumRussianRootDomain(input, &normalized))
        << input;
  }
  for (const char* input : {"ru", "xn--p1ai", "su"}) {
    EXPECT_EQ(TitaniumRuDomainValidationResult::kPublicSuffix,
              NormalizeTitaniumRussianRootDomain(input, &normalized))
        << input;
  }
}

TEST(TitaniumRuDomainPolicyTest, EveryZoneSwitchCombination) {
  const std::vector<std::string> domains = {"bank.ru", "example.com"};
  for (unsigned mask = 0; mask < 8; ++mask) {
    const TitaniumRuBuiltInZoneStates enabled = {
        (mask & 1) != 0, (mask & 2) != 0, (mask & 4) != 0};
    const auto names = GetTitaniumRussianRootPermittedDnsNames(enabled, domains);
    std::vector<std::string> expected;
    for (size_t i = 0; i < enabled.size(); ++i) {
      if (enabled[i]) {
        expected.emplace_back(kTitaniumRussianRootBuiltInZones[i]);
      }
    }
    expected.insert(expected.end(), domains.begin(), domains.end());
    EXPECT_EQ(expected, names) << mask;
    EXPECT_EQ(std::vector<std::string>(),
              GetTitaniumRussianRootCoveringRules("bank.ru.example.com",
                                                  enabled, {}))
        << mask;
  }
  EXPECT_TRUE(GetTitaniumRussianRootPermittedDnsNames(
                  {false, false, false}, {})
                  .empty());
}

TEST(TitaniumRuDomainPolicyTest, ReportsEveryCoveringRuleButNotItself) {
  const std::vector<std::string> domains = {
      "bank.ru", "pay.bank.ru", "secure.pay.bank.ru"};
  EXPECT_EQ((std::vector<std::string>{".ru", "bank.ru", "pay.bank.ru"}),
            GetTitaniumRussianRootCoveringRules(
                "secure.pay.bank.ru", {true, true, true}, domains));
  EXPECT_EQ((std::vector<std::string>{"bank.ru", "pay.bank.ru"}),
            GetTitaniumRussianRootCoveringRules(
                "secure.pay.bank.ru", {false, true, true}, domains));
  EXPECT_TRUE(GetTitaniumRussianRootCoveringRules(
                  "bank.ru", {false, false, false}, domains)
                  .empty());
}

TEST(TitaniumRuDomainPolicyTest, CoverageUsesDnsLabelBoundaries) {
  EXPECT_TRUE(IsTitaniumRussianRootDomainCoveredBy("example.com", "example.com"));
  EXPECT_TRUE(IsTitaniumRussianRootDomainCoveredBy("www.example.com",
                                                   "example.com"));
  EXPECT_FALSE(IsTitaniumRussianRootDomainCoveredBy("notexample.com",
                                                    "example.com"));
  EXPECT_FALSE(IsTitaniumRussianRootDomainCoveredBy("example.com.attacker.net",
                                                    "example.com"));
  EXPECT_FALSE(IsTitaniumRussianRootDomainCoveredBy("ru", ".ru"));
  EXPECT_TRUE(IsTitaniumRussianRootDomainCoveredBy("site.xn--p1ai",
                                                   ".xn--p1ai"));
}

TEST(TitaniumRuDomainPolicyTest, UserLimitDoesNotDependOnEnabledZones) {
  std::vector<std::string> domains;
  for (size_t i = 0; i < kTitaniumRussianRootMaxUserDomains + 1; ++i) {
    domains.push_back("site" + std::to_string(i) + ".example.com");
  }
  EXPECT_EQ(kTitaniumRussianRootMaxUserDomains,
            GetTitaniumRussianRootPermittedDnsNames(
                {false, false, false}, domains)
                .size());
  EXPECT_EQ(kTitaniumRussianRootMaxUserDomains + 3,
            GetTitaniumRussianRootPermittedDnsNames(
                {true, true, true}, domains)
                .size());
}

}  // namespace
}  // namespace net
