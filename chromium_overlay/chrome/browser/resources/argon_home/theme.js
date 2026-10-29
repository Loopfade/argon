// Run before the stylesheet so restored preferences and browser AMOLED apply to the first paint.
(() => {
  'use strict';
  const modes = new Set(['light', 'dark', 'amoled']);
  const root = document.documentElement;
  const media = matchMedia('(prefers-color-scheme: dark)');
  let browserTheme = root.dataset.browserTheme;
  let choice = 'system';
  let listenerInstalled = false;
  let connecting = null;

  function resolve(preference, browser, systemDark) {
    if (modes.has(preference)) return preference;
    return modes.has(browser) ? browser : systemDark ? 'dark' : 'light';
  }

  function apply(preference = choice) {
    choice = modes.has(preference) ? preference : 'system';
    const theme = resolve(choice, browserTheme, media.matches);
    root.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = {light:'#f7f7f4', dark:'#141715', amoled:'#000000'}[theme];
    return theme;
  }

  function updateBrowser(theme) {
    if (!modes.has(theme)) return;
    browserTheme = theme;
    root.dataset.browserTheme = theme;
    apply();
  }

  async function connect() {
    if (location.protocol !== 'chrome:' || typeof globalThis.chrome?.send !== 'function') return;
    if (connecting) return connecting;
    connecting = (async () => {
      const {addWebUiListener, sendWithPromise} = await import('chrome://resources/js/cr.js');
      if (!listenerInstalled) {
        addWebUiListener('argon-browser-theme-changed', updateBrowser);
        listenerInstalled = true;
      }
      updateBrowser(await sendWithPromise('getArgonBrowserTheme'));
    })();
    try { await connecting; }
    finally { connecting = null; }
  }

  try {
    const stored = JSON.parse(localStorage.getItem('argon-start-v1'));
    if (stored && (stored.theme === 'system' || modes.has(stored.theme))) choice = stored.theme;
  } catch { /* The application reports invalid storage; the theme bootstrap never overwrites it. */ }
  media.addEventListener('change', () => apply());
  globalThis.argonHomeTheme = Object.freeze({apply, resolve, connect});
  apply();
})();
