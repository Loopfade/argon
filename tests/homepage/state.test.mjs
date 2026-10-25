import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState, loadStoredState, createDump, parseDump, restoreState, dumpStats} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

function sample() {
  return {...initialState(), theme: 'dark', engine: 'personal-search', showLabels: false, newTab: true,
    customEngines: [{id: 'personal-search', name: 'Мой поиск', url: 'https://search.example/?q={query}&lang=ru'}],
    shortcuts: [
      {id: 'my-folder', name: 'Проекты', type: 'folder', children: [{id: 'my-site', name: 'Мой сайт', url: 'https://example.com/?tag=работа', iconMode: 'custom', customIcon: 'data:image/png;base64,iVBORw0KGgo='}]},
      {id: 'another-site', name: 'Документы', url: 'https://docs.example.com', iconMode: 'favicon'}
    ]};
}

test('fresh page has no sample shortcuts', () => assert.deepEqual(initialState().shortcuts, []));

test('dump round trip preserves folders, uploaded icon, engine and every preference', () => {
  const state = sample();
  const restored = parseDump(JSON.stringify(createDump(state, new Date('2026-10-24T18:00:00Z'))));
  assert.deepEqual(restored.state, state);
  assert.deepEqual(dumpStats(restored.state), {sites: 2, folders: 1, engines: 1});
  assert.equal(restored.createdAt, '2026-10-24T18:00:00.000Z');
});

test('migration removes original examples and keeps user edits and folder additions', () => {
  const legacy = {...sample(), shortcuts: [
    {id: 'youtube', name: 'YouTube', url: 'https://youtube.com', icon: 'youtube', iconMode: 'favicon'},
    {id: 'github', name: 'Мои репозитории', url: 'https://github.com', icon: 'github', iconMode: 'favicon'},
    {id: 'work', name: 'Работа', type: 'folder', children: [
      {id: 'figma', name: 'Figma', url: 'https://figma.com', icon: 'figma', iconMode: 'favicon'},
      sample().shortcuts[1]
    ]},
    {id: 'read', name: 'Читать', type: 'folder', children: []}
  ]};
  delete legacy.schemaVersion;
  const result = loadStoredState(legacy);
  assert.equal(result.migrated, true);
  assert.deepEqual(result.state.shortcuts.map(x => x.id), ['github', 'work']);
  assert.deepEqual(result.state.shortcuts[1].children, [sample().shortcuts[1]]);
  assert.equal(result.state.theme, 'dark');
  assert.equal(result.state.engine, 'personal-search');
  assert.equal(legacy.shortcuts[2].children.length, 2);
});

test('migration does not repeatedly remove explicitly restored or newly chosen shortcuts', () => {
  const state = initialState();
  state.shortcuts = [{id: 'youtube', name: 'YouTube', url: 'https://youtube.com', icon: 'youtube', iconMode: 'favicon'}];
  assert.deepEqual(loadStoredState(state), {state, migrated: false});
});

test('restore replaces settings as a single saved state', () => {
  const data = new Map([['settings', JSON.stringify(initialState())]]);
  const restored = restoreState({setItem: (key, value) => data.set(key, value)}, 'settings', sample());
  assert.deepEqual(JSON.parse(data.get('settings')), sample());
  assert.deepEqual(restored, sample());
});

test('a storage failure leaves the previous state intact', () => {
  const previous = initialState();
  const saved = JSON.stringify(previous);
  let current = previous;
  assert.throws(() => { current = restoreState({setItem() { throw new Error('quota'); }}, 'settings', sample()); }, /quota/);
  assert.equal(current, previous);
  assert.equal(JSON.stringify(current), saved);
});

test('invalid dump is rejected before storage is touched', () => {
  let writes = 0;
  const dump = createDump(sample());
  dump.state.shortcuts[1].url = 'javascript:alert(1)';
  assert.throws(() => restoreState({setItem() { writes++; }}, 'settings', parseDump(JSON.stringify(dump)).state), /http/);
  assert.equal(writes, 0);
});

test('rejects corrupt files, wrong app and unsupported versions', () => {
  for (const source of ['{', 'null', '{}', JSON.stringify({...createDump(sample()), version: 99})]) assert.throws(() => parseDump(source));
});

test('rejects broken references, duplicate IDs, nested folders and unsafe icon data', () => {
  const changes = [
    s => {s.engine = 'missing';},
    s => {s.customEngines[0].url = 'https://example.com';},
    s => {s.customEngines[0].id = 'google';},
    s => {s.shortcuts[1].id = 'my-site';},
    s => {s.shortcuts[0].children.push({id: 'nested', name: 'Nested', type: 'folder', children: []});},
    s => {s.shortcuts[1].icon = '"><script>';},
    s => {s.shortcuts[0].children[0].customIcon = 'data:text/html;base64,AAAA';},
    s => {s.showLabels = 'false';}
  ];
  for (const change of changes) {
    const dump = createDump(sample());
    change(dump.state);
    assert.throws(() => parseDump(JSON.stringify(dump)));
  }
});
