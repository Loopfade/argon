import test from 'node:test';
import assert from 'node:assert/strict';
import {HomepageStore, initialState, shortcutDraftFromHash}
  from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

const request = (url, title = '') => '#add-site?' + new URLSearchParams({url, title});
function stores() {
  const data = new Map([['home', JSON.stringify(initialState())]]);
  const storage = {getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value)};
  return {storage, first: new HomepageStore(storage, 'home'), second: new HomepageStore(storage, 'home')};
}

test('browser draft preserves the full address and bounded Unicode title', () => {
  const url = 'https://example.com/page?q=a%26b&next=%23anchor#part?title=other';
  assert.deepEqual(shortcutDraftFromHash(request(url, '  Страница\n & "сайт"  ')),
    {name: 'Страница & "сайт"', url, iconMode: 'favicon'});
  assert.equal(shortcutDraftFromHash(request(url)).name, 'example.com');
  assert.equal(shortcutDraftFromHash(request(url, 'а'.repeat(39) + '😀')).name, 'а'.repeat(39));
  assert.equal(shortcutDraftFromHash(request(url, 'а'.repeat(38) + '😀')).name.length, 40);
  assert.equal(shortcutDraftFromHash('#unrelated'), null);
});

test('browser draft rejects internal, executable, ambiguous and oversized addresses', () => {
  for (const url of ['chrome://settings/', 'javascript:alert(1)', 'data:text/html,test',
    'file:///tmp/test', 'about:blank', '/relative', 'https://', 'https://example.com/' + 'x'.repeat(16384)]) {
    assert.throws(() => shortcutDraftFromHash(request(url)));
  }
  for (const fragment of ['#add-site?title=only', request('https://example.com') + '&url=https://other.example',
    request('https://example.com') + '&title=second', '#add-site?' + 'x'.repeat(110001)]) {
    assert.throws(() => shortcutDraftFromHash(fragment));
  }
});

test('confirmed draft adds one shortcut without unlocking editing or overwriting another tab', () => {
  const {first, second} = stores();
  second.setEditing(true);
  second.update(state => {
    state.theme = 'dark';
    state.shortcuts.push({id:'folder', type:'folder', name:'Работа', children:[]});
  }, {shortcuts:true});
  first.addShortcut({id:'site', ...shortcutDraftFromHash(request('https://example.com/path', 'Страница'))}, 'folder');
  assert.equal(first.editing, false);
  assert.equal(first.latest().theme, 'dark');
  assert.equal(first.latest().shortcuts[0].children[0].url, 'https://example.com/path');
  assert.throws(() => first.update(state => { state.shortcuts = []; }, {shortcuts:true}));
});

test('duplicate and removed-folder checks use the latest stored tree and leave it intact', () => {
  const {first, second, storage} = stores();
  second.addShortcut({id:'existing', name:'Existing', url:'https://EXAMPLE.com:443/path?q=1#part'});
  const before = storage.getItem('home');
  assert.throws(() => first.addShortcut({id:'duplicate', name:'Duplicate', url:'https://example.com/path?q=1#part'}), /Existing/);
  assert.equal(storage.getItem('home'), before);
  assert.throws(() => first.addShortcut({id:'missing', name:'Missing', url:'https://other.example/'}, 'deleted-folder'));
  assert.equal(storage.getItem('home'), before);
  first.addShortcut({id:'another', name:'Another page', url:'https://example.com/path?q=2#part'});
  assert.equal(first.latest().shortcuts.length, 2);
});

test('quota or corrupt storage cannot partially save a browser draft', () => {
  const {first, storage} = stores();
  const before = storage.getItem('home');
  const original = storage.setItem;
  storage.setItem = () => { throw new Error('quota'); };
  assert.throws(() => first.addShortcut({id:'site', name:'Site', url:'https://example.com/'}), /quota/);
  assert.equal(storage.getItem('home'), before);
  assert.deepEqual(first.state, initialState());
  storage.setItem = original;
  storage.setItem('home', 'broken');
  assert.throws(() => first.addShortcut({id:'site', name:'Site', url:'https://example.com/'}));
  assert.equal(storage.getItem('home'), 'broken');
});
