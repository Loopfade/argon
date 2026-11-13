"""Exercise the production menu command without replacing the source tab."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_homepage import homepage


def run_add_flow(compiler=None, release='17'):
    sources = {
        'android/net/Uri.java': '''package android.net;
public class Uri {
 public static String encode(String value) {
  return java.net.URLEncoder.encode(value, java.nio.charset.StandardCharsets.UTF_8).replace("+", "%20");
 }
}''',
        'org/chromium/build/annotations/NullMarked.java': '''package org.chromium.build.annotations;
public @interface NullMarked {}''',
        'org/chromium/build/annotations/Nullable.java': '''package org.chromium.build.annotations;
@java.lang.annotation.Target({java.lang.annotation.ElementType.TYPE_USE}) public @interface Nullable {}''',
        'org/chromium/url/GURL.java': '''package org.chromium.url;
public class GURL {
 private final String value;
 private java.net.URI uri;
 public GURL(String value) {
  this.value=value;
  try { uri=new java.net.URI(value); } catch (Exception ignored) {}
 }
 public boolean isValid() { return uri!=null; }
 public String getScheme() { return uri==null||uri.getScheme()==null?"":uri.getScheme(); }
 public String getHost() { return uri==null||uri.getHost()==null?"":uri.getHost(); }
 public String getSpec() { return value; }
}''',
        'org/chromium/chrome/browser/tab/Tab.java': '''package org.chromium.chrome.browser.tab;
import org.chromium.url.GURL;
public class Tab {
 public boolean destroyed, incognito, nativePage, error;
 public String url="https://example.com/path?q=a%26b&next=%23x#part?title=other";
 public String title="Страница & <title> + # %";
 public boolean isDestroyed() { return destroyed; }
 public boolean isIncognito() { return incognito; }
 public boolean isNativePage() { return nativePage; }
 public boolean isShowingErrorPage() { return error; }
 public GURL getUrl() { return new GURL(url); }
 public String getTitle() { return title; }
}''',
    }
    sources['org/chromium/chrome/browser/ntp/ArgonHomeShortcut.java'] = (
        homepage.OVERLAY / homepage.JAVA_SHORTCUT).read_text()
    sources['AddShortcutFlow.java'] = '''
import org.chromium.chrome.browser.tab.Tab;
import org.chromium.chrome.browser.ntp.ArgonHomeShortcut;
public class AddShortcutFlow {
 static class R { static class id { static final int argon_add_to_home_id=1; } }
 static class PageTransition { static final int AUTO_BOOKMARK=2; }
 static class TabLaunchType { static final int FROM_CHROME_UI=3; }
 static class LoadUrlParams {
  final String url;
  LoadUrlParams(String value, int transition) { url=value; assert transition==2; }
 }
 final class Creator {
  void createNewTab(LoadUrlParams params, int launchType, Tab parent) {
   assert launchType==3;
   assert parent==current;
   assert parent.url.equals(originalUrl) : "source tab must remain on its page";
   opened=params.url; count++;
  }
 }
 Tab current=new Tab();
 String originalUrl=current.url, opened;
 int count;
 Tab getActivityTab() { return current; }
 boolean isTabNtp(Tab tab) { return false; }
 Creator getTabCreator(boolean incognito) { assert !incognito; return new Creator(); }
 boolean select(int id) {
ARGON_MENU_ACTION
  return false;
 }
 static void check(boolean value) { if (!value) throw new AssertionError(); }
 static String decode(String value) {
  return java.net.URLDecoder.decode(value, java.nio.charset.StandardCharsets.UTF_8);
 }
 public static void main(String[] args) {
  AddShortcutFlow flow=new AddShortcutFlow();
  check(ArgonHomeShortcut.canAdd(flow.current));
  check(flow.select(1));
  check(flow.count==1 && flow.opened.startsWith("chrome://argon/#add-site?url="));
  String[] fields=flow.opened.substring(flow.opened.indexOf("?url=")+1).split("&");
  check(fields.length==2);
  check(decode(fields[0].substring(4)).equals(flow.current.url));
  check(decode(fields[1].substring(6)).equals(flow.current.title));
  check(!flow.select(99) && flow.count==1);
  for (String url : new String[]{"chrome://settings/", "about:blank", "file:///tmp/a",
    "data:text/plain,test", "javascript:alert(1)", "https:///missing-host", "broken URL",
    "https://example.com/"+"x".repeat(16384)}) {
   flow.current.url=url;
   check(!ArgonHomeShortcut.canAdd(flow.current));
   check(flow.select(1) && flow.count==1);
  }
  flow.current.url=flow.originalUrl;
  flow.current.incognito=true; check(flow.select(1) && flow.count==1); flow.current.incognito=false;
  flow.current.destroyed=true; check(flow.select(1) && flow.count==1); flow.current.destroyed=false;
  flow.current.nativePage=true; check(flow.select(1) && flow.count==1); flow.current.nativePage=false;
  flow.current.error=true; check(flow.select(1) && flow.count==1); flow.current.error=false;
  flow.current.title="a".repeat(511)+"😀";
  check(decode(ArgonHomeShortcut.draftUrl(flow.current).split("&title=")[1]).equals("a".repeat(511)));
  flow.current.url="http://localhost:8080/page"; check(ArgonHomeShortcut.canAdd(flow.current));
  flow.current=null; check(flow.select(1) && flow.count==1);
  System.out.println("PASS: menu draft preserves the source tab and full URL; private/internal/error pages are rejected");
 }
}'''.replace('ARGON_MENU_ACTION', homepage.SHORTCUT_ACTION_AFTER)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, source in sources.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
        compiled = subprocess.run([*(compiler or ['javac']), *(['--release', release] if release else []), '-d', directory,
                                   *map(str, root.rglob('*.java'))], capture_output=True, text=True)
        if compiled.returncode:
            raise ValueError(compiled.stderr)
        result = subprocess.run(['java', '-ea', '-cp', directory, 'AddShortcutFlow'], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(result.stdout + result.stderr)
        return result.stdout


class HomepageAddTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK required')
    def test_menu_only_opens_a_draft_in_a_new_regular_tab(self):
        self.assertIn('PASS: menu draft', run_add_flow())

    def test_both_menu_variants_keep_the_command_next_to_bookmarks(self):
        original = '\n'.join(homepage.SHORTCUT_MENU_ANCHORS)
        patched = homepage.patch_app_menu(original)
        self.assertEqual(patched.count(homepage.SHORTCUT_MENU_ADDITION), 2)
        self.assertEqual(homepage.patch_app_menu(patched), patched)
        for anchor in homepage.SHORTCUT_MENU_ANCHORS:
            self.assertIn(anchor + homepage.SHORTCUT_MENU_ADDITION, patched)
