// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include "base/at_exit.h"
#include "base/i18n/icu_util.h"
#include "testing/gtest/include/gtest/gtest.h"

int main(int argc, char** argv) {
  base::AtExitManager at_exit;
  if (!base::i18n::InitializeICU()) {
    return 1;
  }
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
