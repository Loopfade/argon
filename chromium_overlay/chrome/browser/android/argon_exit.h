// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#ifndef CHROME_BROWSER_ANDROID_ARGON_EXIT_H_
#define CHROME_BROWSER_ANDROID_ARGON_EXIT_H_
namespace argon {
// UI-thread only. Covers page, extension and service-worker renderer launches.
bool IsExitInProgress();
}
#endif
