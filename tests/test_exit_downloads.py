"""Exercise the production C++ download guard with late initialization and Resume events."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = r"""
#include <algorithm>
#include <cassert>
#include <functional>
#include <iostream>
#include <memory>
#include <set>
#include <utility>
#include <vector>
template<class T> class raw_ptr {
 public:
  raw_ptr(T* value = nullptr) : value_(value) {}
  T* get() const { return value_; }
  T* operator->() const { return value_; }
  operator T*() const { return value_; }
  raw_ptr& operator=(T* value) { value_ = value; return *this; }
 private: T* value_;
};
namespace base {
template<class Signature> class OnceCallback;
template<> class OnceCallback<void(bool)> {
 public:
  template<class F> OnceCallback(F callback) : callback_(std::move(callback)) {}
  void Run(bool success) && { auto callback = std::move(callback_); callback(success); }
 private: std::function<void(bool)> callback_;
};
}
namespace download {
class DownloadItem {
 public:
  enum State { IN_PROGRESS, COMPLETE };
  class Observer {
   public:
    virtual ~Observer() = default;
    virtual void OnDownloadUpdated(DownloadItem*) {}
    virtual void OnDownloadDestroyed(DownloadItem*) {}
  };
  ~DownloadItem() {
    auto copy = observers;
    for (auto* observer : copy) observer->OnDownloadDestroyed(this);
    assert(observers.empty());
  }
  State GetState() const { return state; }
  bool IsPaused() const { return paused; }
  void AddObserver(Observer* observer) {
    assert(std::find(observers.begin(), observers.end(), observer) == observers.end());
    observers.push_back(observer);
  }
  void RemoveObserver(Observer* observer) { std::erase(observers, observer); }
  void Pause() { paused = true; ++pauses; Notify(); }
  void Resume() { paused = false; Notify(); }
  void Notify() {
    auto copy = observers;
    for (auto* observer : copy) observer->OnDownloadUpdated(this);
  }
  bool paused = false;
  int pauses = 0;
  State state = IN_PROGRESS;
  std::vector<Observer*> observers;
};
}
namespace content {
class DownloadManager {
 public:
  using DownloadVector = std::vector<raw_ptr<download::DownloadItem>>;
  class Observer {
   public:
    virtual ~Observer() = default;
    virtual void OnDownloadCreated(DownloadManager*, download::DownloadItem*) {}
    virtual void OnManagerInitialized() {}
    virtual void ManagerGoingDown(DownloadManager*) {}
  };
  ~DownloadManager() { Shutdown(); }
  void AddObserver(Observer* observer) { observers.push_back(observer); }
  void RemoveObserver(Observer* observer) { std::erase(observers, observer); }
  bool IsManagerInitialized() { return initialized; }
  void GetAllDownloads(DownloadVector* result) { result->assign(items.begin(), items.end()); }
  void Create(download::DownloadItem* item) {
    if (std::find(items.begin(), items.end(), item) == items.end()) items.push_back(item);
    auto copy = observers;
    for (auto* observer : copy) observer->OnDownloadCreated(this, item);
  }
  void Initialize() {
    initialized = true;
    auto copy = observers;
    for (auto* observer : copy) observer->OnManagerInitialized();
  }
  void Shutdown() {
    if (closed) return;
    closed = true;
    auto copy = observers;
    for (auto* observer : copy) observer->ManagerGoingDown(this);
    assert(observers.empty());
  }
  bool initialized = false;
  bool closed = false;
  std::vector<Observer*> observers;
  std::vector<download::DownloadItem*> items;
};
}
bool g_exiting = true;
"""
SCENARIOS = r"""
int main() {
  {
    content::DownloadManager manager;
    download::DownloadItem history, fresh, completed;
    completed.state = download::DownloadItem::COMPLETE;
    int callbacks = 0;
    {
      DownloadGuard guard(&manager);
      guard.PauseWhenReady([&](bool ready) { assert(ready); ++callbacks; });
      assert(callbacks == 0);
      manager.Create(&history);
      assert(history.paused && history.pauses == 1);
      assert(callbacks == 0);
      manager.Initialize();
      assert(callbacks == 1);
      manager.Create(&history);
      assert(history.observers.size() == 1);
      manager.Create(&fresh);
      fresh.Resume();
      assert(fresh.paused && fresh.pauses == 2);
      manager.Create(&completed);
      assert(completed.pauses == 0);
      auto transient = std::make_unique<download::DownloadItem>();
      manager.Create(transient.get());
      transient.reset();
      g_exiting = false;
      fresh.Resume();
      assert(!fresh.paused);
    }
    assert(history.observers.empty() && fresh.observers.empty() && completed.observers.empty());
  }
  g_exiting = true;
  {
    content::DownloadManager manager;
    download::DownloadItem item;
    manager.Create(&item);
    manager.initialized = true;
    DownloadGuard guard(&manager);
    bool done = false;
    guard.PauseWhenReady([&](bool ready) { assert(ready && item.paused); done = true; });
    assert(done);
  }
  {
    content::DownloadManager manager;
    download::DownloadItem item;
    DownloadGuard guard(&manager);
    int failures = 0;
    guard.PauseWhenReady([&](bool ready) { assert(!ready); ++failures; });
    manager.Create(&item);
    manager.Shutdown();
    assert(failures == 1 && item.observers.empty());
    guard.PauseWhenReady([&](bool ready) { assert(!ready); ++failures; });
    assert(failures == 2);
  }
  std::cout << "PASS: production download guard, startup, Resume and teardown" << std::endl;
}
"""

class ExitDownloadTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("g++"), "C++ compiler required")
    def test_late_history_resume_and_manager_destruction(self):
        native = (ROOT / "chromium_overlay/chrome/browser/android/argon_exit.cc").read_text()
        guard = native.split("// BEGIN ARGON_DOWNLOAD_GUARD", 1)[1].split("// END ARGON_DOWNLOAD_GUARD", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "guard.cc"
            binary = Path(directory) / "guard"
            source.write_text(ADAPTER + guard + SCENARIOS)
            compiled = subprocess.run(["g++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                str(source), "-o", str(binary)], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: production download guard", result.stdout)
