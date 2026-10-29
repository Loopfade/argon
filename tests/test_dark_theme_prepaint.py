"""Exercise the production pre-paint observer with completed/restored page lifecycles."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dark_themes', ROOT / 'scripts/apply_dark_themes.py')
dark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dark)

PREFIX = r'''
#include <cstdint>
#include <iostream>
#include <memory>
#include <optional>
#include <stdexcept>
constexpr uint32_t SK_ColorBLACK = 0xFF000000;
namespace content {
struct NavigationHandle {
  bool primary = true, committed = true, same_document = false;
  bool IsInPrimaryMainFrame() const { return primary; }
  bool HasCommitted() const { return committed; }
  bool IsSameDocument() const { return same_document; }
};
struct WebContents {
  bool painted = false, night = true, has_theme = true;
  std::optional<uint32_t> background;
  bool CompletedFirstVisuallyNonEmptyPaint() const { return painted; }
  void SetPageBaseBackgroundColor(std::optional<uint32_t> color) { background = color; }
};
class WebContentsObserver {
 public:
  explicit WebContentsObserver(WebContents* contents) : contents_(contents) {}
  virtual ~WebContentsObserver() = default;
  virtual void DidFinishNavigation(NavigationHandle*) {}
  virtual void DidFirstVisuallyNonEmptyPaint() {}
 protected:
  WebContents* web_contents() { return contents_; }
 private:
  WebContents* contents_;
};
template<class T> class WebContentsUserData {
 public:
  explicit WebContentsUserData(WebContents&) {}
  static std::unique_ptr<T> Create(WebContents* contents) {
    return std::unique_ptr<T>(new T(contents));
  }
};
}
namespace night_mode {
class WebContentsThemeClient {
 public:
  static WebContentsThemeClient* FromWebContents(content::WebContents* contents) {
    static WebContentsThemeClient client;
    client.contents_ = contents;
    return contents->has_theme ? &client : nullptr;
  }
  bool IsNightModeEnabled() { return contents_->night; }
 private:
  content::WebContents* contents_ = nullptr;
};
}
#define WEB_CONTENTS_USER_DATA_KEY_DECL()
#define WEB_CONTENTS_USER_DATA_KEY_IMPL(T)
'''

SCENARIOS = r'''
void Check(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
int main() {
  try {
    content::WebContents page;
    auto observer = content::WebContentsUserData<PrePaintBackground>::Create(&page);
    auto* events = static_cast<content::WebContentsObserver*>(observer.get());
    content::NavigationHandle navigation;
    observer->Apply();
    Check(page.background == SK_ColorBLACK, "new dark page needs a placeholder");
    page.painted = true;
    events->DidFirstVisuallyNonEmptyPaint();
    Check(!page.background, "first paint must release the canvas");

    // A restored/prerendered Page retains its completed-paint state. Chromium
    // synthesizes the paint notification during commit, before navigation ends.
    events->DidFirstVisuallyNonEmptyPaint();
    events->DidFinishNavigation(&navigation);
    Check(!page.background, "restored painted page must not get a black canvas");
    observer->Apply();
    Check(!page.background, "attaching an already painted WebContents must preserve its canvas");

    page.painted = false;
    events->DidFinishNavigation(&navigation);
    Check(page.background == SK_ColorBLACK, "next new document needs its own placeholder");
    page.painted = true;
    events->DidFirstVisuallyNonEmptyPaint();
    page.painted = false;
    for (int kind = 0; kind < 3; ++kind) {
      content::NavigationHandle ignored;
      ignored.primary = kind != 0;
      ignored.committed = kind != 1;
      ignored.same_document = kind == 2;
      events->DidFinishNavigation(&ignored);
      Check(!page.background, "subframe, failed and same-document navigations must not reset the canvas");
    }
    page.night = false;
    observer->Apply();
    Check(!page.background, "light browser theme must keep the default canvas");
    page.night = true;
    page.has_theme = false;
    observer->Apply();
    Check(!page.background, "absent theme provider must keep the default canvas");
    std::cout << "PASS: production pre-paint observer, new/painted/restored pages and theme changes\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
'''


@unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
class PrePaintBackgroundTests(unittest.TestCase):
    def test_production_observer_preserves_already_painted_pages(self):
        hunks = dark.sections(dark.PATCH.read_text())[Path('chrome/browser/android/tab_android.cc')]
        added = next(after for _, after in hunks if 'class PrePaintBackground' in after)
        implementation = added[added.index('class PrePaintBackground'):]
        end = 'WEB_CONTENTS_USER_DATA_KEY_IMPL(PrePaintBackground);'
        implementation = implementation[:implementation.index(end) + len(end)]
        with tempfile.TemporaryDirectory() as temporary:
            source, binary = Path(temporary) / 'prepaint.cc', Path(temporary) / 'prepaint'
            source.write_text(PREFIX + implementation + SCENARIOS)
            subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: production pre-paint observer', result.stdout)


if __name__ == '__main__':
    unittest.main()
