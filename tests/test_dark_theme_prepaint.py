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
class WebContentsObserver;
struct NavigationHandle {
  bool primary = true, committed = true, same_document = false;
  bool IsInPrimaryMainFrame() const { return primary; }
  bool HasCommitted() const { return committed; }
  bool IsSameDocument() const { return same_document; }
};
struct WebContents {
  bool painted = false, night = true, has_theme = false;
  std::optional<uint32_t> background;
  std::shared_ptr<WebContentsObserver> user_data;
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
  static void CreateForWebContents(WebContents* contents) {
    if (!contents->user_data) contents->user_data.reset(new T(contents));
  }
  static T* FromWebContents(WebContents* contents) {
    return static_cast<T*>(contents->user_data.get());
  }
};
}
namespace night_mode {
class WebContentsThemeClient {
 public:
  static void CreateForWebContents(content::WebContents* contents) {
    contents->has_theme = true;
  }
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
    AttachWebContents(page);
    Check(page.background == SK_ColorBLACK,
          "new dark tab needs a placeholder before its first navigation commits");
    auto* observer = PrePaintBackground::FromWebContents(&page);
    auto* events = static_cast<content::WebContentsObserver*>(observer);
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
    AttachWebContents(page);
    Check(!page.background, "reattaching a painted tab must preserve its canvas");

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
    content::WebContents light_page;
    light_page.night = false;
    AttachWebContents(light_page);
    Check(!light_page.background, "new light tab must keep the default canvas");
    std::cout << "PASS: production pre-paint observer, new/painted/restored pages and theme changes\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
'''


def run_prepaint_flow(tab_source):
    """Compile the real observer and attachment calls with WebContents adapters."""
    end = 'WEB_CONTENTS_USER_DATA_KEY_IMPL(PrePaintBackground);'
    implementation = tab_source[tab_source.index('class PrePaintBackground'):]
    implementation = implementation[:implementation.index(end) + len(end)]
    calls = (
        'night_mode::WebContentsThemeClient::CreateForWebContents(web_contents_.get());',
        'PrePaintBackground::CreateForWebContents(web_contents_.get());',
        'PrePaintBackground::FromWebContents(web_contents_.get())->Apply();',
    )
    if any(tab_source.count(call) != 1 for call in calls):
        raise ValueError('Pinned TabAndroid theme initialization changed')
    start = min(tab_source.index(call) for call in calls)
    stop = max(tab_source.index(call) + len(call) for call in calls)
    attachment = '''
void AttachWebContents(content::WebContents& page) {
  struct { content::WebContents* value; auto* get() { return value; } } web_contents_{&page};
''' + tab_source[start:stop] + '\n}\n'
    with tempfile.TemporaryDirectory() as temporary:
        source, binary = Path(temporary) / 'prepaint.cc', Path(temporary) / 'prepaint'
        source.write_text(PREFIX + implementation + attachment + SCENARIOS)
        subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                        str(source), '-o', str(binary)], check=True, capture_output=True, text=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return result.stdout


@unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
class PrePaintBackgroundTests(unittest.TestCase):
    def test_production_observer_preserves_already_painted_pages(self):
        hunks = dark.sections(dark.PATCH.read_text())[Path('chrome/browser/android/tab_android.cc')]
        source = '\n'.join(after for _, after in hunks)
        self.assertIn('PASS: production pre-paint observer', run_prepaint_flow(source))


if __name__ == '__main__':
    unittest.main()
