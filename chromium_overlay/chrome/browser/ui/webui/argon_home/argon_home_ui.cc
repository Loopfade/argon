// Copyright 2026 The Argon Authors
// Use of this source code is governed by the repository's LICENSE file.

#include "chrome/browser/ui/webui/argon_home/argon_home_ui.h"

#include <memory>
#include <string>
#include <string_view>
#include <utility>

#include "base/functional/bind.h"
#include "base/memory/ref_counted_memory.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/ui/webui/sanitized_image/sanitized_image_source.h"
#include "content/public/browser/url_data_source.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_data_source.h"
#include "services/network/public/mojom/content_security_policy.mojom.h"

namespace {

// Generated from the checked-in HTML/CSS/JS by apply_homepage.py. Immutable
// responses are compiled into the APK, so no server or extracted files are used.
#include "chrome/browser/ui/webui/argon_home/argon_home_resources.inc"

std::string_view GetResource(std::string_view path) {
  path = path.substr(0, path.find('?'));
  if (path.empty() || path == "index.html") {
    return kArgonHomeHtml;
  }
  if (path == "app.css") {
    return kArgonHomeCss;
  }
  if (path == "app.js") {
    return kArgonHomeJs;
  }
  if (path == "state.js") {
    return kArgonHomeState;
  }
  if (path == "icons.js") {
    return kArgonHomeIcons;
  }
  return {};
}

}  // namespace

bool ArgonHomeUIConfig::IsWebUIEnabled(
    content::BrowserContext* browser_context) {
  return !Profile::FromBrowserContext(browser_context)->IsOffTheRecord();
}

ArgonHomeUI::ArgonHomeUI(content::WebUI* web_ui) : WebUIController(web_ui) {
  auto* profile = Profile::FromWebUI(web_ui);
  content::URLDataSource::Add(profile,
                            std::make_unique<SanitizedImageSource>(profile));
  auto* source = content::WebUIDataSource::CreateAndAdd(
      profile, "argon");
  source->SetRequestFilter(
      base::BindRepeating([](const std::string& path) {
        return !GetResource(path).empty();
      }),
      base::BindRepeating([](const std::string& path,
                             content::WebUIDataSource::GotDataCallback callback) {
        std::move(callback).Run(base::MakeRefCounted<base::RefCountedString>(
            std::string(GetResource(path))));
      }));
  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::TrustedTypes,
      "trusted-types argon-home;");
  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::ImgSrc,
      "img-src 'self' data: chrome://image;");
  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::ConnectSrc, "connect-src 'none';");
  source->OverrideContentSecurityPolicy(
      network::mojom::CSPDirectiveName::FrameSrc, "frame-src 'none';");
}

ArgonHomeUI::~ArgonHomeUI() = default;
