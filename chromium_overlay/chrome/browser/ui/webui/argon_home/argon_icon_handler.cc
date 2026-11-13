// Copyright 2026 The Argon Authors
// Use of this source code is governed by the repository's LICENSE file.
#include "chrome/browser/ui/webui/argon_home/argon_icon_handler.h"

#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "base/base64.h"
#include "base/functional/bind.h"
#include "base/memory/weak_ptr.h"
#include "base/time/time.h"
#include "base/values.h"
#include "chrome/browser/profiles/profile.h"
#include "content/public/browser/storage_partition.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_message_handler.h"
#include "net/base/net_errors.h"
#include "net/http/http_response_headers.h"
#include "net/traffic_annotation/network_traffic_annotation.h"
#include "net/url_request/redirect_info.h"
#include "services/network/public/cpp/resource_request.h"
#include "services/network/public/cpp/shared_url_loader_factory.h"
#include "services/network/public/cpp/simple_url_loader.h"
#include "services/network/public/mojom/url_response_head.mojom.h"
#include "url/gurl.h"

namespace {

constexpr size_t kMaxImageBytes = 1024 * 1024;
constexpr size_t kMaxPageBytes = 256 * 1024;
constexpr size_t kMaxManifestBytes = 64 * 1024;
constexpr size_t kMaxRequests = 4;

bool ValidURL(const GURL& url) {
  return url.is_valid() && url.SchemeIsHTTPOrHTTPS() && url.has_host() &&
         !url.has_username() && !url.has_password();
}

// Only explicit icon discovery and Bing background selection use this transport.
// Page scripts are not run and
// the WebUI's connect-src/frame-src restrictions remain in place.
class ArgonIconHandler final : public content::WebUIMessageHandler {
 public:
  void RegisterMessages() override {
    web_ui()->RegisterMessageCallback(
        "getArgonIconResource",
        base::BindRepeating(&ArgonIconHandler::Read, base::Unretained(this)));
  }

 private:
  struct Request {
    std::unique_ptr<network::SimpleURLLoader> loader;
    base::Value callback;
    std::string kind;
    int redirects = 0;
  };

  void OnJavascriptDisallowed() override {
    weak_factory_.InvalidateWeakPtrs();
    requests_.clear();
  }

  void Reply(size_t id, base::DictValue result = {}) {
    auto it = requests_.find(id);
    if (it == requests_.end()) return;
    base::Value callback = std::move(it->second.callback);
    requests_.erase(it);
    if (IsJavascriptAllowed()) ResolveJavascriptCallback(callback, result);
  }

  void OnRedirect(size_t id, const GURL& before,
                  const net::RedirectInfo& info,
                  const network::mojom::URLResponseHead&,
                  std::vector<std::string>*) {
    auto it = requests_.find(id);
    if (it == requests_.end()) return;
    if (++it->second.redirects > 3 || !ValidURL(info.new_url) ||
        (before.SchemeIs("https") && !info.new_url.SchemeIs("https"))) {
      Reply(id);
    }
  }

  void OnLoaded(size_t id, std::optional<std::string> body) {
    auto it = requests_.find(id);
    if (it == requests_.end()) return;
    const auto& request = it->second;
    const auto* loader = request.loader.get();
    const auto* info = loader->ResponseInfo();
    // A large HTML document can supply its bounded prefix, usually the head.
    // Images and manifests must be downloaded completely.
    const bool partial_page = request.kind == "page" &&
        loader->NetError() == net::ERR_INSUFFICIENT_RESOURCES;
    if (!body || (loader->NetError() != net::OK && !partial_page) ||
        !info || !info->headers || !ValidURL(loader->GetFinalURL()) ||
        info->headers->response_code() < 200 ||
        info->headers->response_code() >= 300) {
      Reply(id);
      return;
    }
    const std::string& mime = info->mime_type;
    const bool allowed = request.kind == "page"
        ? (mime == "text/html" || mime == "application/xhtml+xml")
        : request.kind == "manifest"
            ? (mime == "application/manifest+json" || mime == "application/json" ||
               mime == "text/plain")
            : (mime == "image/png" || mime == "image/jpeg" ||
               mime == "image/webp" || mime == "image/gif" ||
               mime == "image/svg+xml" || mime == "image/x-icon" ||
               mime == "image/vnd.microsoft.icon");
    base::DictValue result;
    if (allowed) {
      result.Set("url", loader->GetFinalURL().spec());
      result.Set("mime", mime);
      result.Set("charset", info->charset);
      // Transfer bytes; the renderer decodes text without inserting page HTML.
      result.Set("body", base::Base64Encode(*body));
    }
    Reply(id, std::move(result));
  }

  void Read(const base::ListValue& args) {
    if (args.size() != 3 || !args[0].is_string() ||
        !args[1].is_string() || !args[2].is_string()) return;
    AllowJavascript();
    const std::string& kind = args[1].GetString();
    const std::string& value = args[2].GetString();
    const GURL url(value);
    auto* profile = Profile::FromWebUI(web_ui());
    if (profile->IsOffTheRecord() || requests_.size() >= kMaxRequests ||
        value.size() > 16384 || !ValidURL(url) ||
        (kind != "page" && kind != "manifest" && kind != "image")) {
      ResolveJavascriptCallback(args[0], base::DictValue());
      return;
    }
    auto resource = std::make_unique<network::ResourceRequest>();
    resource->url = url.GetWithoutRef();
    resource->credentials_mode = network::mojom::CredentialsMode::kOmit;
    resource->do_not_prompt_for_login = true;
    auto loader = network::SimpleURLLoader::Create(
        std::move(resource),
        net::DefineNetworkTrafficAnnotation("argon_site_icon_discovery", R"(
          semantics {
            sender: "Argon homepage image chooser"
            description: "Reads site HTML, an optional web manifest and a "
                         "bounded set of images for the homepage icon chooser, "
                         "or a Bing JSON feed and a selected background image. "
                         "Page scripts are not executed."
            trigger: "The user presses Find icons or selects Bing backgrounds."
            data: "Page and icon URLs. No cookies, HTTP authentication "
                  "credentials or referrer are sent."
            destination: WEBSITE
          }
          policy {
            cookies_allowed: NO
            setting: "Only runs on an explicit user request."
            policy_exception_justification: "A user-initiated Argon feature."
          })"));
    const size_t id = next_id_++;
    auto* raw_loader = loader.get();
    requests_.emplace(id, Request{std::move(loader), args[0].Clone(), kind});
    raw_loader->SetTimeoutDuration(base::Seconds(6));
    raw_loader->SetAllowPartialResults(kind == "page");
    raw_loader->SetOnRedirectCallback(base::BindRepeating(
        &ArgonIconHandler::OnRedirect, weak_factory_.GetWeakPtr(), id));
    raw_loader->DownloadToString(
        profile->GetDefaultStoragePartition()
            ->GetURLLoaderFactoryForBrowserProcess().get(),
        base::BindOnce(&ArgonIconHandler::OnLoaded,
                       weak_factory_.GetWeakPtr(), id),
        kind == "image" ? kMaxImageBytes :
            kind == "manifest" ? kMaxManifestBytes : kMaxPageBytes);
  }

  size_t next_id_ = 0;
  std::map<size_t, Request> requests_;
  base::WeakPtrFactory<ArgonIconHandler> weak_factory_{this};
};

}  // namespace

std::unique_ptr<content::WebUIMessageHandler> CreateArgonIconHandler() {
  return std::make_unique<ArgonIconHandler>();
}
