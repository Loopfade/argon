import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState, loadStoredState, createDump, parseDump, HomepageStore}
  from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

function fixture(percent=135) {
  return {...initialState(),theme:'amoled',density:'compact',shortcutScaleMode:'manual',shortcutScale:percent,
    engine:'mine',customEngines:[{id:'mine',name:'Мой поиск',url:'https://search.example/?q={query}'}],
    shortcuts:[{id:'folder',type:'folder',name:'Работа',children:[
      {id:'docs',name:'Документы',url:'https://docs.example/',iconMode:'custom',customIcon:'data:image/png;base64,aGVsbG8='}
    ]}]};
}

test('manual scale and every homepage preference survive backup, load and restoration',()=>{
  for(const percent of [50,100,135,200]) {
    const expected=fixture(percent),dump=parseDump(JSON.stringify(createDump(expected)));
    assert.deepEqual(dump.state,expected);
    assert.deepEqual(loadStoredState(dump.state).state,expected);
    let saved;
    const storage={getItem:()=>saved??null,setItem:(_,value)=>{saved=value;}};
    const store=new HomepageStore(storage,'home');
    store.replace(dump.state);
    assert.deepEqual(new HomepageStore(storage,'home').state,expected);
    assert.equal(dump.state.schemaVersion,2);
    assert.equal(dump.state.density,'compact','legacy builds must receive a known density');
  }
});

test('old backups retain their preset without acquiring manual settings',()=>{
  for(const density of ['compact','comfortable','large']) {
    const old={...initialState(),density};
    const restored=parseDump(JSON.stringify(createDump(old))).state;
    assert.deepEqual(restored,old);
    assert(!Object.hasOwn(restored,'shortcutScale'));
    assert(!Object.hasOwn(restored,'shortcutScaleMode'));
  }
  const legacy={...initialState()};delete legacy.schemaVersion;delete legacy.density;
  assert.deepEqual(loadStoredState(legacy).state,initialState());
});

test('preset switches and unrelated writes preserve the remembered manual percentage',()=>{
  let saved=JSON.stringify(fixture(175));
  const store=new HomepageStore({getItem:()=>saved,setItem:(_,value)=>{saved=value;}},'home');
  store.update(state=>{state.density='comfortable';state.shortcutScaleMode='preset';});
  store.update(state=>{state.newTab=true;});
  const restored=parseDump(JSON.stringify(createDump(store.state))).state;
  assert.equal(restored.shortcutScale,175);
  assert.equal(restored.shortcutScaleMode,'preset');
  assert.equal(restored.density,'comfortable');
  assert.equal(restored.newTab,true);
  assert.deepEqual(restored.shortcuts,fixture().shortcuts);
});

test('invalid manual settings cannot overwrite the previous saved homepage',()=>{
  const saved=JSON.stringify(fixture()),writes=[];
  const store=new HomepageStore({getItem:()=>saved,setItem:(_,value)=>writes.push(value)},'home');
  const invalid=[...['49',49,201,0,-1,NaN,Infinity,100.5,null].map(shortcutScale=>({...fixture(),shortcutScale})),
    {...fixture(),shortcutScaleMode:'unknown'},
    {...fixture(),shortcutScale:undefined}];
  for(const state of invalid)assert.throws(()=>store.replace(state));
  assert.deepEqual(writes,[]);
  assert.deepEqual(store.state,fixture());
});
