import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {language, t} from '../../chromium_overlay/chrome/browser/resources/argon_home/i18n.mjs';
import {initialState, createDump, parseDump, loadStoredState, validateState}
  from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

function inLanguage(locale, action) {
  const previous = globalThis.document;
  globalThis.document = {documentElement:{lang:locale}};
  try { return action(); }
  finally { globalThis.document = previous; }
}

test('Russian follows the browser locale and other locales fall back to English', () => {
  for (const locale of ['ru','ru-RU','RU_ru']) assert.equal(language(locale),'ru');
  for (const locale of ['en','en-US','de-DE','fr','']) assert.equal(language(locale),'en');
  inLanguage('ru',()=>assert.equal(t('Настроить'),'Настроить'));
  inLanguage('en',()=>assert.equal(t('Настроить'),'Customize'));
});

test('every static page fragment and validation message has an English translation', async () => {
  const root = new URL('../../chromium_overlay/chrome/browser/resources/argon_home/',import.meta.url);
  const sources = await Promise.all(['app.js','state.mjs'].map(name=>readFile(new URL(name,root),'utf8')));
  const html = await readFile(new URL('index.html',root),'utf8');
  inLanguage('en',()=>{
    for(const source of sources) {
      const messages = [...source.matchAll(/\bt\(("(?:\\.|[^"\\])*")/g)].map(match=>JSON.parse(match[1]));
      assert(messages.length>25,'must exercise the actual production messages');
      for(const message of messages) assert.doesNotMatch(t(message),/[А-Яа-яЁё]/,message);
    }
    assert.doesNotMatch(t(html),/[А-Яа-яЁё]/,'initial text, accessibility labels and placeholders');
    assert.throws(()=>validateState({...initialState(),shortcuts:[{}]}),/Invalid identifier/);
    assert.throws(()=>parseDump('broken'),/Could not read JSON/);
  });
});

test('language changes preserve Cyrillic user data, backup identity and legacy migration',()=>{
  const state=initialState();
  state.shortcuts=[{id:'user',name:'Избранное',url:'https://example.com/'}];
  state.customEngines=[{id:'custom',name:'Поисковая система',url:'https://example.com/?q={query}'}];
  state.engine='custom';
  const dump=inLanguage('ru',()=>createDump(state,new Date('2026-01-01T00:00:00Z')));
  inLanguage('en',()=>{
    assert.deepEqual(parseDump(JSON.stringify(dump)).state,state);
    const migrated=loadStoredState({shortcuts:[...state.shortcuts,
      {id:'mail',name:'Почта',url:'https://mail.google.com',icon:'mail',iconMode:'favicon'}]});
    assert.deepEqual(migrated.state.shortcuts,state.shortcuts);
    assert.equal(t('Некорректное поле «{label}» в дампе.',{label:'Название'}),
      'Invalid field «Название» in the backup.');
  });
});
