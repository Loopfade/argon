// Copyright 2026 Argon contributors
// SPDX-License-Identifier: GPL-2.0-only
// Compatibility only. Permissions and elapsed-time budgets remain in browser code.
(() => {
  const slot = '__argonBackgroundPlaybackVisibility';
  if (!Object.hasOwn(window, slot)) {
    let enabled = false;
    const values = new Map([['hidden', false], ['visibilityState', 'visible']]);
    for (const [name, visibleValue] of values) {
      let prototype = document;
      let original;
      while (prototype && !original) {
        original = Object.getOwnPropertyDescriptor(prototype, name);
        prototype = Object.getPrototypeOf(prototype);
      }
      if (!original || typeof original.get !== 'function') continue;
      const readOriginal = original.get.bind(document);
      try {
        Object.defineProperty(document, name, {
          configurable: true,
          get: () => enabled ? visibleValue : readOriginal(),
        });
      } catch (_) { /* A site may have made its property non-configurable. */ }
    }
    const filter = event => {
      if (enabled) event.stopImmediatePropagation();
    };
    document.addEventListener('visibilitychange', filter, true);
    window.addEventListener('blur', filter, true);
    Object.defineProperty(window, slot, {
      value: value => { enabled = value === true; },
    });
  }
  if (typeof window[slot] === 'function') window[slot](ARGON_OVERRIDE_ACTIVE);
})();
