import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('homepage', ROOT / 'scripts/apply_homepage.py')
homepage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(homepage)
FIXTURES = ROOT / 'tests/fixtures/homepage'


def input_tree(root):
    values = {
        homepage.NATIVE_FACTORY: (FIXTURES / 'native_page_factory_method.java.in').read_text(),
        homepage.NATIVE_HANDLER: (FIXTURES / 'new_tab_page_url_handler.cc.in').read_text(),
        homepage.URL_RESOLVER_FACTORY: (FIXTURES / 'UrlConstantResolverFactory.java.in').read_text(),
        homepage.TABBED_ACTIVITY: (FIXTURES / 'startup_draw_block.java.in').read_text(),
        homepage.CONFIGS: '#include "build/build_config.h"\nvoid RegisterChromeWebUIConfigs() {\n#if BUILDFLAG(IS_ANDROID)\n  map.AddWebUIConfig(std::make_unique<OtherUIConfig>());\n#endif\n}\n',
        homepage.GN: 'source_set("configs") {\n  if (is_android) {\n    deps += [\n      "//chrome/browser/ui/android/lens",\n    ]\n  }\n}\n',
        homepage.TOOLBAR_MANAGER: (FIXTURES / 'toolbar_constructor_init.java.in').read_text()
            + '\n' + homepage.TOOLBAR_BEFORE + '\n' + homepage.TOOLBAR_NAV_START
            + '\n' + homepage.TOOLBAR_NAV_FINISH + '\n    /**\n     * @return The {@link OmniboxStub}.\n     */\n'
            + '    public @Nullable OmniboxStub getOmniboxStub() {\n    }\n',
        homepage.TOOLBAR_CONTAINER: (FIXTURES / 'toolbar_capture.java.in').read_text(),
        homepage.TOOLBAR_PHONE: homepage.PHONE_CAPTURE_FIELD_BEFORE + '\n'
            + (FIXTURES / 'toolbar_phone_capture.java.in').read_text(),
        homepage.JAVA_CATALOG: '  "java/src/org/chromium/chrome/browser/ntp/NewTabPage.java",\n',
        homepage.JNI_CATALOG: '  generate_jni("chrome_jni_headers") {\n    sources = [\n    ]\n  }\n',
    }
    for path, text in values.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def snapshot(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


class HomepageTests(unittest.TestCase):
    def test_patch_is_idempotent_and_bundles_every_frontend_file(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            homepage.apply(src)
            before = snapshot(src)
            homepage.apply(src)
            self.assertEqual(before, snapshot(src))
            generated = (src / homepage.UI / 'argon_home_resources.inc').read_text()
            for filename in ('index.html', 'app.css', 'app.js', 'state.mjs', 'icons.mjs', 'i18n.js', 'theme.js', 'bookmarks.mjs', 'browser-search.mjs'):
                self.assertIn((homepage.OVERLAY / homepage.RESOURCES / filename).read_text(), generated)
            self.assertTrue((src / homepage.UI / 'BUILD.gn').is_file())
            self.assertEqual((src / homepage.JAVA_BRIDGE).read_text(),
                             (homepage.OVERLAY / homepage.JAVA_BRIDGE).read_text())

    def test_changed_toolbar_or_source_catalog_rejects_the_whole_patch(self):
        for path in (homepage.TOOLBAR_MANAGER, homepage.JAVA_CATALOG, homepage.JNI_CATALOG):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                src = Path(directory)
                input_tree(src)
                (src / path).write_text('changed upstream source\n')
                before = snapshot(src)
                with self.assertRaises(ValueError):
                    homepage.apply(src)
                self.assertEqual(before, snapshot(src))

    def test_unknown_upstream_method_changes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            path = src / homepage.NATIVE_HANDLER
            path.write_text(path.read_text().replace('  return false;', '  return true;', 1))
            before = snapshot(src)
            with self.assertRaisesRegex(ValueError, 'Chromium method changed'):
                homepage.apply(src)
            self.assertEqual(before, snapshot(src))

    def test_changed_toolbar_constructor_rejects_the_whole_patch(self):
        for original, changed in (
            ('mActivityTabProvider = tabProvider;', 'mActivityTabProvider = anotherProvider;'),
            ('ntpDelegate = createNewTabPageDelegate();', 'ntpDelegate = anotherDelegate();'),
        ):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                src = Path(directory)
                input_tree(src)
                path = src / homepage.TOOLBAR_MANAGER
                path.write_text(path.read_text().replace(original, changed, 1))
                before = snapshot(src)
                with self.assertRaises(ValueError):
                    homepage.apply(src)
                self.assertEqual(before, snapshot(src))

    def test_changed_capture_gate_rejects_the_whole_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            path = src / homepage.TOOLBAR_CONTAINER
            path.write_text(path.read_text().replace(
                'if (!mNeedCaptureAfterPageLoad', 'if (mNeedCaptureAfterPageLoad', 1))
            before = snapshot(src)
            with self.assertRaisesRegex(ValueError, 'Chromium method changed'):
                homepage.apply(src)
            self.assertEqual(before, snapshot(src))

    def test_changed_phone_capture_rejects_the_whole_patch(self):
        for before, after in (
            ('VisualState.NORMAL);', 'VisualState.INCOGNITO);'),
            ('if (isLayoutRequested()', 'if (!isLayoutRequested()'),
            ('assert mTextureCaptureMode != textureMode;', 'assert true;'),
            ('mUrlExpansionFraction > 0;', 'mUrlExpansionFraction >= 0;'),
        ):
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                src = Path(directory)
                input_tree(src)
                path = src / homepage.TOOLBAR_PHONE
                path.write_text(path.read_text().replace(before, after, 1))
                original = snapshot(src)
                with self.assertRaises(ValueError):
                    homepage.apply(src)
                self.assertEqual(original, snapshot(src))

    def test_unknown_resolver_change_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            path = src / homepage.URL_RESOLVER_FACTORY
            path.write_text(path.read_text().replace('isNtpOverrideEnabled() ||', 'true ||'))
            before = snapshot(src)
            with self.assertRaisesRegex(ValueError, 'Chromium method changed'):
                homepage.apply(src)
            self.assertEqual(before, snapshot(src))

    def test_unknown_startup_draw_change_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory)
            input_tree(src)
            path = src / homepage.TABBED_ACTIVITY
            path.write_text(path.read_text().replace('!isTabWebUiNtp', 'isTabWebUiNtp', 1))
            before = snapshot(src)
            with self.assertRaisesRegex(ValueError, 'Chromium method changed'):
                homepage.apply(src)
            self.assertEqual(before, snapshot(src))

    def test_page_uses_local_modules_and_the_webui_html_policy(self):
        assets = homepage.OVERLAY / homepage.RESOURCES
        html = (assets / 'index.html').read_text()
        self.assertIn('type="module" src="app.js"', html)
        self.assertIn('rel="stylesheet" href="app.css"', html)
        self.assertNotIn('<style>', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('Ваше место в интернете', html)
        app = (assets / 'app.js').read_text()
        self.assertEqual(app.count('.innerHTML='), 1)
        self.assertIn("createPolicy('argon-home'", app)
        self.assertIn('htmlPolicy.createHTML(markup)', app)
        controller = (homepage.OVERLAY / homepage.UI / 'argon_home_ui.cc').read_text()
        self.assertNotIn('DisableTrustedTypesCSP', controller)
        self.assertIn("connect-src 'none';", controller)
        self.assertIn('SetRequestFilter(', controller)
        self.assertIn('AddMessageHandler(CreateArgonThemeHandler())', controller)
        self.assertLess(html.index('src="theme.js"'), html.index('href="app.css"'))
        self.assertIn('std::make_unique<SanitizedImageSource>(profile)', controller)
        self.assertIn("img-src 'self' data: chrome://image;", controller)
        self.assertIn('//chrome/browser/ui/webui/sanitized_image:sanitized_image_source',
                      (homepage.OVERLAY / homepage.UI / 'BUILD.gn').read_text())

    def test_module_imports_use_webui_javascript_mime_paths(self):
        # The pinned WebUIDataSourceImpl serves .mjs as text/html, so browser
        # tests must not hide unsupported import URLs behind a permissive server.
        assets = homepage.OVERLAY / homepage.RESOURCES
        controller = (homepage.OVERLAY / homepage.UI / 'argon_home_ui.cc').read_text()
        for filename in ('app.js', 'state.mjs', 'icons.mjs', 'i18n.js', 'bookmarks.mjs', 'browser-search.mjs'):
            imports = re.findall(r"from ['\"](\./[^'\"]+)['\"]", (assets / filename).read_text())
            for path in imports:
                self.assertTrue(path.endswith('.js'), (filename, path))
                self.assertIn('path == "' + path.removeprefix('./') + '"', controller)

    def test_build_copies_the_patcher_before_applying_it(self):
        docker = (ROOT / 'docker/chromium/Dockerfile').read_text()
        self.assertLess(docker.index('scripts/apply_homepage.py'), docker.index('RUN bash scripts/prepare_chromium.sh patch'))
        self.assertIn('scripts/apply_homepage.py" --chromium-src "$PWD"', (ROOT / 'patch.sh').read_text())

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler is required')
    def test_real_cpp_handler_routes_both_schemes_and_preserves_incognito_and_extensions(self):
        original = (FIXTURES / 'new_tab_page_url_handler.cc.in').read_text()
        method = homepage.method_source(homepage.patch_handler(original), homepage.HANDLER_SIGNATURE)
        harness = (FIXTURES / 'route_harness.cc.in').read_text().replace('ARGON_HANDLER_BODY', method)
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'flow.cc'
            binary = Path(directory) / 'flow'
            src.write_text(harness)
            subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror', str(src), '-o', str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK is required')
    def test_real_java_factory_uses_renderer_only_for_regular_ntp(self):
        original = (FIXTURES / 'native_page_factory_method.java.in').read_text()
        method = homepage.method_source(homepage.patch_factory(original), homepage.FACTORY_SIGNATURE)
        harness = (FIXTURES / 'NativeRoutingFlow.java.in').read_text().replace('ARGON_FACTORY_METHOD', method)
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'NativeRoutingFlow.java'
            src.write_text(harness)
            subprocess.run(['javac', '--release', '17', str(src)], check=True, capture_output=True, text=True)
            subprocess.run(['java', '-cp', directory, 'NativeRoutingFlow'], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK is required')
    def test_restored_webui_ntp_unblocks_first_draw_without_another_content_change(self):
        original = (FIXTURES / 'startup_draw_block.java.in').read_text()
        factory = homepage.method_source(homepage.patch_factory(
            (FIXTURES / 'native_page_factory_method.java.in').read_text()), homepage.FACTORY_SIGNATURE)
        template = (FIXTURES / 'StartupDrawFlow.java.in').read_text()
        for patched in (False, True):
            with self.subTest(patched=patched), tempfile.TemporaryDirectory() as directory:
                text = homepage.patch_startup_draw(original) if patched else original
                block = homepage.method_source(text, homepage.STARTUP_SIGNATURE)
                harness = template.replace('ARGON_STARTUP_BLOCK', block)
                harness = harness.replace('ARGON_FACTORY_METHOD', factory)
                src = Path(directory) / 'StartupDrawFlow.java'
                src.write_text(harness)
                subprocess.run(['javac', '--release', '17', str(src)], check=True,
                               capture_output=True, text=True)
                result = subprocess.run(['java', '-cp', directory, 'StartupDrawFlow'],
                                        capture_output=True, text=True)
                if patched:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('restored regular NTP still blocks first draw', result.stderr)

    @unittest.skipUnless(shutil.which('javac') and shutil.which('java'), 'JDK is required')
    def test_real_resolver_sends_new_tabs_to_chrome_rewriter_even_without_native_override_flag(self):
        def nested_class(text):
            text = re.sub(r'^(package|import) .*\n', '', text, flags=re.MULTILINE)
            text = re.sub(r'@(NullMarked|Nullable|VisibleForTesting)\b', '', text)
            return text.replace('public class UrlConstantResolver', 'static class UrlConstantResolver')

        resolver = (FIXTURES / 'UrlConstantResolver.java.in').read_text()
        factory = homepage.patch_resolver_factory((FIXTURES / 'UrlConstantResolverFactory.java.in').read_text())
        harness = (FIXTURES / 'UrlResolverFlow.java.in').read_text()
        harness = harness.replace('ARGON_RESOLVER_CLASS', nested_class(resolver))
        harness = harness.replace('ARGON_RESOLVER_FACTORY_CLASS', nested_class(factory))
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / 'UrlResolverFlow.java'
            src.write_text(harness)
            subprocess.run(['javac', '--release', '17', str(src)], check=True, capture_output=True, text=True)
            subprocess.run(['java', '-cp', directory, 'UrlResolverFlow'], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
