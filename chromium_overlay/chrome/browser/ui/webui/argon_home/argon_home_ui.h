// Copyright 2026 The Argon Authors
// Use of this source code is governed by the repository's LICENSE file.

#ifndef CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_HOME_UI_H_
#define CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_HOME_UI_H_

#include "content/public/browser/web_ui_controller.h"
#include "content/public/browser/webui_config.h"
#include "content/public/common/url_constants.h"

class ArgonHomeUI;

class ArgonHomeUIConfig : public content::DefaultWebUIConfig<ArgonHomeUI> {
 public:
  ArgonHomeUIConfig()
      : DefaultWebUIConfig(content::kChromeUIScheme, "argon") {}

  bool IsWebUIEnabled(content::BrowserContext* browser_context) override;
};

// The bundled homepage has no native message handlers or remote script sources.
class ArgonHomeUI : public content::WebUIController {
 public:
  explicit ArgonHomeUI(content::WebUI* web_ui);
  ~ArgonHomeUI() override;

  ArgonHomeUI(const ArgonHomeUI&) = delete;
  ArgonHomeUI& operator=(const ArgonHomeUI&) = delete;
};

#endif  // CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_HOME_UI_H_
