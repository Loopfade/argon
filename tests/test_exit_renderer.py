"""Compile the production renderer gate and reject inline virtual definitions."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_exit import exit_patch

CONTENT = Path('content/public/browser/content_browser_client')
CHROME = Path('chrome/browser/chrome_content_browser_client')
METHOD = 'IsRendererProcessLaunchAllowed'


def definition(text, owner):
    matches = re.findall(r'bool ' + owner + '::' + METHOD + r'\(\)\s*\{[^{}]*\}', text)
    if len(matches) != 1:
        raise ValueError(f'Expected one out-of-line {owner} renderer gate')
    return matches[0]


def verify_renderer_gate(src):
    content_h, content_cc = [(src / CONTENT.with_suffix(suffix)).read_text()
                             for suffix in ('.h', '.cc')]
    chrome_h, chrome_cc = [(src / CHROME.with_suffix(suffix)).read_text()
                           for suffix in ('.h', '.cc')]
    base_decl = f'virtual bool {METHOD}();'
    chrome_decl = f'bool {METHOD}() override;'
    for text, declaration in ((content_h, base_decl), (chrome_h, chrome_decl)):
        # Chromium's clang plugin rejects even a trivial non-empty virtual body
        # in the class declaration. Both the base and override must stay out of line.
        if text.count(METHOD) != 1 or declaration not in text:
            raise ValueError('Renderer gate must have a declaration-only header')
    chrome_guard = '#if BUILDFLAG(IS_ANDROID)\n  ' + chrome_decl + '\n#endif'
    if chrome_guard not in chrome_h:
        raise ValueError('Chrome renderer gate must be Android-only')
    if 'chrome/browser/android/argon_exit.h' in chrome_h:
        raise ValueError('Keep the Android exit implementation out of the public header')
    base_body = definition(content_cc, 'ContentBrowserClient')
    chrome_body = definition(chrome_cc, 'ChromeContentBrowserClient')
    chrome_body = '#if BUILDFLAG(IS_ANDROID)\n' + chrome_body + '\n#endif'
    if chrome_body not in chrome_cc:
        raise ValueError('Chrome renderer gate definition must be Android-only')

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'gate.h').write_text('''#pragma once
#define BUILDFLAG(flag) (flag)
namespace content {
class ContentBrowserClient {
 public:
  virtual ~ContentBrowserClient() = default;
  ''' + base_decl + '''
};
}
class ChromeContentBrowserClient : public content::ContentBrowserClient {
 public:
''' + chrome_guard + '''
};
namespace argon { bool IsExitInProgress(); }
''')
        (root / 'gate.cc').write_text('#include "gate.h"\nnamespace content {\n'
                                     + base_body + '\n}\n' + chrome_body + '\n')
        (root / 'main.cc').write_text('''#include "gate.h"
#include <cassert>
bool exiting = false;
namespace argon { bool IsExitInProgress() { return exiting; } }
int main() {
  content::ContentBrowserClient other_embedder;
  ChromeContentBrowserClient chrome;
  content::ContentBrowserClient* client = &chrome;
  assert(other_embedder.IsRendererProcessLaunchAllowed());
  assert(client->IsRendererProcessLaunchAllowed());
  exiting = true;
  assert(other_embedder.IsRendererProcessLaunchAllowed());
  assert(client->IsRendererProcessLaunchAllowed() == !BUILDFLAG(IS_ANDROID));
  exiting = false;
  assert(client->IsRendererProcessLaunchAllowed());
}
''')
        for android in (0, 1):
            binary = root / f'gate-{android}'
            subprocess.run(['g++', '-std=c++20', '-Wall', '-Wextra', '-Werror',
                            f'-DIS_ANDROID={android}', str(root / 'gate.cc'),
                            str(root / 'main.cc'), '-o', str(binary)],
                           check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    return 'PASS: out-of-line renderer gate, Android shutdown and other embedders'


class ExitRendererTests(unittest.TestCase):
    def source_tree(self, root):
        sources = {
            CONTENT.with_suffix('.h'): '  virtual ~ContentBrowserClient() = default;',
            CONTENT.with_suffix('.cc'): 'namespace content {\n}\n',
            CHROME.with_suffix('.h'): '  ~ChromeContentBrowserClient() override;',
            CHROME.with_suffix('.cc'): '#include "build/build_config.h"\n'
                'ChromeContentBrowserClient::ChromeContentBrowserClient() {}\n',
        }
        for path, value in sources.items():
            transform = exit_patch.TRANSFORMS[path]
            value = transform(value)
            self.assertEqual(transform(value), value)
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)

    @unittest.skipUnless(shutil.which('g++'), 'C++ compiler required')
    def test_out_of_line_definitions_link_and_preserve_renderer_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_tree(root)
            self.assertIn('PASS:', verify_renderer_gate(root))

    def test_inline_virtual_body_is_rejected_before_compilation(self):
        for path, declaration, inline in (
            (CONTENT.with_suffix('.h'), f'virtual bool {METHOD}();',
             f'virtual bool {METHOD}() {{ return true; }}'),
            (CHROME.with_suffix('.h'), f'bool {METHOD}() override;',
             f'bool {METHOD}() override {{ return !argon::IsExitInProgress(); }}'),
        ):
            with self.subTest(header=path), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.source_tree(root)
                header = root / path
                header.write_text(header.read_text().replace(declaration, inline))
                with self.assertRaisesRegex(ValueError, 'declaration-only header'):
                    verify_renderer_gate(root)
