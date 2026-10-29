// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#include "chrome/browser/ui/webui/argon_home/argon_search_handler.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "base/android/jni_android.h"
#include "base/android/jni_string.h"
#include "base/android/scoped_java_ref.h"
#include "base/functional/bind.h"
#include "base/memory/raw_ptr.h"
#include "base/strings/string_util.h"
#include "base/strings/utf_string_conversions.h"
#include "base/values.h"
#include "chrome/android/chrome_jni_headers/ArgonHomeBridge_jni.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/search_engines/template_url_service_factory.h"
#include "components/search_engines/search_engine_type.h"
#include "components/search_engines/template_url.h"
#include "components/search_engines/template_url_data.h"
#include "components/search_engines/template_url_service.h"
#include "components/search_engines/template_url_service_observer.h"
#include "content/public/browser/web_contents.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_message_handler.h"
#include "url/gurl.h"

namespace {

struct Builtin {
  const char* id;
  const char* name;
  const char* url;
  SearchEngineType type;
};
constexpr Builtin kBuiltins[] = {
    {"google", "Google", "https://www.google.com/search?q={query}", SEARCH_ENGINE_GOOGLE},
    {"bing", "Bing", "https://www.bing.com/search?q={query}", SEARCH_ENGINE_BING},
    {"duck", "DuckDuckGo", "https://duckduckgo.com/?q={query}", SEARCH_ENGINE_DUCKDUCKGO},
    {"noai", "DuckDuckGo NoAI", "https://noai.duckduckgo.com/?q={query}", SEARCH_ENGINE_OTHER},
};

class ArgonSearchHandler final : public content::WebUIMessageHandler,
                                 public TemplateURLServiceObserver {
 public:
  ~ArgonSearchHandler() override { Stop(); }

  void RegisterMessages() override {
    web_ui()->RegisterMessageCallback("getArgonSearchState", base::BindRepeating(
        &ArgonSearchHandler::GetState, base::Unretained(this)));
    web_ui()->RegisterMessageCallback("setArgonSearchEngine", base::BindRepeating(
        &ArgonSearchHandler::SetEngine, base::Unretained(this)));
    web_ui()->RegisterMessageCallback("focusArgonOmnibox", base::BindRepeating(
        &ArgonSearchHandler::Focus, base::Unretained(this)));
    web_ui()->RegisterMessageCallback("setArgonSearchBoxBounds", base::BindRepeating(
        &ArgonSearchHandler::SetBounds, base::Unretained(this)));
  }

  void OnSearchBoxAlphaChanged(float alpha, bool focused) {
    if (IsJavascriptAllowed() && std::isfinite(alpha)) {
      base::DictValue state;
      state.Set("alpha", static_cast<double>(std::clamp(alpha, 0.f, 1.f)));
      state.Set("focused", focused);
      FireWebUIListener("argon-search-box-state", state);
    }
  }

 private:
  void OnJavascriptAllowed() override {
    bridge_.Reset(Java_ArgonHomeBridge_create(base::android::AttachCurrentThread(),
        web_ui()->GetWebContents()->GetJavaWebContents(), reinterpret_cast<intptr_t>(this)));
    service_ = TemplateURLServiceFactory::GetForProfile(Profile::FromWebUI(web_ui()));
    if (service_) {
      service_->AddObserver(this);
      service_->Load();
    }
  }

  void OnJavascriptDisallowed() override { Stop(); }

  void Stop() {
    pending_.clear();
    if (service_) {
      service_->RemoveObserver(this);
      service_ = nullptr;
    }
    if (!bridge_.is_null()) {
      Java_ArgonHomeBridge_destroy(base::android::AttachCurrentThread(), bridge_);
      bridge_.Reset();
    }
  }

  void OnTemplateURLServiceShuttingDown() override {
    service_->RemoveObserver(this);
    service_ = nullptr;
    ReplyAndNotify();
  }

  void OnTemplateURLServiceChanged() override { ReplyAndNotify(); }

  base::DictValue State() const {
    base::DictValue state;
    state.Set("revision", revision_);
    state.Set("available", !bridge_.is_null()
        && Java_ArgonHomeBridge_isSupported(base::android::AttachCurrentThread(), bridge_));
    state.Set("canChange", service_ && service_->loaded()
        && service_->CanDefaultSearchProviderBeModifiedByUser());
    base::DictValue engine;
    const auto* current = service_ ? service_->GetDefaultSearchProvider() : nullptr;
    state.Set("enabled", current != nullptr);
    engine.Set("id", "browser:current");
    engine.Set("name", current ? base::UTF16ToUTF8(current->short_name()) : "");
    engine.Set("url", current ? current->GenerateSearchURL(service_->search_terms_data(), u"example").spec() : "");
    if (current) {
      const auto sample = current->GenerateSearchURL(service_->search_terms_data(), u"example");
      if (sample.GetHost() == "noai.duckduckgo.com") {
        engine.Set("id", "noai");
      } else {
        for (const auto& builtin : kBuiltins) {
          if (builtin.type != SEARCH_ENGINE_OTHER
              && current->GetEngineType(service_->search_terms_data()) == builtin.type) {
            engine.Set("id", builtin.id);
            break;
          }
        }
      }
    }
    state.Set("engine", std::move(engine));
    return state;
  }

  void ReplyAndNotify() {
    if (!IsJavascriptAllowed() || (service_ && !service_->loaded())) return;
    ++revision_;
    auto pending = std::move(pending_);
    pending_.clear();
    const auto state = State();
    for (const auto& id : pending) ResolveJavascriptCallback(id, state);
    FireWebUIListener("argon-search-state-changed", state);
  }

  void GetState(const base::ListValue& args) {
    if (args.size() != 1 || !args[0].is_string()) return;
    AllowJavascript();
    pending_.push_back(args[0].Clone());
    ReplyAndNotify();
  }

  void Reject(const base::Value& id) {
    RejectJavascriptCallback(id, base::Value("Search provider cannot be changed"));
  }

  void SetEngine(const base::ListValue& args) {
    if (args.size() != 2 || !args[0].is_string() || !args[1].is_dict()) return;
    AllowJavascript();
    if (!service_ || !service_->loaded() || bridge_.is_null()
        || !service_->CanDefaultSearchProviderBeModifiedByUser()) {
      Reject(args[0]);
      return;
    }
    const auto& choice = args[1].GetDict();
    const std::string* id = choice.FindString("id");
    const std::string* name = choice.FindString("name");
    const std::string* url = choice.FindString("url");
    const Builtin* builtin = nullptr;
    if (id) for (const auto& item : kBuiltins) if (*id == item.id) builtin = &item;
    std::string title = builtin ? builtin->name : name ? *name : "";
    std::string pattern = builtin ? builtin->url : url ? *url : "";
    if (title.empty() || title.size() > 256 || pattern.size() > 16384
        || pattern.find("{query}") == std::string::npos) {
      Reject(args[0]);
      return;
    }
    std::string sample = pattern;
    base::ReplaceSubstringsAfterOffset(&sample, 0, "{query}", "example");
    const GURL sample_url(sample);
    if (!sample_url.is_valid() || !sample_url.SchemeIsHTTPOrHTTPS()) { Reject(args[0]); return; }
    base::ReplaceSubstringsAfterOffset(&pattern, 0, "{query}", "{searchTerms}");
    TemplateURL* selected = nullptr;
    for (const auto& item : service_->GetTemplateURLs()) {
      if (item->type() != TemplateURL::NORMAL) continue;
      if (item->url() == pattern
          || (builtin && builtin->type != SEARCH_ENGINE_OTHER && item->prepopulate_id() > 0
              && item->GetEngineType(service_->search_terms_data()) == builtin->type)) {
        selected = item;
        break;
      }
    }
    bool added = false;
    if (!selected) {
      TemplateURLData data;
      data.SetShortName(base::UTF8ToUTF16(title));
      const std::string host = sample_url.GetHost();
      std::string keyword = host;
      for (int suffix = 2; service_->GetTemplateURLForKeyword(base::UTF8ToUTF16(keyword)); ++suffix) {
        if (suffix > 1000) { Reject(args[0]); return; }
        keyword = host + "-" + std::to_string(suffix);
      }
      data.SetKeyword(base::UTF8ToUTF16(keyword));
      data.SetURL(pattern);
      data.favicon_url = builtin && std::string(builtin->id) == "noai"
          ? GURL("https://duckduckgo.com/favicon.ico") : sample_url.Resolve("/favicon.ico");
      data.input_encodings = {"UTF-8"};
      auto candidate = std::make_unique<TemplateURL>(data);
      if (!candidate->SupportsReplacement(service_->search_terms_data())) { Reject(args[0]); return; }
      selected = service_->Add(std::move(candidate));
      added = selected != nullptr;
    }
    if (!selected) { Reject(args[0]); return; }
    if (selected != service_->GetDefaultSearchProvider()) {
      if (!service_->CanMakeDefault(selected)) {
        if (added) service_->Remove(selected);
        Reject(args[0]);
        return;
      }
      service_->SetUserSelectedDefaultSearchProvider(selected);
    }
    const auto state = State();
    ResolveJavascriptCallback(args[0], state);
  }

  void Focus(const base::ListValue& args) {
    if (args.size() != 2 || !args[0].is_string()) return;
    AllowJavascript();
    if (!args[1].is_string() || args[1].GetString().size() > 16384) {
      ResolveJavascriptCallback(args[0], base::Value(false));
      return;
    }
    JNIEnv* env = base::android::AttachCurrentThread();
    const bool focused = !bridge_.is_null()
        && Java_ArgonHomeBridge_focus(env, bridge_,
            base::android::ConvertUTF8ToJavaString(env, args[1].GetString()));
    ResolveJavascriptCallback(args[0], base::Value(focused));
  }

  void SetBounds(const base::ListValue& args) {
    if (args.size() != 5 || !IsJavascriptAllowed() || bridge_.is_null()) return;
    float values[5];
    for (size_t i = 0; i < 5; ++i) {
      if (!args[i].is_int() && !args[i].is_double()) return;
      const double value = args[i].is_int() ? args[i].GetInt() : args[i].GetDouble();
      if (!std::isfinite(value) || std::abs(value) > 1000000) return;
      values[i] = value;
    }
    if (values[2] <= 0 || values[3] <= 0 || values[4] < 0 || values[4] > 1) return;
    Java_ArgonHomeBridge_updateBounds(base::android::AttachCurrentThread(), bridge_,
        values[0], values[1], values[2], values[3], values[4]);
  }

  raw_ptr<TemplateURLService> service_ = nullptr;
  int revision_ = 0;
  std::vector<base::Value> pending_;
  base::android::ScopedJavaGlobalRef<jobject> bridge_;
};

static void JNI_ArgonHomeBridge_OnSearchBoxAlphaChanged(
    JNIEnv*, jlong observer, jfloat alpha, jboolean focused) {
  reinterpret_cast<ArgonSearchHandler*>(observer)->OnSearchBoxAlphaChanged(alpha, focused);
}

}  // namespace

std::unique_ptr<content::WebUIMessageHandler> CreateArgonSearchHandler() {
  return std::make_unique<ArgonSearchHandler>();
}

DEFINE_JNI(ArgonHomeBridge)
