"""Exercise the Android launcher instead of treating an internal href as a click test."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_homepage_native import BRIDGE, FIXTURES, STUBS

BOOKMARK_MANAGER_APIS = (
    'chrome/browser/bookmarks/android/java/src/org/chromium/chrome/browser/bookmarks/BookmarkManagerOpener.java',
    'chrome/android/java/src/org/chromium/chrome/browser/bookmarks/BookmarkManagerOpenerImpl.java',
    'components/bookmarks/common/android/java/src/org/chromium/components/bookmarks/BookmarkId.java',
)


def verify_bookmark_manager_api(sources):
    def parameters(source, signature):
        matches = re.findall(signature + r'\s*\(([^)]*)\)', source)
        if not matches:
            raise ValueError('Review the pinned Android bookmark launcher API')
        return [[part.strip().split()[0] for part in re.sub(r'@\w+\s+', '', args).split(',')]
                for args in matches]

    for path in BOOKMARK_MANAGER_APIS[:2]:
        if ['Activity', 'Tab', 'Profile', 'BookmarkId'] not in parameters(
                sources[path], r'void\s+showBookmarkManager'):
            raise ValueError('Review the pinned four-argument bookmark launcher API')
    implementation = sources[BOOKMARK_MANAGER_APIS[1]]
    if 'public BookmarkManagerOpenerImpl()' not in implementation:
        raise ValueError('Review the pinned bookmark launcher constructor')
    if ['long', 'int'] not in parameters(sources[BOOKMARK_MANAGER_APIS[2]], r'public\s+BookmarkId'):
        raise ValueError('Review the pinned bookmark ID constructor')


def run_bookmark_manager_flow():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        sources = dict(STUBS)
        sources['org/chromium/chrome/browser/ntp/ArgonHomeBridge.java'] = BRIDGE.read_text()
        sources['org/chromium/chrome/browser/omnibox/NewTabPageDelegate.java'] = (
            FIXTURES / 'NewTabPageDelegate.java.in').read_text()
        sources['BookmarkManagerFlow.java'] = r'''
import org.chromium.chrome.browser.ChromeTabbedActivity;
import org.chromium.chrome.browser.bookmarks.BookmarkManagerOpenerImpl;
import org.chromium.chrome.browser.ntp.ArgonHomeBridge;
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.components.bookmarks.BookmarkType;
import org.chromium.content_public.browser.WebContents;
import org.chromium.ui.base.WindowAndroid;
import org.chromium.url.GURL;

public class BookmarkManagerFlow {
  static boolean open(WebContents contents, long folder) throws Exception {
    var method = ArgonHomeBridge.class.getDeclaredMethod(
        "openBookmarkManager", WebContents.class, long.class);
    method.setAccessible(true);
    return (boolean) method.invoke(null, contents, folder);
  }
  static void check(boolean value) { if (!value) throw new AssertionError(); }
  public static void main(String[] args) throws Exception {
    var contents = new WebContents();
    var window = contents.window = new WindowAndroid();
    var activity = new ChromeTabbedActivity(); window.activity = activity;
    var tab = activity.tab = new Tab(); tab.contents = contents;
    tab.url = new GURL("chrome://argon/");
    check(open(contents, -1));
    check(BookmarkManagerOpenerImpl.activity == activity);
    check(BookmarkManagerOpenerImpl.tab == tab);
    check(BookmarkManagerOpenerImpl.profile == tab.getProfile());
    check(BookmarkManagerOpenerImpl.folder == null);
    check(open(contents, Long.MAX_VALUE));
    check(BookmarkManagerOpenerImpl.folder.getId() == Long.MAX_VALUE);
    check(BookmarkManagerOpenerImpl.folder.getType() == BookmarkType.NORMAL);
    window.isTablet = true;
    check(open(contents, 123)); // The platform launcher owns tablet routing too.
    int opens = BookmarkManagerOpenerImpl.opens;
    check(!open(contents, -2));
    activity.finishing = true; check(!open(contents, -1)); activity.finishing = false;
    activity.destroyed = true; check(!open(contents, -1)); activity.destroyed = false;
    contents.destroyed = true; check(!open(contents, -1)); contents.destroyed = false;
    tab.destroyed = true; check(!open(contents, -1)); tab.destroyed = false;
    tab.incognito = true; check(!open(contents, -1)); tab.incognito = false;
    tab.contents = new WebContents(); check(!open(contents, -1)); tab.contents = contents;
    tab.url = new GURL("https://example.com/"); check(!open(contents, -1));
    activity.tab = null; check(!open(contents, -1));
    window.activity = null; check(!open(contents, -1));
    check(!open(null, -1));
    check(BookmarkManagerOpenerImpl.opens == opens);
    System.out.println("PASS: native bookmark launcher, folder IDs, profile and activity lifetime");
  }
}
'''
        for name, content in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        compiled = subprocess.run(['javac', '--release', '17', '-d', str(root),
                                   *map(str, root.rglob('*.java'))], capture_output=True, text=True)
        if compiled.returncode:
            raise AssertionError(compiled.stderr)
        result = subprocess.run(['java', '-cp', str(root), 'BookmarkManagerFlow'],
                                check=True, capture_output=True, text=True)
        return result.stdout


class HomepageBookmarkManagerTests(unittest.TestCase):
    def test_fixture_retains_the_real_profile_and_folder_arguments(self):
        paths = ('org/chromium/chrome/browser/bookmarks/BookmarkManagerOpener.java',
                 'org/chromium/chrome/browser/bookmarks/BookmarkManagerOpenerImpl.java',
                 'org/chromium/components/bookmarks/BookmarkId.java')
        sources = {actual: STUBS[stub] for actual, stub in zip(BOOKMARK_MANAGER_APIS, paths)}
        verify_bookmark_manager_api(sources)
        changed = dict(sources)
        changed[BOOKMARK_MANAGER_APIS[0]] = sources[BOOKMARK_MANAGER_APIS[0]].replace(
            'Profile profile, ', '')
        with self.assertRaisesRegex(ValueError, 'four-argument'):
            verify_bookmark_manager_api(changed)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
    def test_production_bridge_opens_the_native_manager_and_checks_activity_lifetime(self):
        self.assertIn('PASS: native bookmark launcher', run_bookmark_manager_flow())


if __name__ == '__main__':
    unittest.main()
