"""Regression checks at the WebUI URL and native bookmark integration boundaries."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_homepage import homepage

ROOT = Path(__file__).resolve().parents[1]
WEBUI_HANDLER = 'content/public/browser/web_ui_message_handler.h'
WEBUI_FIXTURE = ROOT / 'tests/fixtures/homepage/web_ui_message_handler.h.in'


def compile_bookmark_snapshot(webui_header, snapshot=None):
    """Compile the production snapshot against Chromium's actual WebUI accessor.

    Other dependencies only need declarations for this syntax check. The Android
    job still compiles the complete handler against the full Chromium headers.
    """
    accessor = homepage.method_source(webui_header, 'WebUI* web_ui()')
    if snapshot is None:
        handler = (homepage.OVERLAY / homepage.UI / 'argon_bookmarks_handler.cc').read_text()
        snapshot = homepage.method_source(handler, 'base::DictValue Snapshot()')
    program = r'''
#include <cstddef>
#include <memory>
#include <string>
#include <utility>
#include <vector>
using std::size_t;
namespace base {
std::string NumberToString(long long);
struct DictValue { template <typename T> void Set(const char*, T&&); };
struct ListValue { void Append(DictValue); };
}
struct GURL { bool SchemeIsHTTPOrHTTPS() const; };
namespace bookmarks {
namespace prefs { inline constexpr char kEditBookmarksEnabled[] = "edit_bookmarks"; }
struct BookmarkNode {
  long long id() const;
  const std::vector<std::unique_ptr<BookmarkNode>>& children() const;
  bool is_url() const;
  const GURL& url() const;
};
struct BookmarkModel {
  bool loaded() const;
  const BookmarkNode* root_node() const;
};
}
struct WebUI {};
struct PrefService { bool GetBoolean(const char*) const; };
struct Profile {
  static Profile* FromWebUI(WebUI*);
  PrefService* GetPrefs();
};
class WebUIMessageHandler {
 protected:
''' + accessor + r'''
 private:
  WebUI* web_ui_ = nullptr;
};
class SnapshotHandler : public WebUIMessageHandler {
  bookmarks::BookmarkModel* model_ = nullptr;
  int revision_ = 0;
  const bookmarks::BookmarkNode* Destination() const;
  base::DictValue Node(const bookmarks::BookmarkNode&, size_t, size_t&, bool&, size_t) const;
 public:
''' + snapshot + '\n};\n'
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'snapshot.cc'
        source.write_text(program)
        return subprocess.run(['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                               '-fsyntax-only', str(source)], capture_output=True, text=True)


def verify_bookmark_webui_api(header):
    accessor = homepage.method_source(header, 'WebUI* web_ui()')
    expected = homepage.method_source(WEBUI_FIXTURE.read_text(), 'WebUI* web_ui()')
    if accessor != expected:
        raise ValueError('Review the pinned WebUIMessageHandler accessor and bookmark fixture')
    result = compile_bookmark_snapshot(header)
    if result.returncode:
        raise AssertionError('Bookmark snapshot does not compile against pinned WebUI API:\n'
                             + result.stderr)


class HomepageBookmarkTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
    def test_production_snapshot_compiles_against_webui_accessor(self):
        verify_bookmark_webui_api(WEBUI_FIXTURE.read_text())

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
    def test_const_snapshot_reproduces_the_android_compiler_error(self):
        handler = (homepage.OVERLAY / homepage.UI / 'argon_bookmarks_handler.cc').read_text()
        snapshot = homepage.method_source(handler, 'base::DictValue Snapshot()')
        self.assertIn('Snapshot() {', snapshot)
        broken = snapshot.replace('Snapshot() {', 'Snapshot() const {', 1)
        result = compile_bookmark_snapshot(WEBUI_FIXTURE.read_text(), broken)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('web_ui()', result.stderr)
        self.assertIn('const', result.stderr)

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
    def test_production_resource_filter_accepts_webui_fragments(self):
        source = (homepage.OVERLAY / homepage.UI / 'argon_home_ui.cc').read_text()
        methods = '\n'.join(homepage.method_source(source, signature) for signature in (
            'std::string_view ResourcePath(', 'std::string_view GetResource('))
        program = '#include <string_view>\n#include <cassert>\n'
        program += homepage.resource_header(homepage.OVERLAY / homepage.RESOURCES)
        program += '\n' + methods + r'''
int main() {
  // URLDataSource::URLToRequestPath retains everything after the first slash,
  // including #ref. An HTTP fixture server cannot exercise this contract.
  for (auto path : {"", "#add-site?url=https%3A%2F%2Fexample.com&title=Site",
                    "index.html#add-site?url=https%3A%2F%2Fexample.com",
                    "?ignored=1#fragment"}) assert(GetResource(path) == kArgonHomeHtml);
  assert(GetResource("browser-bookmarks.js?1#ref") == kArgonBrowserBookmarks);
  assert(GetResource("app.js#fragment") == kArgonHomeJs);
  assert(GetResource("unknown#add-site?url=x").empty());
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'resource.cc'
            binary = Path(directory) / 'resource'
            path.write_text(program)
            subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror', str(path), '-o', str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True)

    def test_native_bookmark_handler_is_bundled_linked_and_compiled(self):
        handler = (homepage.OVERLAY / homepage.UI / 'argon_bookmarks_handler.cc').read_text()
        self.assertIn('BookmarkModelFactory::GetForBrowserContext', handler)
        self.assertIn('bookmarks::prefs::kEditBookmarksEnabled', handler)
        self.assertIn('model_->AddObserver(this)', handler)
        self.assertIn('model_->RemoveObserver(this)', handler)
        importer = homepage.method_source(handler, 'void Import(')
        self.assertLess(importer.index('// Validate the complete batch'), importer.index('model_->AddFolder'))
        self.assertIn('urls.contains(entry.url.spec())', handler)
        build = (homepage.OVERLAY / homepage.UI / 'BUILD.gn').read_text()
        self.assertIn('"argon_bookmarks_handler.cc"', build)
        self.assertIn('"//chrome/browser/bookmarks"', build)
        runner = (ROOT / '.github/ci/test-domain-policy.sh').read_text()
        self.assertEqual(runner.count('argon_bookmarks_handler.o'), 2)


if __name__ == '__main__':
    unittest.main()
