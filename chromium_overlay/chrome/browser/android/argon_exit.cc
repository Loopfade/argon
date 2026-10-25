// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
#include "chrome/browser/android/argon_exit.h"

#include <set>
#include <string>
#include <utility>
#include <vector>

#include "base/android/callback_android.h"
#include "base/android/jni_string.h"
#include "base/barrier_closure.h"
#include "base/functional/bind.h"
#include "base/memory/weak_ptr.h"
#include "base/memory/raw_ptr.h"
#include "base/no_destructor.h"
#include "base/time/time.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/browsing_data/chrome_browsing_data_remover_constants.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/profiles/profile_manager.h"
#include "chrome/browser/ui/android/tab_model/tab_model.h"
#include "chrome/browser/ui/android/tab_model/tab_model_list.h"
#include "chrome/common/pref_names.h"
#include "components/download/public/common/download_item.h"
#include "components/prefs/pref_service.h"
#include "components/services/storage/public/mojom/local_storage_control.mojom.h"
#include "content/public/browser/browsing_data_remover.h"
#include "content/public/browser/download_manager.h"
#include "content/public/browser/render_process_host.h"
#include "content/public/browser/render_process_host_observer.h"
#include "content/public/browser/service_worker_context.h"
#include "content/public/browser/storage_partition.h"
#include "extensions/browser/extension_registrar.h"
#include "extensions/browser/extension_registry.h"
#include "extensions/common/extension.h"

// Includes Profile's JNI conversion before generated bindings.
#include "chrome/android/chrome_jni_headers/ArgonExitBridge_jni.h"

namespace argon {
namespace {
bool g_exiting = false;
struct SuspendedExtensions {
  base::WeakPtr<Profile> profile;
  std::vector<std::string> ids;
};
std::vector<SuspendedExtensions>& Suspended() {
  static base::NoDestructor<std::vector<SuspendedExtensions>> suspended;
  return *suspended;
}
std::vector<Profile*> Profiles() {
  std::vector<Profile*> profiles;
  for (Profile* profile : g_browser_process->profile_manager()->GetLoadedProfiles()) {
    profiles.push_back(profile);
    for (Profile* otr : profile->GetAllOffTheRecordProfiles()) profiles.push_back(otr);
  }
  return profiles;
}

// A stop request alone is not a barrier: wait for running/starting renderers to exit.
// New launches are rejected by ChromeContentBrowserClient while g_exiting is set.
class RendererBarrier : public content::RenderProcessHostObserver {
 public:
  explicit RendererBarrier(base::OnceClosure done) : done_(std::move(done)) {}
  void Start() {
    for (auto it = content::RenderProcessHost::AllHostsIterator(); !it.IsAtEnd(); it.Advance()) {
      auto* host = it.GetCurrentValue();
      if (!host->IsInitializedAndNotDead()) continue;
      pending_.insert(host);
      host->AddObserver(this);
    }
    auto hosts = pending_;
    for (auto* host : hosts) {
      if (host->IsReady()) host->Shutdown(0);
    }
    starting_ = false;
    MaybeDone();
  }
  void RenderProcessReady(content::RenderProcessHost* host) override { host->Shutdown(0); }
  void RenderProcessExited(content::RenderProcessHost* host,
                           const content::ChildProcessTerminationInfo&) override { Remove(host); }
  void RenderProcessHostDestroyed(content::RenderProcessHost* host) override { Remove(host); }
 private:
  void Remove(content::RenderProcessHost* host) {
    host->RemoveObserver(this);
    pending_.erase(host);
    MaybeDone();
  }
  void MaybeDone() {
    if (starting_ || !pending_.empty()) return;
    auto done = std::move(done_);
    delete this;
    std::move(done).Run();
  }
  bool starting_ = true;
  std::set<content::RenderProcessHost*> pending_;
  base::OnceClosure done_;
};

void PrepareModels(bool close_tabs, bool recovery, base::OnceCallback<void(bool)> done) {
  JNIEnv* env = jni_zero::AttachCurrentThread();
  // Hold Java references: closing an OTR model can remove native models from the list.
  using Model = std::pair<base::android::ScopedJavaGlobalRef<jobject>, bool>;
  std::vector<Model> models;
  for (TabModel* model : TabModelList::models()) {
    models.emplace_back(base::android::ScopedJavaGlobalRef<jobject>(model->GetJavaObject()),
                        close_tabs && !recovery);
  }
  if (TabModel* archived = TabModelList::GetArchivedTabModel()) {
    models.emplace_back(base::android::ScopedJavaGlobalRef<jobject>(archived->GetJavaObject()), close_tabs);
  }
  for (const auto& [model, close_model] : models) {
    // Recovery's fresh launch/VIEW tab must survive. Its old session has already
    // been discarded by ChromeTabbedActivity before restoring any tab state.
    if (!Java_ArgonExitBridge_prepareModel(env, model, close_model)) {
      std::move(done).Run(false);
      return;
    }
  }
  auto* barrier = new RendererBarrier(base::BindOnce(std::move(done), true));
  barrier->Start();
}

uint64_t RemovalMask(int types) {
  namespace data = chrome_browsing_data_remover;
  using Remover = content::BrowsingDataRemover;
  uint64_t mask = 0;
  if (types & 1) mask |= data::DATA_TYPE_HISTORY;
  if (types & 2) mask |= data::DATA_TYPE_SITE_DATA;
  if (types & 4) mask |= Remover::DATA_TYPE_CACHE;
  if (types & 8) mask |= data::DATA_TYPE_CONTENT_SETTINGS;
  if (types & 16) mask |= data::DATA_TYPE_FORM_DATA;
  if (types & 32) mask |= data::DATA_TYPE_PASSWORDS | data::DATA_TYPE_ACCOUNT_PASSWORDS;
  return mask;
}

class Removal : public content::BrowsingDataRemover::Observer {
 public:
  Removal(Profile* profile, int types, base::android::ScopedJavaGlobalRef<jobject> callback)
      : profile_(profile->GetWeakPtr()), remover_(profile->GetBrowsingDataRemover()),
        types_(types), callback_(std::move(callback)) {}
  void Start() {
    uint64_t mask = RemovalMask(types_);
    if ((types_ & 1) && !profile_->GetPrefs()->GetBoolean(prefs::kAllowDeletingBrowserHistory)) {
      blocked_types_ |= 1;
      mask &= ~RemovalMask(1);
    }
    if (!mask) { OnBrowsingDataRemoverDone(0); return; }
    remover_->AddObserver(this);
    observing_ = true;
    remover_->RemoveAndReply(base::Time(), base::Time::Max(), mask,
        content::BrowsingDataRemover::ORIGIN_TYPE_UNPROTECTED_WEB, this);
  }
  void OnBrowsingDataRemoverDone(uint64_t failed_data_types) override {
    if (observing_) remover_->RemoveObserver(this);
    int failed = blocked_types_;
    for (int bit = 1; bit <= 32; bit <<= 1) {
      if ((types_ & bit) && (RemovalMask(bit) & failed_data_types)) failed |= bit;
    }
    auto callback = std::move(callback_);
    auto profile = profile_;
    delete this;
    if (!profile) { base::android::RunIntCallbackAndroid(callback, 63); return; }
    // Flush the native preference updates made by deletion before killing the process.
    profile->GetPrefs()->CommitPendingWrite(base::BindOnce(
        [](base::android::ScopedJavaGlobalRef<jobject> callback, int failed) {
          base::android::RunIntCallbackAndroid(callback, failed);
        }, std::move(callback), failed));
  }
 private:
  base::WeakPtr<Profile> profile_;
  raw_ptr<content::BrowsingDataRemover> remover_;
  int types_;
  int blocked_types_ = 0;
  bool observing_ = false;
  base::android::ScopedJavaGlobalRef<jobject> callback_;
};
}  // namespace

bool IsExitInProgress() {
  return g_exiting || Java_ArgonExitBridge_isBlocked(jni_zero::AttachCurrentThread());
}
}  // namespace argon

namespace {
using argon::g_exiting;
using argon::PrepareModels;
using argon::Profiles;
using argon::Removal;
using argon::Suspended;
using argon::SuspendedExtensions;

static std::string JNI_ArgonExitBridge_ProfileKey(JNIEnv*, Profile* profile) {
  return profile->GetOriginalProfile()->GetBaseName().AsUTF8Unsafe();
}

static void JNI_ArgonExitBridge_Prepare(
    JNIEnv* env, Profile* profile, jboolean close_tabs, jboolean recovery,
    const jni_zero::JavaRef<jobject>& callback) {
  g_exiting = true;
  auto profiles = Profiles();
  std::vector<content::StoragePartition*> partitions;
  for (Profile* current : profiles) {
    std::vector<download::DownloadItem*> downloads;
    current->GetDownloadManager()->GetAllDownloads(&downloads);
    for (auto* item : downloads) {
      if (item->GetState() == download::DownloadItem::IN_PROGRESS) item->Pause();
    }
    auto* registry = extensions::ExtensionRegistry::Get(current);
    auto* registrar = extensions::ExtensionRegistrar::Get(current);
    if (registry && registrar) {
      SuspendedExtensions suspended{current->GetWeakPtr(), {}};
      for (const auto& extension : registry->enabled_extensions()) suspended.ids.push_back(extension->id());
      for (const auto& id : suspended.ids) registrar->TerminateExtension(id);
      Suspended().push_back(std::move(suspended));
    }
    current->ForEachLoadedStoragePartition([&](content::StoragePartition* partition) {
      partitions.push_back(partition);
    });
  }
  auto done = base::BindOnce([](base::android::ScopedJavaGlobalRef<jobject> callback,
                                bool close_tabs, bool recovery) {
    PrepareModels(close_tabs, recovery, base::BindOnce(
        [](base::android::ScopedJavaGlobalRef<jobject> callback, bool success) {
          base::android::RunBooleanCallbackAndroid(callback, success);
        }, std::move(callback)));
  }, base::android::ScopedJavaGlobalRef<jobject>(env, callback), static_cast<bool>(close_tabs),
     static_cast<bool>(recovery));
  if (partitions.empty()) { std::move(done).Run(); return; }
  auto stopped = base::BarrierClosure(partitions.size(), std::move(done));
  for (auto* partition : partitions) partition->GetServiceWorkerContext()->StopAllServiceWorkers(stopped);
}

static void JNI_ArgonExitBridge_Clear(JNIEnv* env, Profile* profile, jint types,
                                     const jni_zero::JavaRef<jobject>& callback) {
  if (!g_exiting || types < 0 || (types & ~63)) {
    base::android::RunIntCallbackAndroid(callback, 63);
    return;
  }
  // A LocalStorageControl round trip follows Flush on the same ordered pipe.
  // Preserve internal WebUI storage; removal is restricted to web origins below.
  auto clear = base::BindOnce([](base::WeakPtr<Profile> profile, int types,
                                 base::android::ScopedJavaGlobalRef<jobject> callback) {
    if (!profile) { base::android::RunIntCallbackAndroid(callback, 63); return; }
    auto* removal = new Removal(profile.get(), types, std::move(callback));
    removal->Start();
  }, profile->GetOriginalProfile()->GetWeakPtr(), types,
     base::android::ScopedJavaGlobalRef<jobject>(env, callback));
  std::vector<content::StoragePartition*> partitions;
  profile->ForEachLoadedStoragePartition([&](content::StoragePartition* partition) {
    partitions.push_back(partition);
  });
  if (partitions.empty()) { std::move(clear).Run(); return; }
  auto flushed = base::BarrierClosure(partitions.size(), std::move(clear));
  for (auto* partition : partitions) {
    auto* control = partition->GetLocalStorageControl();
    control->Flush();
    control->GetUsage(base::BindOnce([](base::RepeatingClosure done,
        std::vector<storage::mojom::StorageUsageInfoPtr>) { done.Run(); }, flushed));
  }
}

static void JNI_ArgonExitBridge_Resume(JNIEnv*, Profile*) {
  g_exiting = false;
  auto suspended = std::move(Suspended());
  Suspended().clear();
  for (const auto& item : suspended) {
    if (!item.profile) continue;
    auto* registrar = extensions::ExtensionRegistrar::Get(item.profile.get());
    if (!registrar) continue;
    for (const auto& id : item.ids) registrar->ReloadExtensionWithQuietFailure(id);
  }
}
}  // namespace

DEFINE_JNI(ArgonExitBridge)
