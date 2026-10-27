#!/usr/bin/env python3
"""Bundle the Argon homepage and route regular Android NTPs to chrome://argon/.

This runs after the pinned Vanadium patches. Changed upstream methods must be
reviewed before this patch will apply; every input is checked before writing.
"""
import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'chromium_overlay'
UI = Path('chrome/browser/ui/webui/argon_home')
RESOURCES = Path('chrome/browser/resources/argon_home')
CONFIGS = Path('chrome/browser/ui/webui/chrome_web_ui_configs.cc')
GN = Path('chrome/browser/ui/webui/BUILD.gn')
NATIVE_FACTORY = Path('chrome/android/java/src/org/chromium/chrome/browser/native_page/NativePageFactory.java')
NATIVE_HANDLER = Path('chrome/browser/android/ntp/new_tab_page_url_handler.cc')
URL_RESOLVER_FACTORY = Path('chrome/browser/url_constants/android/java/src/org/chromium/chrome/browser/url_constants/UrlConstantResolverFactory.java')
INPUT_PATHS = (CONFIGS, GN, NATIVE_FACTORY, NATIVE_HANDLER, URL_RESOLVER_FACTORY)

FACTORY_SIGNATURE = '    private static boolean isChromePageUrlOverridden(GURL url, boolean isIncognito) {'
HANDLER_SIGNATURE = 'bool HandleAndroidNativePageURL(GURL* url,'
RESOLVER_SIGNATURE = '    private static UrlConstantResolver buildOriginalResolver() {'
FACTORY_DIGEST = '905c86c4d9066d43abfa512b02ae1649961991706ec493fe026a16f386b7d315'
HANDLER_DIGEST = '6420fc2dfc3a1bea11edf3ea5c2fa928f93d79b12f00b16f83d31b040b0a6c7d'
RESOLVER_DIGEST = 'c8676f48d70c326f26086693906f9fd97473ced55a21ff3c80053613f06748d3'

RESOLVER_BEFORE = '''        UrlConstantResolver resolver = new UrlConstantResolver();
        resolver.registerOverride(
                getOriginalNativeNtpUrl(),
                () -> {
                    // Returns 'chrome://newtab' even when WebUI NTP is enabled in order to respect
                    // extensions/NTP location overrides and delegate to C++ URL rewrite handler.
                    if (isNtpOverrideEnabled() || isWebUiNtpOverrideEnabled()) {
                        return getOriginalNtpUrl();
                    } else {
                        return null;
                    }
                });'''
RESOLVER_AFTER = '''        // ARGON: regular new tabs must enter the chrome:// URL rewrite chain.
        // A native-page factory veto alone leaves callers on chrome-native://,
        // whose empty renderer document cannot display the bundled WebUI.
        // Keep policy and extension overrides in C++, ahead of our fallback.
        // This must also work when ChromeNativeUrlOverriding is disabled.
        UrlConstantResolver resolver = new UrlConstantResolver() {
            @Override
            public String getNtpUrl() {
                return getOriginalNtpUrl();
            }
        };'''

FACTORY_BEFORE = FACTORY_SIGNATURE + '\n'
FACTORY_AFTER = FACTORY_BEFORE + '''        // ARGON: render the bundled homepage for regular NTPs in either scheme.
        // Incognito and overrides of bookmarks/history retain their native flow.
        if (!isIncognito
                && UrlConstants.NTP_HOST.equals(url.getHost())
                && (UrlConstants.CHROME_SCHEME.equals(url.getScheme())
                        || "chrome-native".equals(url.getScheme()))) {
            return true;
        }

'''
HANDLER_BEFORE = '''  if (url->SchemeIs(content::kChromeUIScheme)) {'''
HANDLER_AFTER = '''  // ARGON: the native-page factory lets regular NTPs reach this renderer route.
  // Extension URL overrides above keep priority; external/custom homepages do
  // not match either internal scheme. Incognito retains Chromium's NTP.
  if (!Profile::FromBrowserContext(browser_context)->IsOffTheRecord() &&
      (url->SchemeIs(content::kChromeUIScheme) ||
       url->SchemeIs(chrome::kChromeNativeScheme)) &&
      url->GetHost() == chrome::kChromeUINewTabHost) {
    *url = GURL("chrome://argon/");
    return true;
  }

''' + HANDLER_BEFORE


def replace_once(text, before, after):
    if text.count(after) == 1:
        if before in text.replace(after, '', 1):
            raise ValueError('Ambiguous already-applied Argon homepage patch')
        return text
    if text.count(after) or text.count(before) != 1:
        raise ValueError('Argon homepage patch anchor changed; review Chromium update')
    return text.replace(before, after, 1)


def method_source(text, signature):
    if text.count(signature) != 1:
        raise ValueError(f'Expected one Chromium method: {signature.strip()}')
    start = text.index(signature)
    end = text.index('{', start) + 1
    depth = 1
    while depth and end < len(text):
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    if depth:
        raise ValueError('Incomplete Chromium method in homepage patch')
    return text[start:end]


def reviewed_method(text, signature, digest, before, after):
    method = method_source(text, signature)
    if method.count(after) == 1:
        method = method.replace(after, before, 1)
    if hashlib.sha256(method.encode()).hexdigest() != digest:
        raise ValueError(f'Chromium method changed; review homepage patch: {signature.strip()}')
    return replace_once(text, before, after)


def patch_factory(text):
    text = reviewed_method(text, FACTORY_SIGNATURE, FACTORY_DIGEST, FACTORY_BEFORE, FACTORY_AFTER)
    return replace_once(text,
        '     * <p>chrome-native:// scheme pages are not affected by this.',
        '     * <p>Argon also renders regular chrome-native://newtab pages in WebUI.')


def patch_handler(text):
    return reviewed_method(text, HANDLER_SIGNATURE, HANDLER_DIGEST, HANDLER_BEFORE, HANDLER_AFTER)


def patch_resolver_factory(text):
    return reviewed_method(text, RESOLVER_SIGNATURE, RESOLVER_DIGEST,
                           RESOLVER_BEFORE, RESOLVER_AFTER)


def patch_configs(text):
    text = replace_once(text,
        '#include "build/build_config.h"',
        '#include "build/build_config.h"\n\n'
        '#if BUILDFLAG(IS_ANDROID)\n'
        '#include "chrome/browser/ui/webui/argon_home/argon_home_ui.h"\n'
        '#endif')
    return replace_once(text,
        '#if BUILDFLAG(IS_ANDROID)\n  map.AddWebUIConfig(',
        '#if BUILDFLAG(IS_ANDROID)\n'
        '  map.AddWebUIConfig(std::make_unique<ArgonHomeUIConfig>());\n'
        '  map.AddWebUIConfig(')


def patch_gn(text):
    return replace_once(text,
        '      "//chrome/browser/ui/android/lens",',
        '      "//chrome/browser/ui/android/lens",\n'
        '      "//chrome/browser/ui/webui/argon_home",')


def resource_header(resources):
    entries = [('index.html', 'kArgonHomeHtml', 'ARGON_HTML'),
               ('app.css', 'kArgonHomeCss', 'ARGON_CSS'),
               ('app.js', 'kArgonHomeJs', 'ARGON_JS'),
               ('state.mjs', 'kArgonHomeState', 'ARGON_STATE'),
               ('icons.mjs', 'kArgonHomeIcons', 'ARGON_ICONS')]
    result = ['// Generated by scripts/apply_homepage.py; do not edit.\n']
    for filename, name, delimiter in entries:
        value = (resources / filename).read_text()
        if '\x00' in value or f'){delimiter}"' in value:
            raise ValueError(f'Invalid bundled homepage resource: {filename}')
        result.append(f'constexpr char {name}[] = R"{delimiter}({value}){delimiter}";\n')
    return '\n'.join(result)


def apply(src):
    transforms = [(CONFIGS, patch_configs), (GN, patch_gn),
                  (NATIVE_FACTORY, patch_factory), (NATIVE_HANDLER, patch_handler),
                  (URL_RESOLVER_FACTORY, patch_resolver_factory)]
    writes = {src / path: transform((src / path).read_text()) for path, transform in transforms}
    for source in (OVERLAY / UI).iterdir():
        if source.is_file():
            target = src / UI / source.name
            value = source.read_text()
            if target.exists() and target.read_text() != value:
                raise ValueError(f'Refusing to overwrite an unexpected homepage source: {target}')
            writes[target] = value
    writes[src / UI / 'argon_home_resources.inc'] = resource_header(OVERLAY / RESOURCES)
    for target, value in writes.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text() != value:
            target.write_text(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chromium-src', type=Path, required=True)
    args = parser.parse_args()
    apply(args.chromium_src.resolve())


if __name__ == '__main__':
    main()
