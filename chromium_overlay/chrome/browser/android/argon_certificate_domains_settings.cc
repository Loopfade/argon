// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only

#include <string>
#include <vector>

#include "base/android/jni_string.h"
#include "chrome/android/chrome_jni_headers/ArgonCertificateDomainsSettings_jni.h"
#include "chrome/browser/net/argon_ca_policy.h"
#include "chrome/browser/profiles/profile.h"

namespace {

static std::vector<std::string> JNI_ArgonCertificateDomainsSettings_GetDomains(
    JNIEnv* env,
    Profile* profile) {
  return argon::GetRussianCaAdditionalDomains(profile->GetPrefs());
}

static jboolean JNI_ArgonCertificateDomainsSettings_IsBuiltInZoneEnabled(
    JNIEnv* env,
    Profile* profile,
    jint zone) {
  if (zone < 0 || static_cast<size_t>(zone) >=
                      argon::prefs::kRussianCaBuiltInZonePrefs.size()) {
    return false;
  }
  return profile->GetPrefs()->GetBoolean(
      argon::prefs::kRussianCaBuiltInZonePrefs[zone]);
}

static jboolean JNI_ArgonCertificateDomainsSettings_SetBuiltInZoneEnabled(
    JNIEnv* env,
    Profile* profile,
    jint zone,
    jboolean enabled) {
  if (zone < 0 || static_cast<size_t>(zone) >=
                      argon::prefs::kRussianCaBuiltInZonePrefs.size()) {
    return false;
  }
  profile->GetPrefs()->SetBoolean(
      argon::prefs::kRussianCaBuiltInZonePrefs[zone], enabled);
  return true;
}

static std::vector<std::string>
JNI_ArgonCertificateDomainsSettings_GetCoveringRules(
    JNIEnv* env,
    Profile* profile,
    const std::string& domain) {
  return argon::GetRussianCaCoveringRules(profile->GetPrefs(), domain);
}

static jint JNI_ArgonCertificateDomainsSettings_AddDomain(
    JNIEnv* env,
    Profile* profile,
    const std::string& input) {
  return static_cast<jint>(argon::AddRussianCaDomain(profile->GetPrefs(), input));
}

static jboolean JNI_ArgonCertificateDomainsSettings_RemoveDomain(
    JNIEnv* env,
    Profile* profile,
    const std::string& input) {
  return argon::RemoveRussianCaDomain(profile->GetPrefs(), input);
}

}  // namespace

DEFINE_JNI(ArgonCertificateDomainsSettings)
