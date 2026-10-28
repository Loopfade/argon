// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include "chrome/browser/net/argon_ca_policy.h"

#include <string>
#include <utility>
#include <vector>

#include "components/prefs/testing_pref_service.h"
#include "testing/gtest/include/gtest/gtest.h"

namespace argon {
namespace {

class ArgonRussianCaPolicyTest : public testing::Test {
 protected:
  void SetUp() override { RegisterRussianCaPrefs(prefs_.registry()); }

  void SetZones(const net::TitaniumRuBuiltInZoneStates& enabled) {
    for (size_t i = 0; i < enabled.size(); ++i) {
      prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[i], enabled[i]);
    }
  }

  TestingPrefServiceSimple prefs_;
};

TEST_F(ArgonRussianCaPolicyTest, DefaultsPreserveExistingTrustAndList) {
  EXPECT_EQ((net::TitaniumRuBuiltInZoneStates{true, true, true}),
            GetRussianCaBuiltInZoneStates(&prefs_));
  EXPECT_EQ((std::vector<std::string>{".ru", ".xn--p1ai", ".su"}),
            GetRussianCaPermittedDnsNames(&prefs_));
  EXPECT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "EXAMPLE.COM."));
  EXPECT_EQ((std::vector<std::string>{"example.com"}),
            GetRussianCaAdditionalDomains(&prefs_));
}

TEST_F(ArgonRussianCaPolicyTest, AddsRussianDomainOnlyWhenItsZoneIsDisabled) {
  EXPECT_EQ(AddRussianCaDomainResult::kCovered,
            AddRussianCaDomain(&prefs_, "bank.ru"));
  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], false);
  EXPECT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, " BANK.RU. "));
  EXPECT_EQ((std::vector<std::string>{"bank.ru"}),
            GetRussianCaAdditionalDomains(&prefs_));
  EXPECT_EQ((std::vector<std::string>{".xn--p1ai", ".su", "bank.ru"}),
            GetRussianCaPermittedDnsNames(&prefs_));
  EXPECT_EQ(AddRussianCaDomainResult::kDuplicate,
            AddRussianCaDomain(&prefs_, "BANK.RU"));
}

TEST_F(ArgonRussianCaPolicyTest, PreviewUsesCurrentRulesWithoutChangingPrefs) {
  SetZones({false, false, false});
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "pay.bank.ru"));
  const auto saved = prefs_.GetList(prefs::kRussianCaAdditionalDomains).Clone();
  std::string normalized;

  EXPECT_EQ(AddRussianCaDomainResult::kSuccess,
            ValidateRussianCaDomain(&prefs_, " BANK.RU. ", &normalized));
  EXPECT_EQ("bank.ru", normalized);
  EXPECT_EQ(saved, prefs_.GetList(prefs::kRussianCaAdditionalDomains));

  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], true);
  EXPECT_EQ(AddRussianCaDomainResult::kCovered,
            ValidateRussianCaDomain(&prefs_, " BANK.RU. ", &normalized));
  EXPECT_EQ(AddRussianCaDomainResult::kCovered,
            AddRussianCaDomain(&prefs_, " BANK.RU. "));
  EXPECT_EQ(saved, prefs_.GetList(prefs::kRussianCaAdditionalDomains));
  EXPECT_EQ((std::vector<std::string>{".ru"}),
            GetRussianCaCoveringRules(&prefs_, "pay.bank.ru"));

  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], false);
  EXPECT_EQ(AddRussianCaDomainResult::kSuccess,
            ValidateRussianCaDomain(&prefs_, " BANK.RU. ", &normalized));
  EXPECT_EQ(saved, prefs_.GetList(prefs::kRussianCaAdditionalDomains));
  EXPECT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, " BANK.RU. "));
  EXPECT_EQ(AddRussianCaDomainResult::kCovered,
            ValidateRussianCaDomain(&prefs_, "www.bank.ru", &normalized));
  EXPECT_EQ((std::vector<std::string>{"bank.ru"}),
            GetRussianCaCoveringRules(&prefs_, "pay.bank.ru"));
  EXPECT_EQ((std::vector<std::string>{"bank.ru", "pay.bank.ru"}),
            GetRussianCaAdditionalDomains(&prefs_));
}

TEST_F(ArgonRussianCaPolicyTest, PreviewAndWriteAgreeOnErrorsAtTheLimit) {
  for (size_t i = 0; i < net::kTitaniumRussianRootMaxUserDomains; ++i) {
    ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
              AddRussianCaDomain(
                  &prefs_, "site" + std::to_string(i) + ".example.com"));
  }
  const auto saved = prefs_.GetList(prefs::kRussianCaAdditionalDomains).Clone();
  const struct {
    const char* input;
    AddRussianCaDomainResult expected;
  } cases[] = {
      {"https://example.com", AddRussianCaDomainResult::kInvalid},
      {"ru", AddRussianCaDomainResult::kPublicSuffix},
      {"bank.ru", AddRussianCaDomainResult::kCovered},
      {"www.site0.example.com", AddRussianCaDomainResult::kCovered},
      {"SITE0.EXAMPLE.COM.", AddRussianCaDomainResult::kDuplicate},
      {"other.example.com", AddRussianCaDomainResult::kTooMany},
  };
  for (const auto& test : cases) {
    SCOPED_TRACE(test.input);
    std::string normalized;
    EXPECT_EQ(test.expected,
              ValidateRussianCaDomain(&prefs_, test.input, &normalized));
    EXPECT_EQ(saved, prefs_.GetList(prefs::kRussianCaAdditionalDomains));
    EXPECT_EQ(test.expected, AddRussianCaDomain(&prefs_, test.input));
    EXPECT_EQ(saved, prefs_.GetList(prefs::kRussianCaAdditionalDomains));
  }
}

TEST_F(ArgonRussianCaPolicyTest, SwitchingAndEditingDoNotLoseCoveredEntries) {
  SetZones({false, false, false});
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "bank.ru"));
  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], true);
  EXPECT_EQ((std::vector<std::string>{".ru"}),
            GetRussianCaCoveringRules(&prefs_, "bank.ru"));
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "example.com"));
  EXPECT_TRUE(RemoveRussianCaDomain(&prefs_, "example.com"));
  EXPECT_EQ((std::vector<std::string>{"bank.ru"}),
            GetRussianCaAdditionalDomains(&prefs_));
  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], false);
  EXPECT_TRUE(GetRussianCaCoveringRules(&prefs_, "bank.ru").empty());
  EXPECT_EQ((std::vector<std::string>{"bank.ru"}),
            GetRussianCaPermittedDnsNames(&prefs_));
}

TEST_F(ArgonRussianCaPolicyTest, CoveredEntryCanBeRemovedWithoutRevokingZone) {
  SetZones({false, false, false});
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "bank.ru"));
  prefs_.SetBoolean(prefs::kRussianCaBuiltInZonePrefs[0], true);
  EXPECT_TRUE(RemoveRussianCaDomain(&prefs_, "BANK.RU."));
  EXPECT_TRUE(GetRussianCaAdditionalDomains(&prefs_).empty());
  EXPECT_EQ((std::vector<std::string>{".ru"}),
            GetRussianCaPermittedDnsNames(&prefs_));
}

TEST_F(ArgonRussianCaPolicyTest, AddingParentReportsOverlapWithoutDeletingChild) {
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "pay.example.com"));
  ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
            AddRussianCaDomain(&prefs_, "example.com"));
  EXPECT_EQ((std::vector<std::string>{"example.com"}),
            GetRussianCaCoveringRules(&prefs_, "pay.example.com"));
  EXPECT_EQ(AddRussianCaDomainResult::kCovered,
            AddRussianCaDomain(&prefs_, "www.example.com"));
  EXPECT_EQ((std::vector<std::string>{"example.com", "pay.example.com"}),
            GetRussianCaAdditionalDomains(&prefs_));
  EXPECT_TRUE(RemoveRussianCaDomain(&prefs_, "example.com"));
  EXPECT_TRUE(GetRussianCaCoveringRules(&prefs_, "pay.example.com").empty());
  EXPECT_EQ((std::vector<std::string>{".ru", ".xn--p1ai", ".su", "pay.example.com"}),
            GetRussianCaPermittedDnsNames(&prefs_));
}

TEST_F(ArgonRussianCaPolicyTest, EveryCombinationUsesIndependentPreferences) {
  for (unsigned mask = 0; mask < 8; ++mask) {
    const net::TitaniumRuBuiltInZoneStates enabled = {
        (mask & 1) != 0, (mask & 2) != 0, (mask & 4) != 0};
    SetZones(enabled);
    EXPECT_EQ(enabled, GetRussianCaBuiltInZoneStates(&prefs_));
    for (size_t i = 0; i < enabled.size(); ++i) {
      const std::string host =
          "bank" + std::string(net::kTitaniumRussianRootBuiltInZones[i]);
      EXPECT_EQ(enabled[i], !GetRussianCaCoveringRules(&prefs_, host).empty());
    }
  }
  SetZones({false, false, false});
  EXPECT_TRUE(GetRussianCaPermittedDnsNames(&prefs_).empty());
}

TEST_F(ArgonRussianCaPolicyTest, ReadsNormalizeAndRetainEntriesRegardlessOfZone) {
  base::ListValue stored;
  stored.Append("BANK.RU.");
  stored.Append("bank.ru");
  stored.Append("bad_name.com");
  stored.Append(42);
  prefs_.SetList(prefs::kRussianCaAdditionalDomains, std::move(stored));
  EXPECT_EQ((std::vector<std::string>{"bank.ru"}),
            GetRussianCaAdditionalDomains(&prefs_));
  EXPECT_EQ((std::vector<std::string>{".ru"}),
            GetRussianCaCoveringRules(&prefs_, " BANK.RU. "));
  EXPECT_TRUE(RemoveRussianCaDomain(&prefs_, "bank.ru"));
}

TEST_F(ArgonRussianCaPolicyTest, UserLimitAndValidationStillApply) {
  EXPECT_EQ(AddRussianCaDomainResult::kPublicSuffix,
            AddRussianCaDomain(&prefs_, "com"));
  EXPECT_EQ(AddRussianCaDomainResult::kInvalid,
            AddRussianCaDomain(&prefs_, "https://example.com"));
  EXPECT_EQ(AddRussianCaDomainResult::kInvalid,
            AddRussianCaDomain(&prefs_, "*.example.com"));
  for (size_t i = 0; i < net::kTitaniumRussianRootMaxUserDomains; ++i) {
    ASSERT_EQ(AddRussianCaDomainResult::kSuccess,
              AddRussianCaDomain(
                  &prefs_, "site" + std::to_string(i) + ".example.com"));
  }
  EXPECT_EQ(AddRussianCaDomainResult::kTooMany,
            AddRussianCaDomain(&prefs_, "other.example.com"));
  SetZones({false, false, false});
  EXPECT_EQ(AddRussianCaDomainResult::kTooMany,
            AddRussianCaDomain(&prefs_, "other.example.com"));
}

TEST_F(ArgonRussianCaPolicyTest, BatchCoverageMatchesDetailsForNormalizedSnapshot) {
  base::ListValue stored;
  stored.Append("BANK.RU.");
  stored.Append("bank.ru");
  stored.Append("pay.example.com");
  stored.Append("example.com");
  stored.Append("bad_name.com");
  prefs_.SetList(prefs::kRussianCaAdditionalDomains, std::move(stored));
  const auto domains = GetRussianCaAdditionalDomains(&prefs_);
  EXPECT_EQ((std::vector<std::string>{"bank.ru", "pay.example.com"}),
            GetRussianCaCoveredDomains(&prefs_, domains));
  SetZones({false, false, false});
  EXPECT_EQ((std::vector<std::string>{"pay.example.com"}),
            GetRussianCaCoveredDomains(&prefs_, domains));
  for (const auto& domain : domains) {
    const auto covered = GetRussianCaCoveredDomains(&prefs_, domains);
    EXPECT_EQ(!GetRussianCaCoveringRules(&prefs_, domain).empty(),
              std::find(covered.begin(), covered.end(), domain) != covered.end());
  }
  // Rendering coverage must never rewrite or drop saved entries.
  EXPECT_EQ(domains, GetRussianCaAdditionalDomains(&prefs_));
}

}  // namespace
}  // namespace argon
