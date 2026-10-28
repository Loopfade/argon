import test from 'node:test';
import assert from 'node:assert/strict';
import {HomepageStore, initialState, createDump, parseDump, moveShortcut, navigationTarget} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

function fixture(value=initialState()) {
  const values=new Map([['home',JSON.stringify(value)]]);
  return {values,storage:{getItem:key=>values.get(key)??null,setItem:(key,data)=>values.set(key,data)}};
}
const site=(id)=>({id,name:id,url:'https://'+id+'.example/'});

test('settings-only editing prevents layout changes outside the session; accepted edits survive reload',()=>{
  const {storage}=fixture(),page=new HomepageStore(storage,'home');
  assert.equal(page.editing,false);
  assert.throws(()=>page.update(s=>s.shortcuts.push(site('one')),{shortcuts:true}),/редактирование/);
  page.setEditing(true);
  page.update(s=>s.shortcuts.push(site('one')),{shortcuts:true});
  page.setEditing(false);
  const reopened=new HomepageStore(storage,'home');
  assert.equal(reopened.editing,false);
  assert.deepEqual(reopened.state.shortcuts,[site('one')]);
});

test('failed saving cannot change the active layout or the stored copy',()=>{
  const {storage,values}=fixture(); const page=new HomepageStore(storage,'home');page.setEditing(true);
  const previous=values.get('home'),state=page.state;
  storage.setItem=()=>{throw Error('quota');};
  assert.throws(()=>page.update(s=>s.shortcuts.push(site('one')),{shortcuts:true}),/quota/);
  assert.equal(page.state,state);assert.equal(values.get('home'),previous);
});

test('settings and layout changes preserve edits made in another home tab',()=>{
  const {storage}=fixture(),a=new HomepageStore(storage,'home'),b=new HomepageStore(storage,'home');
  a.setEditing(true);a.update(s=>s.shortcuts.push(site('one')),{shortcuts:true});
  b.update(s=>{s.theme='dark';});
  a.update(s=>s.shortcuts.push(site('two')),{shortcuts:true});
  assert.equal(a.state.theme,'dark');assert.deepEqual(a.state.shortcuts,[site('one'),site('two')]);
});

test('corrupt saved data is not silently overwritten by a later preference change',()=>{
  const {storage,values}=fixture();values.set('home','{broken');
  const page=new HomepageStore(storage,'home');assert(page.loadError);
  assert.throws(()=>page.update(s=>{s.theme='dark';}));assert.equal(values.get('home'),'{broken');
  page.replace(initialState());assert.equal(page.loadError,null);
});

test('moving sites into and out of folders preserves their data and prevents nested folders',()=>{
  const state={...initialState(),shortcuts:[site('one'),{id:'folder',name:'Folder',type:'folder',children:[site('two')]}]};
  moveShortcut(state.shortcuts,'one',null,'folder',1);
  assert.deepEqual(state.shortcuts[0].children,[site('two'),site('one')]);
  moveShortcut(state.shortcuts,'two','folder',null,0);
  assert.deepEqual(state.shortcuts[0],site('two'));
  assert.throws(()=>moveShortcut(state.shortcuts,'folder',null,'folder',0),/вложить/);
  assert.deepEqual(parseDump(JSON.stringify(createDump(state))).state,state);
});

test('density survives a dump and old dumps default to the normal size',()=>{
  const state={...initialState(),density:'large'};
  assert.equal(parseDump(JSON.stringify(createDump(state))).state.density,'large');
  const old=createDump(state);delete old.state.density;
  assert.equal(parseDump(JSON.stringify(old)).state.density,'comfortable');
});

test('web addresses open directly while text and unsafe protocols use the search engine',()=>{
  const engine='https://search.example/?q={query}';
  assert.equal(navigationTarget('example.com/path',engine),'https://example.com/path');
  assert.equal(navigationTarget('http://localhost:8080/a',engine),'http://localhost:8080/a');
  assert.equal(navigationTarget('localhost:3000',engine),'https://localhost:3000/');
  assert.equal(navigationTarget('192.168.1.1',engine),'https://192.168.1.1/');
  assert.equal(navigationTarget('ищем сайт',engine),'https://search.example/?q='+encodeURIComponent('ищем сайт'));
  assert.equal(navigationTarget('javascript:alert(1)',engine),'https://search.example/?q='+encodeURIComponent('javascript:alert(1)'));
  assert.equal(navigationTarget(' ',engine),null);
});


test('editing is never exported or resumed from storage and restore ends the session',()=>{
  const {storage,values}=fixture(),page=new HomepageStore(storage,'home');
  page.setEditing(true);page.update(s=>s.shortcuts.push(site('one')),{shortcuts:true});
  const dump=createDump(page.state);
  assert.equal(dump.state.editing,undefined);assert.equal(dump.state.locked,undefined);
  const stored=JSON.parse(values.get('home'));stored.editing=true;stored.locked=false;
  values.set('home',JSON.stringify(stored));
  assert.equal(new HomepageStore(storage,'home').editing,false);
  page.replace(dump.state);assert.equal(page.editing,false);
  assert.throws(()=>page.update(s=>s.shortcuts.push(site('two')),{shortcuts:true}),/редактирование/);
});
