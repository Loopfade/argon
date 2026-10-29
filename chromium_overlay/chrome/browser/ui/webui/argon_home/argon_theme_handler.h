// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#ifndef CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_THEME_HANDLER_H_
#define CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_THEME_HANDLER_H_

#include <memory>
#include <string>

namespace content {
class WebUIMessageHandler;
}

std::string GetArgonBrowserTheme();
std::unique_ptr<content::WebUIMessageHandler> CreateArgonThemeHandler();

#endif  // CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_THEME_HANDLER_H_
