// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include "net/cert/titanium_ru_domain_policy.h"

#include <string>

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

TEST(TitaniumRuDomainPolicyTest, BuiltInDomainsStayImplicit) {
  std::string normalized;
  for (const char* input : {"ru", "bank.ru", "xn--p1ai", "site.xn--p1ai",
                            "su", "archive.su"}) {
    EXPECT_EQ(TitaniumRuDomainValidationResult::kCoveredByBuiltIn,
              NormalizeTitaniumRussianRootDomain(input, &normalized))
        << input;
  }
}

}  // namespace
}  // namespace net
