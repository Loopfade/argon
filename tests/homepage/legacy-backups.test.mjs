import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {createDump,parseDump,restoreState,dumpStats} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';
import {bookmarkRecords} from '../../chromium_overlay/chrome/browser/resources/argon_home/browser-bookmarks.mjs';

const files=['argon-2026-10-28.json','argon-2026-11-13-before-native.json'];
for(const filename of files){
 const source=await readFile(new URL('../fixtures/homepage/backups/'+filename,import.meta.url),'utf8');
 const original=JSON.parse(source);
 test(filename+': a historical export retains folders, addresses, appearance and preferences',()=>{
  const restored=parseDump(source);
  assert.deepEqual(restored.state,original.state);
  assert.equal(restored.createdAt,original.createdAt);
  assert.equal(restored.version,1);
  for(const key of ['browserBookmarks','bookmarksMigrated','bookmarkIcons','bookmarkMigrationBackup'])assert.equal(restored.state[key],undefined);
  assert.equal(restored.state.engine,'my-search');
  assert.equal(restored.state.shortcuts.at(-1).id,'youtube','explicitly backed-up items must not be removed by sample-profile migration');
  assert.deepEqual(dumpStats(restored.state),{sites:5,folders:3,icons:1,engines:1});
 });
 test(filename+': conversion to native records preserves empty folders and duplicate addresses in their original locations',()=>{
  const records=bookmarkRecords(parseDump(source).state.shortcuts);
  assert.equal(records.length,8);
  assert(records.some(record=>record.folder&&record.name==='Пустая папка'&&record.path.length===0));
  const copies=records.filter(record=>record.url==='https://shared.example/page?q=one#part');
  assert.deepEqual(copies.map(record=>record.path),[['Работа'],['Личное']]);
  assert(copies[0].customIcon.startsWith('data:image/png;base64,'));
  assert.equal(copies[1].icon,'music');
  assert.equal(records[0].url,'https://news.example/?a=1&b=2#news');
  assert.equal(records.find(record=>record.name==='Документы').icon,'book');
 });
 test(filename+': re-export and local restore remain readable without rewriting the source file',async()=>{
  const parsed=parseDump(source),dump=createDump(parsed.state,new Date('2026-11-14T00:00:00Z'));
  assert.deepEqual(parseDump(JSON.stringify(dump)).state,original.state);
  let raw='previous';const state=restoreState({setItem:(key,value)=>{assert.equal(key,'test');raw=value;}},'test',parsed.state);
  assert.deepEqual(JSON.parse(raw),state);assert.deepEqual(state,original.state);
  assert.equal(await readFile(new URL('../fixtures/homepage/backups/'+filename,import.meta.url),'utf8'),source);
 });
}
test('missing optional fields retain their defaults while newer historical options are preserved',async()=>{
 const states=await Promise.all(files.map(async name=>parseDump(await readFile(new URL('../fixtures/homepage/backups/'+name,import.meta.url),'utf8')).state));
 assert.equal(states[0].shortcutScale,undefined);assert.equal(states[0].shortcutScaleMode,undefined);assert.equal(states[0].background,undefined);
 assert.equal(states[1].shortcutScale,145);assert.equal(states[1].shortcutScaleMode,'manual');
 assert.equal(states[1].theme,'amoled');assert.equal(states[1].background.source,'custom');
});
test('damaged or unsupported historical files cannot partially restore settings',async()=>{
 const good=JSON.parse(await readFile(new URL('../fixtures/homepage/backups/'+files[0],import.meta.url),'utf8'));
 for(const mutate of [dump=>{dump.version=99;},dump=>{dump.createdAt='invalid';},dump=>{dump.state.shortcuts[1].children[0].url='javascript:alert(1)';}]){
  const candidate=structuredClone(good);mutate(candidate);let writes=0;
  assert.throws(()=>restoreState({setItem(){writes++;}},'test',parseDump(JSON.stringify(candidate)).state));
  assert.equal(writes,0);
 }
});
