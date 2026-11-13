// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#include "chrome/browser/ui/webui/argon_home/argon_bookmarks_handler.h"

#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "base/functional/bind.h"
#include "base/memory/raw_ptr.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/utf_string_conversions.h"
#include "base/values.h"
#include "chrome/browser/bookmarks/bookmark_model_factory.h"
#include "chrome/browser/profiles/profile.h"
#include "components/bookmarks/browser/bookmark_model.h"
#include "components/bookmarks/browser/bookmark_model_observer.h"
#include "components/bookmarks/browser/bookmark_node.h"
#include "components/bookmarks/common/bookmark_pref_names.h"
#include "components/prefs/pref_service.h"
#include "content/public/browser/web_ui.h"
#include "content/public/browser/web_ui_message_handler.h"
#include "url/gurl.h"

namespace {

struct ImportEntry {
  std::u16string title;
  GURL url;
  std::vector<std::u16string> path;
  bool folder = false;
};

class ArgonBookmarksHandler final : public content::WebUIMessageHandler,
                                    public bookmarks::BookmarkModelObserver {
 public:
  ~ArgonBookmarksHandler() override { StopObserving(); }

  void RegisterMessages() override {
    web_ui()->RegisterMessageCallback(
        "getArgonBookmarks", base::BindRepeating(&ArgonBookmarksHandler::Get,
                                                base::Unretained(this)));
    web_ui()->RegisterMessageCallback(
        "importArgonBookmarks", base::BindRepeating(&ArgonBookmarksHandler::Import,
                                                   base::Unretained(this)));
  }

 private:
  void OnJavascriptAllowed() override {
    model_ = BookmarkModelFactory::GetForBrowserContext(Profile::FromWebUI(web_ui()));
    if (model_) model_->AddObserver(this);
  }
  void OnJavascriptDisallowed() override { StopObserving(); }
  void StopObserving() {
    if (model_) model_->RemoveObserver(this);
    model_ = nullptr;
  }

  const bookmarks::BookmarkNode* Destination() const {
    return model_->account_mobile_node() ? model_->account_mobile_node()
                                         : model_->mobile_node();
  }

  base::DictValue Node(const bookmarks::BookmarkNode& node, size_t depth,
                       size_t& count, bool& truncated) const {
    base::DictValue result;
    result.Set("id", base::NumberToString(node.id()));
    result.Set("name", base::UTF16ToUTF8(node.GetTitle()));
    if (node.is_url()) {
      result.Set("url", node.url().spec());
    } else {
      result.Set("type", "folder");
      base::ListValue children;
      if (depth < 64) {
        for (const auto& child : node.children()) {
          if (count >= 20000) { truncated = true; break; }
          if (child->is_url() && !child->url().SchemeIsHTTPOrHTTPS()) continue;
          ++count;
          children.Append(Node(*child, depth + 1, count, truncated));
        }
      } else if (!node.children().empty()) {
        truncated = true;
      }
      result.Set("children", std::move(children));
    }
    return result;
  }

  base::DictValue Snapshot() {
    base::DictValue result;
    const bool ready = model_ && model_->loaded();
    result.Set("available", model_ != nullptr);
    result.Set("ready", ready);
    result.Set("revision", revision_);
    result.Set("canEdit", ready && Profile::FromWebUI(web_ui())->GetPrefs()->GetBoolean(
                                      bookmarks::prefs::kEditBookmarksEnabled));
    base::ListValue roots;
    size_t count = 0;
    bool truncated = false;
    if (ready) {
      // Show mobile bookmarks directly. Other stores and managed roots retain
      // their folder boundaries; no bookmark is moved between profile stores.
      const auto* destination = Destination();
      for (const auto& root : model_->root_node()->children()) {
        if (root.get() == destination) {
          for (const auto& child : root->children()) {
            if (count >= 20000) { truncated = true; break; }
            if (child->is_url() && !child->url().SchemeIsHTTPOrHTTPS()) continue;
            ++count;
            roots.Append(Node(*child, 0, count, truncated));
          }
        } else if (!root->children().empty()) {
          if (count >= 20000) { truncated = true; break; }
          ++count;
          roots.Append(Node(*root, 0, count, truncated));
        }
      }
    }
    result.Set("roots", std::move(roots));
    result.Set("truncated", truncated);
    return result;
  }

  void Get(const base::ListValue& args) {
    if (args.size() != 1 || !args[0].is_string()) return;
    AllowJavascript();
    ResolveJavascriptCallback(args[0], Snapshot());
  }

  void CollectUrls(const bookmarks::BookmarkNode& node,
                   std::set<std::string>& urls) const {
    std::vector<const bookmarks::BookmarkNode*> pending{&node};
    while (!pending.empty()) {
      const auto* current = pending.back();
      pending.pop_back();
      if (current->is_url()) urls.insert(current->url().spec());
      for (const auto& child : current->children()) pending.push_back(child.get());
    }
  }

  void Import(const base::ListValue& args) {
    if ((args.size() != 2 && args.size() != 3) || !args[0].is_string() || !args[1].is_list()) return;
    AllowJavascript();
    auto fail = [&] { RejectJavascriptCallback(args[0], base::Value("bookmarks-import-failed")); };
    const bool restore = args.size() == 3 && args[2].is_string() && args[2].GetString() == "restore";
    if (args.size() == 3 && !restore) { fail(); return; }
    if (!model_ || !model_->loaded() || !Destination() ||
        !Profile::FromWebUI(web_ui())->GetPrefs()->GetBoolean(
            bookmarks::prefs::kEditBookmarksEnabled) || args[1].GetList().size() > 20000) {
      fail(); return;
    }
    // Validate the complete batch before touching the native model.
    std::vector<ImportEntry> entries;
    for (const auto& value : args[1].GetList()) {
      if (!value.is_dict()) { fail(); return; }
      const auto& dict = value.GetDict();
      const auto* title = dict.FindString("name");
      const auto* path = dict.FindList("path");
      if (!title || title->empty() || title->size() > 16384 || !path || path->size() > 64) {
        fail(); return;
      }
      ImportEntry entry;
      entry.title = base::UTF8ToUTF16(*title);
      entry.folder = dict.FindBool("folder").value_or(false);
      for (const auto& part : *path) {
        if (!part.is_string() || part.GetString().empty() || part.GetString().size() > 16384) {
          fail(); return;
        }
        entry.path.push_back(base::UTF8ToUTF16(part.GetString()));
      }
      if (!entry.folder) {
        const auto* url = dict.FindString("url");
        if (!url || url->size() > 16384) { fail(); return; }
        entry.url = GURL(*url);
        if (!entry.url.is_valid() || !entry.url.SchemeIsHTTPOrHTTPS()) { fail(); return; }
      }
      entries.push_back(std::move(entry));
    }
    std::set<std::string> urls;
    CollectUrls(*model_->root_node(), urls);
    importing_ = true;
    model_->BeginExtensiveChanges();
    for (const auto& entry : entries) {
      // Preserve an existing bookmark's title, folder and storage location.
      if (!restore && !entry.folder && urls.contains(entry.url.spec())) continue;
      const auto* parent = Destination();
      auto path = entry.path;
      if (entry.folder) path.push_back(entry.title);
      for (const auto& name : path) {
        const bookmarks::BookmarkNode* folder = nullptr;
        for (const auto& child : parent->children()) {
          if (child->is_folder() && child->GetTitle() == name) { folder = child.get(); break; }
        }
        parent = folder ? folder : model_->AddFolder(parent, parent->children().size(), name);
      }
      if (!entry.folder) {
        if (restore) {
          bool exists = false;
          for (const auto& child : parent->children()) {
            if (child->is_url() && child->url() == entry.url && child->GetTitle() == entry.title) {
              exists = true; break;
            }
          }
          if (exists) continue;
        }
        model_->AddURL(parent, parent->children().size(), entry.title, entry.url);
        urls.insert(entry.url.spec());
      }
    }
    model_->EndExtensiveChanges();
    importing_ = false;
    Changed();
    ResolveJavascriptCallback(args[0], Snapshot());
  }

  void Changed() {
    ++revision_;
    if (!importing_ && !extensive_changes_ && IsJavascriptAllowed()) {
      FireWebUIListener("argon-bookmarks-changed", Snapshot());
    }
  }
  void BookmarkModelLoaded(bool) override { Changed(); }
  void BookmarkModelBeingDeleted() override { StopObserving(); Changed(); }
  void BookmarkNodeMoved(const bookmarks::BookmarkNode*, size_t,
                         const bookmarks::BookmarkNode*, size_t) override { Changed(); }
  void BookmarkNodeAdded(const bookmarks::BookmarkNode*, size_t, bool) override { Changed(); }
  void BookmarkNodeRemoved(const bookmarks::BookmarkNode*, size_t,
                           const bookmarks::BookmarkNode*, const std::set<GURL>&,
                           const base::Location&) override { Changed(); }
  void BookmarkNodeChanged(const bookmarks::BookmarkNode*) override { Changed(); }
  void BookmarkNodeFaviconChanged(const bookmarks::BookmarkNode*) override {}
  void BookmarkNodeChildrenReordered(const bookmarks::BookmarkNode*) override { Changed(); }
  void BookmarkAllUserNodesRemoved(const std::set<GURL>&,
                                  const base::Location&) override { Changed(); }
  void BookmarkPermanentNodeVisibilityChanged(const bookmarks::BookmarkPermanentNode*) override { Changed(); }
  void ExtensiveBookmarkChangesBeginning() override { extensive_changes_ = true; }
  void ExtensiveBookmarkChangesEnded() override { extensive_changes_ = false; Changed(); }

  raw_ptr<bookmarks::BookmarkModel> model_ = nullptr;
  int revision_ = 0;
  bool importing_ = false;
  bool extensive_changes_ = false;
};

}  // namespace

std::unique_ptr<content::WebUIMessageHandler> CreateArgonBookmarksHandler() {
  return std::make_unique<ArgonBookmarksHandler>();
}
