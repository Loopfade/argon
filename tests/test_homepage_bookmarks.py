"""Regression checks at the WebUI URL and native bookmark integration boundaries."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_homepage import homepage

ROOT = Path(__file__).resolve().parents[1]


class HomepageBookmarkTests(unittest.TestCase):
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
        self.assertLess(handler.index('// Validate the complete batch'), handler.index('model_->AddFolder'))
        self.assertIn('urls.contains(entry.url.spec())', handler)
        build = (homepage.OVERLAY / homepage.UI / 'BUILD.gn').read_text()
        self.assertIn('"argon_bookmarks_handler.cc"', build)
        self.assertIn('"//chrome/browser/bookmarks"', build)
        runner = (ROOT / '.github/ci/test-domain-policy.sh').read_text()
        self.assertEqual(runner.count('argon_bookmarks_handler.o'), 2)


if __name__ == '__main__':
    unittest.main()
