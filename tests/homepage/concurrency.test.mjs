import test from 'node:test';
import assert from 'node:assert/strict';
import {HomepageStore, initialState} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

function fixture() {
  const values = new Map([['home', JSON.stringify(initialState())]]);
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const queues = new Map();
  const locks = {
    request(key, options, operation) {
      assert.equal(options.mode, 'exclusive');
      const previous = queues.get(key) ?? Promise.resolve();
      let release;
      queues.set(key, new Promise(resolve => { release = resolve; }));
      return previous.then(operation).finally(release);
    },
  };
  return {values, storage, locks};
}

test('shared writes read the latest state only after obtaining the origin-wide lock', async () => {
  const {values, storage, locks} = fixture();
  const first = new HomepageStore(storage, 'home');
  const second = new HomepageStore(storage, 'home');
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let entered;
  const started = new Promise(resolve => { entered = resolve; });
  const firstWrite = first.exclusive(async () => {
    entered();
    await held;
    first.update(state => { state.showLabels = false; });
  }, locks);
  await started;
  const secondWrite = second.exclusive(() => {
    second.update(state => { state.newTab = true; });
  }, locks);
  assert.equal(JSON.parse(values.get('home')).newTab, false);
  release();
  await Promise.all([firstWrite, secondWrite]);
  const saved = JSON.parse(values.get('home'));
  assert.equal(saved.showLabels, false);
  assert.equal(saved.newTab, true);
});

test('a rejected write releases the lock and preserves the last saved state', async () => {
  const {values, storage, locks} = fixture();
  const page = new HomepageStore(storage, 'home');
  const previous = values.get('home');
  await assert.rejects(page.exclusive(() => page.update(() => {
    throw new Error('rejected edit');
  }), locks), /rejected edit/);
  assert.equal(values.get('home'), previous);
  await page.exclusive(() => page.update(state => { state.theme = 'amoled'; }), locks);
  assert.equal(JSON.parse(values.get('home')).theme, 'amoled');
});

test('missing cross-tab locking rejects a shared write before touching storage', async () => {
  const {values, storage} = fixture();
  const page = new HomepageStore(storage, 'home');
  const previous = values.get('home');
  await assert.rejects(page.exclusive(() => page.update(state => {
    state.newTab = true;
  }), null));
  assert.equal(values.get('home'), previous);
});
