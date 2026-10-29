import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {initialState, loadStoredState, createDump, parseDump} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

const source = readFileSync(new URL('../../chromium_overlay/chrome/browser/resources/argon_home/theme.js', import.meta.url), 'utf8');

function boot({browser = 'system', systemDark = false, stored = null, native = false} = {}) {
  const root = {dataset: {browserTheme: browser}}, meta = {};
  let mediaListener, nativeListener, subscriptions = 0, requests = 0;
  const media = {matches: systemDark, addEventListener(name, fn) { mediaListener = fn; }};
  const context = {
    document: {documentElement: root, querySelector: () => meta},
    matchMedia: () => media,
    localStorage: {getItem: () => stored, setItem() { throw new Error('bootstrap must not write storage'); }},
    location: {protocol: native ? 'chrome:' : 'https:'},
    chrome: native ? {send() {}} : undefined,
    mockWebUI: {
      addWebUiListener(name, fn) {
        assert.equal(name, 'argon-browser-theme-changed'); subscriptions++; nativeListener = fn;
      },
      async sendWithPromise(name) { assert.equal(name, 'getArgonBrowserTheme'); requests++; return browser; },
    },
  };
  // Node has no chrome:// loader. Substitute only that import; execute the
  // production bootstrap, subscription and callback code unchanged.
  runInNewContext(source.replace("import('chrome://resources/js/cr.js')", 'Promise.resolve(mockWebUI)'), context);
  return {api: context.argonHomeTheme, root, meta,
    system(dark) {media.matches = dark; mediaListener();},
    browser(theme) {browser = theme; nativeListener(theme);},
    counts: () => ({subscriptions, requests})};
}

test('first paint follows browser theme even when Android prefers the opposite', () => {
  for (const browser of ['light', 'dark', 'amoled']) {
    const b = boot({browser, systemDark: browser === 'light'});
    assert.equal(b.root.dataset.theme, browser);
    if (browser === 'amoled') assert.equal(b.meta.content, '#000000');
    b.system(browser !== 'light');
    assert.equal(b.root.dataset.theme, browser);
  }
});

test('manual themes are restored before paint; malformed storage is left intact', () => {
  for (const theme of ['light', 'dark', 'amoled']) {
    const b = boot({browser: 'amoled', stored: JSON.stringify({...initialState(), theme})});
    assert.equal(b.root.dataset.theme, theme);
    b.system(true);
    assert.equal(b.root.dataset.theme, theme);
  }
  assert.equal(boot({browser: 'dark', stored: '{broken'}).root.dataset.theme, 'dark');
});

test('browser events update automatic pages and preserve manual overrides', async () => {
  const b = boot({browser: 'light', native: true});
  await Promise.all([b.api.connect(), b.api.connect()]);
  assert.deepEqual(b.counts(), {subscriptions: 1, requests: 1});
  for (const theme of ['dark', 'amoled', 'light']) {
    b.browser(theme); assert.equal(b.root.dataset.theme, theme);
  }
  b.api.apply('dark'); b.browser('amoled');
  assert.equal(b.root.dataset.theme, 'dark');
  b.api.apply('system');
  assert.equal(b.root.dataset.theme, 'amoled');
  b.browser('invalid'); assert.equal(b.root.dataset.theme, 'amoled');
  await b.api.connect();
  assert.deepEqual(b.counts(), {subscriptions: 1, requests: 2});
});

test('ordinary web previews follow system color scheme without the native bridge', async () => {
  const b = boot();
  b.system(true); assert.equal(b.root.dataset.theme, 'dark');
  b.system(false); assert.equal(b.root.dataset.theme, 'light');
  await b.api.connect(); assert.deepEqual(b.counts(), {subscriptions: 0, requests: 0});
});

test('old profiles and new AMOLED dumps retain all homepage data', () => {
  for (const theme of ['system', 'light', 'dark', 'amoled']) {
    const state = {...initialState(), theme,
      shortcuts: [{id: 'personal', name: 'Личный сайт', url: 'https://example.org', iconMode: 'favicon'}]};
    assert.deepEqual(loadStoredState(state).state, state);
    assert.deepEqual(parseDump(JSON.stringify(createDump(state))).state, state);
  }
});
