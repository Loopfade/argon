// Copyright 2026 The Argon Authors
// Use of this source code is governed by the repository's LICENSE file.
#ifndef CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_ICON_HANDLER_H_
#define CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_ICON_HANDLER_H_

#include <memory>

namespace content {
class WebUIMessageHandler;
}

std::unique_ptr<content::WebUIMessageHandler> CreateArgonIconHandler();

#endif  // CHROME_BROWSER_UI_WEBUI_ARGON_HOME_ARGON_ICON_HANDLER_H_
