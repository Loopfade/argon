"""Run the production startup model, including persistence across fresh JVMs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'chromium_overlay/chrome/android/java/src/org/chromium/chrome/browser/base/ArgonUserAgent.java'
ADAPTERS = {
    'android/content/SharedPreferences.java': '''package android.content;
public interface SharedPreferences {
    String getString(String key,String fallback); Editor edit();
    interface Editor { Editor putString(String key,String value); void apply(); boolean commit(); }
}''',
    'org/chromium/base/ContextUtils.java': '''package org.chromium.base;
import android.content.SharedPreferences;
import java.util.Properties;
import java.nio.file.*;
public class ContextUtils {
    public static boolean failCommit;
    private static final Properties VALUES=new Properties();
    private static final String FILE=System.getProperty("preferences.file");
    static {
        if(FILE!=null && Files.exists(Path.of(FILE))) {
            try(var input=Files.newInputStream(Path.of(FILE))) { VALUES.load(input); }
            catch(Exception e) { throw new RuntimeException(e); }
        }
    }
    public static SharedPreferences getAppSharedPreferences() {
        return new SharedPreferences() {
            public String getString(String key,String fallback) { return VALUES.getProperty(key,fallback); }
            public Editor edit() {
                return new Editor() {
                    final Properties pending=new Properties();
                    public Editor putString(String key,String value) { pending.setProperty(key,value); return this; }
                    public void apply() { VALUES.putAll(pending); }
                    public boolean commit() {
                        apply();
                        if(failCommit) return false;
                        if(FILE!=null) {
                            try(var output=Files.newOutputStream(Path.of(FILE))) { VALUES.store(output,""); }
                            catch(Exception e) { return false; }
                        }
                        return true;
                    }
                };
            }
        };
    }
}''',
    'org/chromium/base/StrictModeContext.java': '''package org.chromium.base;
public class StrictModeContext implements AutoCloseable {
    public static StrictModeContext allowDiskWrites() { return new StrictModeContext(); }
    public void close() {}
}''',
    'org/chromium/base/CommandLine.java': '''package org.chromium.base;
public class CommandLine {
    private static final CommandLine INSTANCE=new CommandLine();
    public final java.util.Map<String,String> values=new java.util.HashMap<>();
    public static CommandLine getInstance() { return INSTANCE; }
    public void appendSwitchWithValue(String key,String value) { values.put(key,value); }
    public void appendSwitch(String key) { values.put(key,""); }
}''',
    'org/chromium/build/annotations/NullMarked.java': '''package org.chromium.build.annotations;
public @interface NullMarked {}''',
    'org/chromium/chrome/browser/base/UserAgentStartup.java': '''package org.chromium.chrome.browser.base;
public class UserAgentStartup { public static void initialize() { ArgonUserAgent.applySavedOverride(); } }''',
}


def compile_java(root, sources, adapters):
    sources=list(sources)
    for path, text in adapters.items():
        target=root/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(text)
        sources.append(target)
    compiler=['javac'] if shutil.which('javac') else ['java','-m','jdk.compiler/com.sun.tools.javac.Main']
    result=subprocess.run([*compiler,'-d',str(root/'classes'),*map(str,sources)],text=True,capture_output=True)
    if result.returncode:
        raise ValueError(result.stdout+result.stderr)


def run_java(root, main, *args, properties=()):
    result=subprocess.run(['java','-ea',*properties,'-cp',str(root/'classes'),main,*args],text=True,capture_output=True)
    if result.returncode:
        raise ValueError(result.stdout+result.stderr)
    return result.stdout


class UserAgentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('java'),'JDK required')
    def test_startup_migration_pending_and_process_restart(self):
        with tempfile.TemporaryDirectory(prefix='argon-ua-') as directory:
            root=Path(directory)
            compile_java(root,[MODEL,ROOT/'tests/java/ArgonUserAgentTest.java'],ADAPTERS)
            main='org.chromium.chrome.browser.base.ArgonUserAgentTest'
            cases={'':'DEFAULT','CHROME_MOBILE':'DEFAULT','CHROME_DESKTOP':'DEFAULT',
                   'DEFAULT':'DEFAULT','CHROMIUM':'DEFAULT','unknown':'DEFAULT',
                   'FIREFOX_MOBILE':'FIREFOX','FIREFOX_DESKTOP':'FIREFOX','FIREFOX':'FIREFOX',
                   'YANDEX_MOBILE':'YANDEX','YANDEX_DESKTOP':'YANDEX','YANDEX':'YANDEX',
                   'CUSTOM':'CUSTOM'}
            for saved,expected in cases.items():
                with self.subTest(saved=saved):
                    self.assertIn('PASS:',run_java(root,main,'exercise',saved,expected))
            properties=[f'-Dpreferences.file={root / "settings.properties"}']
            self.assertIn('PASS:',run_java(root,main,'save',properties=properties))
            self.assertIn('PASS:',run_java(root,main,'restored',properties=properties))
