"""Exercise the production User-Agent default and migration with Android adapters."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM_MAJOR = int(json.loads((ROOT / "build-lock.json").read_text())["chromium_version"].split(".", 1)[0])
MODEL = ROOT / (
    "chromium_overlay/chrome/android/java/src/org/chromium/chrome/browser/base/"
    "ArgonUserAgent.java"
)
ADAPTERS = {
    "android/content/SharedPreferences.java": """
package android.content;
public interface SharedPreferences {
    String getString(String key, String fallback);
    Editor edit();
    interface Editor {
        Editor putString(String key, String value);
        void apply();
    }
}
""",
    "org/chromium/base/ContextUtils.java": """
package org.chromium.base;
import android.content.SharedPreferences;
import java.util.HashMap;
import java.util.Map;
public class ContextUtils {
    private static final Map<String, String> VALUES = new HashMap<>();
    public static SharedPreferences getAppSharedPreferences() {
        return new SharedPreferences() {
            public String getString(String key, String fallback) {
                return VALUES.getOrDefault(key, fallback);
            }
            public Editor edit() {
                return new Editor() {
                    final Map<String, String> pending = new HashMap<>();
                    public Editor putString(String key, String value) {
                        pending.put(key, value);
                        return this;
                    }
                    public void apply() { VALUES.putAll(pending); }
                };
            }
        };
    }
}
""",
    "org/chromium/base/CommandLine.java": """
package org.chromium.base;
public class CommandLine {
    private static final CommandLine INSTANCE = new CommandLine();
    public final java.util.Map<String, String> values = new java.util.HashMap<>();
    public static CommandLine getInstance() { return INSTANCE; }
    public void appendSwitchWithValue(String key, String value) {
        if (!key.equals("argon-user-agent") && !key.equals("argon-user-agent-desktop"))
            throw new AssertionError(key);
        values.put(key, value);
    }
}
""",
    "org/chromium/base/version_info/VersionConstants.java": """
package org.chromium.base.version_info;
public class VersionConstants {
    public static final int PRODUCT_MAJOR_VERSION = %d;
}
""" % CHROMIUM_MAJOR,
    "org/chromium/build/annotations/NullMarked.java": """
package org.chromium.build.annotations;
public @interface NullMarked {}
""",
    "org/chromium/build/annotations/Nullable.java": """
package org.chromium.build.annotations;
import java.lang.annotation.*;
@Target({ElementType.TYPE_USE, ElementType.PARAMETER}) public @interface Nullable {}
""",
}


class UserAgentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required")
    def test_fresh_install_legacy_default_and_saved_choices(self):
        compiler = (["javac"] if shutil.which("javac") else
                    ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = [MODEL, ROOT / "tests/java/ArgonUserAgentTest.java"]
            for path, content in ADAPTERS.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                sources.append(target)
            compiled = subprocess.run(
                [*compiler, "-d", str(root / "classes"), *map(str, sources)],
                capture_output=True, text=True,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run(
                ["java", "-ea", "-cp", str(root / "classes"),
                 "org.chromium.chrome.browser.base.ArgonUserAgentTest"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: mobile Chrome default", result.stdout)
