// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#include "chrome/browser/ui/webui/argon_home/argon_theme_handler.h"

#include <cstdint>
#include <memory>
#include <string>

#include "base/android/jni_android.h"
#include "base/android/scoped_java_ref.h"
#include "base/functional/bind.h"
#include "base/values.h"
#include "chrome/android/chrome_jni_headers/ArgonThemeBridge_jni.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_message_handler.h"

namespace {

std::string ThemeName(int theme) {
  switch (theme) {
    case 2:
      return "amoled";
    case 1:
      return "dark";
    default:
      return "light";
  }
}

class ArgonThemeHandler final : public content::WebUIMessageHandler {
 public:
  ~ArgonThemeHandler() override { StopObserving(); }

  void RegisterMessages() override {
    web_ui()->RegisterMessageCallback(
        "getArgonBrowserTheme",
        base::BindRepeating(&ArgonThemeHandler::GetTheme, base::Unretained(this)));
  }

  void OnThemeChanged(int theme) {
    if (IsJavascriptAllowed()) {
      FireWebUIListener("argon-browser-theme-changed", ThemeName(theme));
    }
  }

 private:
  void OnJavascriptAllowed() override {
    bridge_.Reset(Java_ArgonThemeBridge_create(
        base::android::AttachCurrentThread(), reinterpret_cast<intptr_t>(this)));
  }

  void OnJavascriptDisallowed() override { StopObserving(); }

  void StopObserving() {
    if (!bridge_.is_null()) {
      Java_ArgonThemeBridge_destroy(base::android::AttachCurrentThread(), bridge_);
      bridge_.Reset();
    }
  }

  void GetTheme(const base::ListValue& args) {
    if (args.size() != 1 || !args[0].is_string()) {
      return;
    }
    AllowJavascript();
    ResolveJavascriptCallback(args[0], base::Value(GetArgonBrowserTheme()));
  }

  base::android::ScopedJavaGlobalRef<jobject> bridge_;
};

static void JNI_ArgonThemeBridge_OnThemeChanged(JNIEnv* env,
                                              jlong observer,
                                              jint theme) {
  reinterpret_cast<ArgonThemeHandler*>(observer)->OnThemeChanged(theme);
}

}  // namespace

std::string GetArgonBrowserTheme() {
  return ThemeName(
      Java_ArgonThemeBridge_getTheme(base::android::AttachCurrentThread()));
}

std::unique_ptr<content::WebUIMessageHandler> CreateArgonThemeHandler() {
  return std::make_unique<ArgonThemeHandler>();
}

DEFINE_JNI(ArgonThemeBridge)
