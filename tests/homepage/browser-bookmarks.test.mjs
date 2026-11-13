import assert from 'node:assert/strict';
import test from 'node:test';
import {BrowserBookmarks,bookmarkRecords,bookmarkMetadata,previewBookmarkImport,connectBrowserBookmarks,cachedBookmarkIcon}
  from '../../chromium_overlay/chrome/browser/resources/argon_home/browser-bookmarks.mjs';
import {initialState,validateState,createDump,parseDump,dumpStats}
  from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

const snapshot=(revision,roots=[],extra={})=>({available:true,ready:true,canEdit:true,revision,roots,...extra});
const site=(id='7',name='Сайт')=>({id,name,url:'https://example.com/page#section'});
function setup(){
  const events=new Map(),calls=[],changes=[];
  const bindings={addWebUiListener:(name,callback)=>events.set(name,callback),sendWithPromise:async(...args)=>{calls.push(args);return snapshot(1);}};
  return {events,calls,changes,bindings,model:new BrowserBookmarks(bindings,value=>changes.push(value))};
}

test('native changes add, rename and remove bookmarks without a local copy or stale replies',async()=>{
  const {model,events,changes}=setup(),change=events.get('argon-bookmarks-changed');
  change(snapshot(1,[site()]));assert.equal(model.find('7').name,'Сайт');
  change(snapshot(3,[site('7','Другое имя')]));assert.equal(model.find('7').name,'Другое имя');
  assert.equal(model.update(snapshot(2,[site() ])),false);
  assert.equal(model.find('7').name,'Другое имя');
  change(snapshot(4,[]));assert.equal(model.find('7'),null);
  assert.equal(changes.length,3);
});

test('native folders retain arbitrary nesting and reject malformed snapshots atomically',()=>{
  const {model}=setup();
  const roots=[{id:'1',name:'Работа',type:'folder',children:[{id:'2',name:'Архив',type:'folder',children:[site()]}]}];
  assert(model.update(snapshot(1,roots)));assert.deepEqual(model.find('7'),site());
  for(const bad of [snapshot(2,[site('7'),site('7')]),snapshot(2,[{...site(),url:'javascript:alert(1)'}]),snapshot(2,[{...site(),id:'__proto__'}])]) {
    assert(!model.update(bad));assert.equal(model.revision,1);
  }
  assert.equal(model.find('1').children[0].name,'Архив');
});

test('loading, editing policy and server rejection never pretend that an import succeeded',async()=>{
  const {model,bindings,calls}=setup();
  model.update(snapshot(0,[],{ready:false}));
  await assert.rejects(model.import([]));assert.equal(calls.length,0);
  model.update(snapshot(1,[],{canEdit:false}));
  await assert.rejects(model.import([]));assert.equal(calls.length,0);
  model.update(snapshot(2));
  bindings.sendWithPromise=async()=>{throw Error('disk or model failure');};
  await assert.rejects(model.import([{name:'A',url:'https://a.example/',path:[]}]));
  assert.equal(model.revision,2);
});

test('migration and import preserve full addresses, folder paths and appearance metadata',async()=>{
  const {model,bindings,calls}=setup();model.update(snapshot(0));
  const old=[{id:'old',type:'folder',name:'Работа',children:[{id:'old-link',name:'Заголовок',url:'https://example.com/page#section',iconMode:'custom',icon:'book'}]},
    {id:'empty',type:'folder',name:'Пустая',children:[]}];
  const records=bookmarkRecords(old);
  assert.deepEqual(records[1],{name:'Заголовок',url:'https://example.com/page#section',path:['Работа'],iconMode:'custom',icon:'book'});
  assert(records.some(record=>record.folder&&record.name==='Пустая'));
  assert.equal(bookmarkMetadata(old).get(records[1].url).icon,'book');
  bindings.sendWithPromise=async(...args)=>{calls.push(args);return snapshot(2,[site()]);};
  await model.import(records);
  assert.deepEqual(calls,[['importArgonBookmarks',records]]);
  assert.deepEqual(previewBookmarkImport([site()],{records:[records[1],{name:'New',url:'https://new.example/',path:[]},{name:'Repeat',url:'https://new.example/',path:[]}],skipped:3}),
    {imported:1,duplicates:2,skipped:3,folders:0});
});

test('backup validates nested native paths and remappable icons while retaining the old profile schema',()=>{
  const state={...initialState(),bookmarksMigrated:true,bookmarkIcons:{'7':{iconMode:'custom',icon:'book'}},
    browserBookmarks:bookmarkRecords([{id:'1',name:'Folder',type:'folder',children:[site()]}],[],()=>({iconMode:'custom',icon:'book'}))};
  const parsed=parseDump(JSON.stringify(createDump(state)));
  assert.deepEqual(parsed.state,state);
  assert.equal(parsed.state.schemaVersion,2);
  assert.equal(dumpStats(parsed.state).sites,1);assert.equal(dumpStats(parsed.state).folders,1);
  for(const patch of [{bookmarksMigrated:'yes'},{bookmarkIcons:{bad:{iconMode:'custom'}}},{browserBookmarks:[{name:'X',url:'javascript:alert(1)',path:[]} ]},
    {browserBookmarks:[{name:'X',url:'https://example.com',path:Array(65).fill('folder')}]}])assert.throws(()=>validateState({...state,...patch}));
});

test('standalone previews never probe native bindings',async()=>{
  const previous=globalThis.location;globalThis.location={protocol:'https:'};
  try{assert.equal(await connectBrowserBookmarks(()=>{}),null);}finally{if(previous===undefined)delete globalThis.location;else globalThis.location=previous;}
});

test('a newer native event wins over an outstanding refresh without an error',async()=>{
  const {model,bindings,events}=setup();let complete;
  bindings.sendWithPromise=()=>new Promise(resolve=>{complete=resolve;});
  const pending=model.refresh();
  events.get('argon-bookmarks-changed')(snapshot(5,[site('7','Новое')]));
  complete(snapshot(4,[site('7','Старое')]));
  assert.equal(await pending,true);assert.equal(model.find('7').name,'Новое');
});

test('native favicons use the local profile cache without contacting bookmark sites or a favicon server',()=>{
  const url=new URL(cachedBookmarkIcon('https://example.com/a?b=2#part',2.5));
  assert.equal(url.protocol,'chrome:');assert.equal(url.hostname,'favicon2');
  assert.equal(url.searchParams.get('page_url'),'https://example.com/a?b=2#part');
  assert.equal(url.searchParams.get('scale_factor'),'3x');
  assert(!url.searchParams.has('allow_favicon_server_fallback'));
  assert.equal(cachedBookmarkIcon('javascript:alert(1)'),'');
});

test('migration keeps an inactive recovery tree and references its images without duplicating them',()=>{
  const icon='data:image/png;base64,iVBORw0KGgo=';
  const backup=[{id:'old-link',name:'Old',url:'https://example.com/',customIcon:icon,iconMode:'custom'}];
  const state={...initialState(),bookmarksMigrated:true,bookmarkMigrationBackup:backup,
    bookmarkIcons:{'100':{iconMode:'custom',legacyId:'old-link'}}};
  assert.deepEqual(parseDump(JSON.stringify(createDump(state))).state,state);
  assert.equal(JSON.stringify(state).split(icon).length,2,'the large image is stored only once');
  assert.throws(()=>validateState({...state,bookmarkMigrationBackup:[]}));
  assert.deepEqual(previewBookmarkImport([],{records:bookmarkRecords([{id:'empty',name:'Empty',type:'folder',children:[]}]),skipped:0}),
    {imported:0,duplicates:0,skipped:0,folders:1});
});

test('native backup restoration requests folder-aware merging explicitly',async()=>{
  const {model,bindings,calls}=setup();model.update(snapshot(0));
  bindings.sendWithPromise=async(...args)=>{calls.push(args);return snapshot(1);};
  const records=[{name:'Alias',url:'https://example.com/',path:['Folder']}];
  await model.import(records,{restore:true});
  assert.deepEqual(calls,[['importArgonBookmarks',records,'restore']]);
});
