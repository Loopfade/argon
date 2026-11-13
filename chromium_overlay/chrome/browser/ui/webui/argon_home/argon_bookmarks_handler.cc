// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#include "chrome/browser/ui/webui/argon_home/argon_bookmarks_handler.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include "base/functional/bind.h"
#include "base/location.h"
#include "base/memory/raw_ptr.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/utf_string_conversions.h"
#include "base/values.h"
#include "chrome/browser/bookmarks/bookmark_model_factory.h"
#include "chrome/browser/profiles/profile.h"
#include "components/bookmarks/browser/bookmark_model.h"
#include "components/bookmarks/browser/bookmark_client.h"
#include "components/bookmarks/browser/bookmark_model_observer.h"
#include "components/bookmarks/browser/bookmark_node.h"
#include "components/bookmarks/browser/bookmark_utils.h"
#include "components/bookmarks/browser/scoped_group_bookmark_actions.h"
#include "components/bookmarks/common/bookmark_metrics.h"
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
    web_ui()->RegisterMessageCallback(
        "editArgonBookmarks", base::BindRepeating(&ArgonBookmarksHandler::Edit,
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

  bool Editable(const bookmarks::BookmarkNode* node) const {
    return node && node != model_->root_node() && !node->is_permanent_node() &&
           !model_->client()->IsNodeManaged(node);
  }

  bool CanAdd(const bookmarks::BookmarkNode* node) const {
    return node && node->is_folder() && node != model_->root_node() &&
           !model_->client()->IsNodeManaged(node);
  }

  size_t Index(const bookmarks::BookmarkNode* node) const {
    const auto& children = node->parent()->children();
    for (size_t index = 0; index < children.size(); ++index) {
      if (children[index].get() == node) return index;
    }
    return children.size();
  }

  const bookmarks::BookmarkNode* ReadNode(const base::DictValue& operation,
                                         const char* key, bool default_parent = false) const {
    const auto* value = operation.FindString(key);
    if (!value) return nullptr;
    if (default_parent && value->empty()) return Destination();
    int64_t id;
    return base::StringToInt64(*value, &id) && id >= 0
               ? bookmarks::GetBookmarkNodeByID(model_, id) : nullptr;
  }

  bool ExpectedMatches(const base::DictValue& operation,
                       const bookmarks::BookmarkNode* node) const {
    const auto* expected = operation.FindDict("expected");
    if (!expected || !node || !node->parent()) return false;
    const auto* name = expected->FindString("name");
    const auto* parent = expected->FindString("parentId");
    const auto index = expected->FindInt("index");
    if (!name || *name != base::UTF16ToUTF8(node->GetTitle()) || !parent ||
        *parent != base::NumberToString(node->parent()->id()) || !index ||
        *index < 0 || static_cast<size_t>(*index) != Index(node)) return false;
    const auto* url = expected->FindString("url");
    return node->is_folder() ? !url : url && *url == node->url().spec();
  }

  base::DictValue Node(const bookmarks::BookmarkNode& node, size_t depth,
                       size_t& count, bool& truncated, size_t index) const {
    base::DictValue result;
    result.Set("id", base::NumberToString(node.id()));
    result.Set("name", base::UTF16ToUTF8(node.GetTitle()));
    result.Set("parentId", base::NumberToString(node.parent()->id()));
    result.Set("index", static_cast<int>(index));
    result.Set("editable", Editable(&node));
    result.Set("canAdd", CanAdd(&node));
    if (node.is_url()) {
      result.Set("url", node.url().spec());
    } else {
      result.Set("type", "folder");
      base::ListValue children;
      if (depth < 64) {
        size_t child_index = 0;
        for (const auto& child : node.children()) {
          const size_t position = child_index++;
          if (count >= 20000) { truncated = true; break; }
          if (child->is_url() && !child->url().SchemeIsHTTPOrHTTPS()) continue;
          ++count;
          children.Append(Node(*child, depth + 1, count, truncated, position));
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
    result.Set("defaultParentId", ready ? base::NumberToString(Destination()->id()) : "");
    result.Set("canEdit", ready && Profile::FromWebUI(web_ui())->GetPrefs()->GetBoolean(
                                      bookmarks::prefs::kEditBookmarksEnabled));
    base::ListValue roots;
    size_t count = 0;
    bool truncated = false;
    if (ready) {
      // Show mobile bookmarks directly. Other stores and managed roots retain
      // their folder boundaries; no bookmark is moved between profile stores.
      const auto* destination = Destination();
      size_t root_index = 0;
      for (const auto& root : model_->root_node()->children()) {
        const size_t root_position = root_index++;
        if (root.get() == destination) {
          size_t child_index = 0;
          for (const auto& child : root->children()) {
            const size_t position = child_index++;
            if (count >= 20000) { truncated = true; break; }
            if (child->is_url() && !child->url().SchemeIsHTTPOrHTTPS()) continue;
            ++count;
            roots.Append(Node(*child, 0, count, truncated, position));
          }
        } else if (!root->children().empty()) {
          if (count >= 20000) { truncated = true; break; }
          ++count;
          roots.Append(Node(*root, 0, count, truncated, root_position));
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

  void Edit(const base::ListValue& args) {
    if (args.size() != 2 || !args[0].is_string()) return;
    AllowJavascript();
    auto fail = [&] { RejectJavascriptCallback(args[0], base::Value("bookmarks-edit-failed")); };
    if (!model_ || !model_->loaded() || !args[1].is_dict() ||
        !Profile::FromWebUI(web_ui())->GetPrefs()->GetBoolean(
            bookmarks::prefs::kEditBookmarksEnabled)) { fail(); return; }
    const auto& operation = args[1].GetDict();
    const auto* action = operation.FindString("action");
    if (!action || (*action != "create" && *action != "update" &&
                    *action != "move" && *action != "remove")) { fail(); return; }
    const bool create = *action == "create", remove = *action == "remove";
    const auto* node = create ? nullptr : ReadNode(operation, "id");
    if (!create && (!Editable(node) || !ExpectedMatches(operation, node))) { fail(); return; }
    const auto* parent = remove ? node->parent() : ReadNode(operation, "parentId", true);
    if (!CanAdd(parent)) { fail(); return; }
    // All validation precedes the first mutation. Native Move uses an insertion
    // anchor, so hidden/non-web bookmarks retain their order too.
    size_t index = parent->children().size();
    if (!remove) {
      const auto* before_id = operation.FindString("beforeId");
      if (!before_id) { fail(); return; }
      if (!before_id->empty()) {
        const auto* before = ReadNode(operation, "beforeId");
        if (!before || before == node || before->parent() != parent) { fail(); return; }
        index = Index(before);
      }
      for (const auto* ancestor = parent; ancestor; ancestor = ancestor->parent()) {
        if (ancestor == node) { fail(); return; }
      }
    }
    std::u16string title;
    GURL url;
    const bool folder = operation.FindBool("folder").value_or(false);
    if (create || *action == "update") {
      const auto* name = operation.FindString("name");
      if (!name || name->empty() || name->size() > 16384 ||
          (!create && folder != node->is_folder())) { fail(); return; }
      title = base::UTF8ToUTF16(*name);
      if (!folder) {
        const auto* address = operation.FindString("url");
        if (!address || address->size() > 16384) { fail(); return; }
        url = GURL(*address);
        if (!url.is_valid() || !url.SchemeIsHTTPOrHTTPS()) { fail(); return; }
      }
    }
    const std::string id = create ? "" : base::NumberToString(node->id());
    std::string result_id = id;
    importing_ = true;
    {
      // Keep ordinary user edits undoable in Chromium; extensive changes would
      // suspend the undo tracker. Observer delivery to this WebUI is coalesced.
      bookmarks::ScopedGroupBookmarkActions group(model_);
      constexpr auto source = bookmarks::metrics::BookmarkEditSource::kUser;
      if (create) {
        const auto* added = folder ? model_->AddFolder(parent, index, title)
            : model_->AddURL(parent, index, title, url, nullptr, std::nullopt, std::nullopt, true);
        result_id = base::NumberToString(added->id());
      } else if (remove) {
        // As in the previous homepage editor, deleting a folder keeps its
        // contents in the parent rather than silently deleting all its sites.
        while (node->is_folder() && !node->children().empty()) {
          model_->Move(node->children().front().get(), parent, Index(node));
        }
        model_->Remove(node, source, FROM_HERE);
      } else {
        if (*action == "update") {
          model_->SetTitle(node, title, source);
          if (!folder) model_->SetURL(node, url, source);
        }
        model_->Move(node, parent, index);
      }
    }
    importing_ = false;
    Changed();
    base::DictValue result;
    result.Set("snapshot", Snapshot());
    result.Set("id", result_id);
    ResolveJavascriptCallback(args[0], result);
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
