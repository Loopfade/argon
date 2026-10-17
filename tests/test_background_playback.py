"""Run production timer logic and check integration/locale resource integrity without an APK."""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "chromium_overlay/titanium/chromium_src/chrome/android/java"
POLICY = OVERLAY / "src/org/chromium/chrome/browser/media/ArgonBackgroundPlaybackPolicy.java"
spec = importlib.util.spec_from_file_location("background_patch", ROOT / "scripts/apply_background_playback.py")
background = importlib.util.module_from_spec(spec)
spec.loader.exec_module(background)


class BackgroundPlaybackTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("java"), "JDK required for Java policy test")
    def test_native_budgets_and_rule_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            compiler = ["javac"] if shutil.which("javac") else ["java", "-m", "jdk.compiler/com.sun.tools.javac.Main"]
            subprocess.run([*compiler, "-d", directory, str(POLICY),
                            str(ROOT / "tests/java/ArgonBackgroundPlaybackPolicyTest.java")],
                           check=True, capture_output=True, text=True)
            result = subprocess.run(["java", "-cp", directory,
                                    "org.chromium.chrome.browser.media.ArgonBackgroundPlaybackPolicyTest"],
                                   check=True, capture_output=True, text=True)
            self.assertIn("PASS: production playback budgets", result.stdout)

    def test_two_interface_languages_have_identical_keys_and_placeholders(self):
        catalogs = []
        for folder in ("values", "values-ru"):
            catalogs.append({node.attrib["name"]: node for node in ET.parse(
                OVERLAY / "res" / folder / "argon_background_playback_strings.xml").getroot()})
        self.assertEqual(catalogs[0].keys(), catalogs[1].keys())
        for name, english in catalogs[0].items():
            russian = catalogs[1][name]
            if english.tag == "plurals":
                for item in russian:
                    self.assertEqual(re.findall(r"%[\d$]*[ds]", item.text or ""), ["%d"], name)
            else:
                self.assertEqual(re.findall(r"%[\d$]*[ds]", english.text or ""),
                                 re.findall(r"%[\d$]*[ds]", russian.text or ""), name)

    def test_patch_anchor_changes_fail_instead_of_silently_breaking_the_build(self):
        for transform in (background.patch_activity, background.patch_menu,
                          background.patch_locales, background.patch_java_sources,
                          background.patch_resources):
            with self.subTest(transform=transform.__name__):
                with self.assertRaises(ValueError):
                    transform("unexpected upstream source")

    @unittest.skipUnless(shutil.which("node"), "Node required for visibility compatibility test")
    def test_visibility_compatibility_is_reversible_and_idempotent(self):
        script = (OVERLAY / "res/raw/argon_background_playback.js").read_text()
        harness = r'''
const vm = require('node:vm');
const listeners = [];
class Document {
  get hidden() { return true; }
  get visibilityState() { return 'hidden'; }
  addEventListener(name, callback) { listeners.push(callback); }
}
const sandbox = {document: new Document(), window: {addEventListener(name, cb) { listeners.push(cb); }}};
vm.createContext(sandbox);
function check(value) { if (!value) throw Error('Visibility shim regression'); }
'''
        with tempfile.TemporaryDirectory() as directory:
            test = Path(directory) / "test.cjs"
            import json
            harness += "\nvm.runInContext(" + json.dumps(script.replace("ARGON_OVERRIDE_ACTIVE", "true")) + ", sandbox);\n"
            harness += "check(sandbox.document.hidden === false && sandbox.document.visibilityState === 'visible');\n"
            harness += "let stopped = 0; listeners[0]({stopImmediatePropagation() { stopped++; }}); check(stopped === 1);\n"
            harness += "vm.runInContext(" + json.dumps(script.replace("ARGON_OVERRIDE_ACTIVE", "false")) + ", sandbox);\n"
            harness += "check(sandbox.document.hidden === true && sandbox.document.visibilityState === 'hidden');\n"
            harness += "listeners[0]({stopImmediatePropagation() { stopped++; }}); check(stopped === 1 && listeners.length === 2);\n"
            test.write_text(harness)
            subprocess.run(["node", str(test)], check=True, capture_output=True, text=True)
