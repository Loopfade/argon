// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#ifndef CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_SEARCH_HANDLER_H_
#define CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_SEARCH_HANDLER_H_

#include <memory>

namespace content {
class WebUIMessageHandler;
}

std::unique_ptr<content::WebUIMessageHandler> CreateArgonSearchHandler();

#endif  // CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_SEARCH_HANDLER_H_
