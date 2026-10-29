// The browser profile owns the default search provider. The page owns only its layout and choices.
export class BrowserSearch {
  constructor(bindings, onState, onAlpha) {
    this.bindings = bindings;
    this.onState = onState;
    this.onAlpha = onAlpha;
    this.state = null;
    this.revision = -1;
    this.lastBounds = '';
    bindings.addWebUiListener('argon-search-state-changed', state => this.update(state));
    bindings.addWebUiListener('argon-search-box-state', value => {
      if (value && Number.isFinite(value.alpha) && typeof value.focused === 'boolean')
        onAlpha(Math.max(0, Math.min(1, value.alpha)), value.focused);
    });
  }

  get available() { return this.state?.available === true; }
  get canChange() { return this.state?.canChange === true; }
  get engine() { return this.state?.engine; }

  update(state) {
    if (!state || !Number.isInteger(state.revision) || state.revision < 0 || state.revision < this.revision
        || typeof state.available !== 'boolean' || typeof state.canChange !== 'boolean'
        || typeof state.enabled !== 'boolean'
        || typeof state.engine?.id !== 'string' || typeof state.engine.name !== 'string'
        || typeof state.engine.url !== 'string') return;
    this.revision = state.revision;
    this.state = state;
    this.onState(state);
  }

  async refresh() {
    // A restored document can reconnect to a new Java adapter with identical CSS geometry.
    this.lastBounds = '';
    this.update(await this.bindings.sendWithPromise('getArgonSearchState'));
    return this.available;
  }

  async select(engine) {
    if (!this.available || !this.canChange) throw new Error('Search provider is controlled by the browser');
    this.update(await this.bindings.sendWithPromise('setArgonSearchEngine', {
      id: engine.id, name: engine.name, url: engine.url,
    }));
  }

  async focus(text = '') {
    return this.available && await this.bindings.sendWithPromise('focusArgonOmnibox', text) === true;
  }

  bounds(rect, scrollY) {
    if (!this.available) return;
    const originalTop = rect.top + scrollY;
    const values = [rect.left, rect.top, rect.width, rect.height,
      Math.max(0, Math.min(1, scrollY / Math.max(1, originalTop)))];
    if (!values.every(Number.isFinite) || rect.width <= 0 || rect.height <= 0) return;
    const key = JSON.stringify(values);
    if (key === this.lastBounds) return;
    this.lastBounds = key;
    this.bindings.send('setArgonSearchBoxBounds', values);
  }
}

export async function connectBrowserSearch(onState, onAlpha, bindings = null) {
  if (!bindings) {
    if (location.protocol !== 'chrome:' || typeof globalThis.chrome?.send !== 'function') return null;
    const {addWebUiListener, sendWithPromise} = await import('chrome://resources/js/cr.js');
    bindings = {addWebUiListener, sendWithPromise, send: (name, args) => chrome.send(name, args)};
  }
  const search = new BrowserSearch(bindings, onState, onAlpha);
  await search.refresh();
  return search;
}
