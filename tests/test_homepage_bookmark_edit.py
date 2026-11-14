"""Run production native mutations against the pinned BookmarkModel API contract."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_homepage import homepage

FIXTURE = Path(__file__).resolve().parent / 'fixtures/homepage/bookmark_edit_flow.cc.in'
BOOKMARK_EDIT_APIS = (
    'components/bookmarks/browser/bookmark_model.h',
    'components/bookmarks/browser/bookmark_client.h',
    'components/bookmarks/browser/bookmark_utils.h',
    'components/bookmarks/browser/scoped_group_bookmark_actions.h',
    'components/bookmarks/common/bookmark_metrics.h',
)


def verify_bookmark_edit_api(sources):
    model = ' '.join(sources[BOOKMARK_EDIT_APIS[0]].split())
    for signature in (
        'void Move(const BookmarkNode* node, const BookmarkNode* new_parent, size_t index);',
        'void Remove(const BookmarkNode* node, metrics::BookmarkEditSource source, const base::Location& location);',
        'void SetTitle(const BookmarkNode* node, const std::u16string& title, metrics::BookmarkEditSource source);',
        'void SetURL(const BookmarkNode* node, const GURL& url, metrics::BookmarkEditSource source);',
        'bool added_by_user = false);',
    ):
        if signature not in model:
            raise ValueError('Review the pinned native bookmark mutation API: ' + signature)
    for path, signature in (
        (BOOKMARK_EDIT_APIS[1], 'virtual bool IsNodeManaged(const BookmarkNode* node) = 0;'),
        (BOOKMARK_EDIT_APIS[2], 'const BookmarkNode* GetBookmarkNodeByID(const BookmarkModel* model, int64_t id);'),
        (BOOKMARK_EDIT_APIS[3], 'explicit ScopedGroupBookmarkActions(BookmarkModel* model);'),
        (BOOKMARK_EDIT_APIS[4], 'kUser = 0,'),
    ):
        if signature not in ' '.join(sources[path].split()):
            raise ValueError('Review the pinned native bookmark helper API: ' + path)


@unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
class HomepageBookmarkEditTests(unittest.TestCase):
    def test_production_mutations_keep_order_and_reject_unsafe_or_stale_changes(self):
        source = (homepage.OVERLAY / homepage.UI / 'argon_bookmarks_handler.cc').read_text()
        methods = '\n'.join(homepage.method_source(source, signature) for signature in (
            'const bookmarks::BookmarkNode* Destination()',
            'bool Editable(', 'bool CanAdd(', 'size_t Index(',
            'const bookmarks::BookmarkNode* ReadNode(', 'bool ExpectedMatches(', 'void Edit(',
        ))
        program = FIXTURE.read_text().replace('ARGON_BOOKMARK_METHODS', methods)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'edit.cc'
            binary = Path(directory) / 'edit'
            path.write_text(program)
            result = subprocess.run(['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror',
                                     str(path), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS: production bookmark edits', result.stdout)


if __name__ == '__main__':
    unittest.main()
