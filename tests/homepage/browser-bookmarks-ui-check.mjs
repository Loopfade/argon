import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const nativeMock = `
import {BrowserBookmarks} from './browser-bookmarks-production.js';
export {bookmarkRecords,bookmarkMetadata,previewBookmarkImport,cachedBookmarkIcon} from './browser-bookmarks-production.js';
export async function connectBrowserBookmarks(onState) {
 const listeners=new Map();
 const backend=window.nativeBookmarks={revision:0,canEdit:true,fail:false,next:100,
   roots:[{id:'1',name:'Штатная закладка',url:'https://existing.example/'}],imports:[],
   publish(){sessionStorage.setItem('native-bookmarks-fixture',JSON.stringify({roots:this.roots,next:this.next,revision:this.revision,canEdit:this.canEdit}));listeners.get('argon-bookmarks-changed')?.(this.snapshot());},
   snapshot(){const nodes=(items,parentId='0')=>items.map((item,index)=>({...item,parentId,index,editable:item.editable!==false,canAdd:item.type==='folder'&&item.canAdd!==false,...(item.children?{children:nodes(item.children,item.id)}:{})})).filter(item=>item.type==='folder'||/^https?:/.test(item.url));return structuredClone({available:true,ready:true,canEdit:this.canEdit,revision:this.revision,roots:nodes(this.roots),defaultParentId:'0',truncated:false});},
   replace(roots){this.roots=structuredClone(roots);this.revision++;this.publish();},
   all(items=this.roots){return items.flatMap(item=>[item,...(item.children?this.all(item.children):[])]);},
   locate(id,items=this.roots,parentId='0'){for(let index=0;index<items.length;index++){const item=items[index];if(item.id===id)return {item,index,items,parentId};if(item.children){const found=this.locate(id,item.children,item.id);if(found)return found;}}},
   edit(operation){
    if(!this.canEdit||this.fail)throw Error('native write failure');
    const source=operation.id&&this.locate(operation.id);
    if(operation.action!=='create'&&(!source||source.item.editable===false))throw Error('not editable');
    if(source){const expected={name:source.item.name,parentId:source.parentId,index:source.index,...(source.item.type==='folder'?{}:{url:source.item.url})};if(JSON.stringify(expected)!==JSON.stringify(operation.expected))throw Error('stale source');}
    const parent=operation.parentId&&operation.parentId!=='0'?this.locate(operation.parentId)?.item:null;
    if(operation.action!=='remove'&&operation.parentId&&operation.parentId!=='0'&&!parent)throw Error('missing parent');
    if(parent&&parent.type!=='folder')throw Error('not a folder');
    const target=parent?parent.children:this.roots;
    if(parent&&parent.canAdd===false)throw Error('not writable');
    let item=source?.item;
    if(item?.type==='folder'&&parent){const contains=folder=>folder.id===parent.id||(folder.children??[]).some(child=>child.type==='folder'&&contains(child));if(contains(item))throw Error('folder cycle');}
    if(operation.preservePosition&&source?.items!==target)throw Error('changed parent');
    if(operation.action!=='remove'&&!operation.preservePosition&&operation.beforeId&&(!target.some(entry=>entry.id===operation.beforeId)||operation.beforeId===item?.id))throw Error('missing anchor');
    if(operation.action==='create')item={id:String(this.next++),name:operation.name,...(operation.folder?{type:'folder',children:[]}:{url:operation.url})};
    if(operation.action==='remove')source.items.splice(source.index,1,...(item.children??[]));
    else {
     if(operation.action==='update'){item.name=operation.name;if(!operation.folder)item.url=operation.url;}
     if(!operation.preservePosition){
      if(source)source.items.splice(source.index,1);
      const index=operation.beforeId?target.findIndex(entry=>entry.id===operation.beforeId):target.length;
      target.splice(index,0,item);
     }
    }
    this.lastOperation=structuredClone(operation);
    this.revision++;this.publish();return {id:item.id,snapshot:this.snapshot()};
   },
   add(records,mode){
    if(this.fail)throw Error('native write failure');
    this.imports.push(structuredClone(records));
    const seen=new Set(this.all().filter(item=>item.url).map(item=>item.url));
    for(const record of records){
      if(mode!=='restore'&&!record.folder&&seen.has(record.url))continue;
      let target=this.roots;
      const path=record.folder?[...record.path,record.name]:record.path;
      for(const name of path){let folder=target.find(item=>item.type==='folder'&&item.name===name);if(!folder){folder={id:String(this.next++),name,type:'folder',children:[]};target.push(folder);}target=folder.children;}
      if(!record.folder&&!(mode==='restore'&&target.some(item=>item.url===record.url&&item.name===record.name))){target.push({id:String(this.next++),name:record.name,url:record.url});seen.add(record.url);}
    }
    this.revision++;this.publish();return this.snapshot();
   }};
 const saved=sessionStorage.getItem('native-bookmarks-fixture');if(saved)Object.assign(backend,JSON.parse(saved));
 backend.managerRequests=[];backend.managerAvailable=true;
 const bindings={addWebUiListener:(name,callback)=>listeners.set(name,callback),
   sendWithPromise:async(name,value,mode)=>{
    if(name==='getArgonBookmarks')return backend.snapshot();
    if(name==='openArgonBookmarkManager'){backend.managerRequests.push(value);return backend.managerAvailable;}
    if(name==='editArgonBookmarks')return backend.edit(value);
    return backend.add(value,mode);
   }};
 const model=new BrowserBookmarks(bindings,onState);await model.refresh();return model;
}`;

export async function checkBrowserBookmarks(browser,base,errors,out) {
 const context=await browser.newContext({viewport:{width:320,height:850},locale:'ru-RU',acceptDownloads:true});
 try {
  await context.route('https://**/*',route=>route.abort());
  await context.route(base+'browser-bookmarks.js',route=>route.fulfill({contentType:'text/javascript',body:nativeMock}));
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const legacy={schemaVersion:2,theme:'amoled',engine:'google',customEngines:[],density:'comfortable',showLabels:true,newTab:false,
    shortcuts:[{id:'old',name:'Прежнее имя',url:'https://existing.example/',iconMode:'custom',icon:'book'},
      {id:'work',name:'Работа',type:'folder',children:[{id:'new',name:'Переносимый сайт',url:'https://migrated.example/a?q=b#c',iconMode:'custom',icon:'music'}]},
      {id:'empty',name:'Пустая папка',type:'folder',children:[]}]};
  await page.addInitScript(value=>{if(localStorage.getItem('argon-start-v1')===null)localStorage.setItem('argon-start-v1',JSON.stringify(value));},legacy);
  await page.goto(base);
  await page.waitForFunction(()=>JSON.parse(localStorage.getItem('argon-start-v1')).bookmarksMigrated);
  const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));
  const wait=()=>page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));
  assert.deepEqual((await state()).shortcuts,[],'migration leaves one authoritative URL list in BookmarkModel');
  assert.deepEqual((await state()).bookmarkMigrationBackup,legacy.shortcuts,'keep recovery data until native asynchronous persistence has completed');
  assert.equal((await state()).bookmarkIcons['1'].icon,'book');
  assert.equal(await page.getByRole('link',{name:'Штатная закладка',exact:true}).count(),1,'existing native title wins over a duplicate shortcut');
  assert(await page.locator('#browser-bookmarks-manager').isVisible());
  assert.equal(await page.locator('#browser-bookmarks-manager').getAttribute('href'),null);
  assert.equal(await page.locator('#browser-bookmarks-manager').getAttribute('aria-label'),'Все закладки');
  assert.equal((await page.locator('#browser-bookmarks-manager').textContent()).trim(),'');
  const toolbarState=await state(),pencil=await page.locator('#edit-bookmarks').boundingBox();
  for(const id of ['browser-bookmarks-manager','edit-bookmarks']) {
    const bounds=await page.locator('#'+id).boundingBox();
    assert(bounds.width>=44&&bounds.height>=44,'compact icons must retain touch targets');
  }
  await page.locator('#edit-bookmarks').click();
  assert(await page.locator('#edit-bookmarks').isHidden());
  assert(await page.locator('#edit-done').isVisible());
  assert.equal(await page.evaluate(()=>document.activeElement.id),'edit-done');
  const check=await page.locator('#edit-done').boundingBox();
  assert.equal(check.x,pencil.x);assert.equal(check.y,pencil.y,'check must replace the pencil without moving');
  assert(await page.locator('#browser-bookmarks-manager').isVisible());
  await page.locator('#edit-done').click();
  assert.equal(await page.evaluate(()=>document.activeElement.id),'edit-bookmarks');
  assert.deepEqual(await state(),toolbarState,'entering and leaving edit mode must not write bookmarks');
  assert(await page.locator('#bookmark-add-hint').isVisible());
  const homeUrl=page.url();
  await page.locator('#browser-bookmarks-manager').click();
  assert.deepEqual(await page.evaluate(()=>nativeBookmarks.managerRequests),['']);
  assert.equal(page.url(),homeUrl,'opening bookmarks must use the Android launcher, not renderer navigation');
  await page.evaluate(()=>nativeBookmarks.managerAvailable=false);
  await page.locator('#browser-bookmarks-manager').click();
  assert.equal(await page.locator('#toast').textContent(),'Не удалось открыть закладки браузера.');
  await page.evaluate(()=>nativeBookmarks.managerAvailable=true);
  await page.getByRole('button',{name:'Папка Работа',exact:true}).click();
  assert.equal(await page.getByRole('link',{name:'Переносимый сайт',exact:true}).getAttribute('href'),'https://migrated.example/a?q=b#c');
  await page.locator('#browser-folder-back').click();
  const imports=await page.evaluate(()=>nativeBookmarks.imports.length);
  await page.reload();await page.getByRole('link',{name:'Штатная закладка',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>nativeBookmarks.imports.length),0,'a deleted native bookmark must not be resurrected from old shortcuts');
  // Simulate native manager changes. The homepage never writes a local URL mirror.
  await page.evaluate(()=>nativeBookmarks.replace([{id:'1',name:'Переименована',url:'https://existing.example/'},
    {id:'2',name:'Глубоко',type:'folder',children:[{id:'3',name:'Внутри',type:'folder',children:[{id:'4',name:'Ссылка',url:'https://nested.example/'}]}]}]));
  await page.getByRole('link',{name:'Переименована',exact:true}).waitFor();
  assert.equal(await page.getByRole('link',{name:'Штатная закладка',exact:true}).count(),0);
  await page.getByRole('button',{name:'Папка Глубоко',exact:true}).click();
  await page.getByRole('button',{name:'Папка Внутри',exact:true}).click();
  assert(await page.getByRole('link',{name:'Ссылка',exact:true}).isVisible());
  await page.locator('#browser-folder-manager').click();
  assert.equal(await page.evaluate(()=>nativeBookmarks.managerRequests.at(-1)),'3');
  assert.equal(page.url(),homeUrl);
  await page.locator('#browser-folder-back').click();
  assert.equal(await page.locator('#dialog-title').textContent(),'Глубоко');
  await page.locator('#browser-folder-back').click();
  assert.equal(await page.locator('#modal-root').innerHTML(),'');
  await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();
  await page.locator('[data-browser-bookmark="1"]').click();
  await page.locator('#native-icon').click();
  assert.equal(await page.locator('#dialog-title').textContent(),'Иконка закладки');
  assert(await page.locator('#shortcut-title').evaluate(node=>node.readOnly));
  assert(await page.locator('#shortcut-url').evaluate(node=>node.readOnly));
  await page.locator('#shortcut-form button[type=submit]').click();await wait();
  assert.equal((await state()).bookmarkIcons['1'].icon,'book','saving a built-in custom icon must not require an uploaded file');
  await page.locator('#edit-done').click();
  await page.locator('#settings-button').click();
  await page.locator('#density-select').selectOption('manual');await wait();
  await page.locator('#shortcut-scale').fill('45');await page.locator('#shortcut-scale').press('Enter');
  assert(await page.locator('#shortcut-scale-error').isVisible());
  await page.locator('#shortcut-scale').fill('50');
  assert(await page.locator('#shortcut-scale-error').isHidden(),'correcting input must clear the obsolete validation error before blur');
  await page.locator('#shortcut-scale').press('Enter');await wait();
  await page.locator('#data-settings').click();
  const download=page.waitForEvent('download');await page.locator('#backup-export').click();
  const bytes=await readFile(await(await download).path()),dump=JSON.parse(bytes);
  assert(dump.state.browserBookmarks.some(record=>record.url==='https://existing.example/'&&record.icon==='book'));
  assert(dump.state.browserBookmarks.some(record=>record.url==='https://nested.example/'&&record.path.join('/')==='Глубоко/Внутри'));
  await page.evaluate(()=>{nativeBookmarks.next=500;nativeBookmarks.replace([{id:'99',name:'Существующая',url:'https://preserved.example/'}]);});
  await page.locator('#backup-file').setInputFiles({name:'bookmarks.json',mimeType:'application/json',buffer:bytes});
  assert((await page.locator('.restore-note').textContent()).includes('без удаления существующих'));
  await page.locator('#confirm-restore').click();await wait();
  assert(await page.getByRole('link',{name:'Существующая',exact:true}).isVisible(),'restore adds bookmarks without deleting a native list');
  assert(await page.getByRole('link',{name:'Переименована',exact:true}).isVisible());
  const remapped=await page.evaluate(()=>nativeBookmarks.all().find(item=>item.url==='https://existing.example/').id);
  assert.notEqual(remapped,'1');assert.equal((await state()).bookmarkIcons[remapped].icon,'book','portable restore remaps custom icons by URL to new native IDs');
  await checkBookmarkEditing(page,wait,state,out);
  await checkBookmarkEdgeCases(page,wait);
  await page.evaluate(()=>nativeBookmarks.replace([]));
  await page.getByText('Нет закладок',{exact:true}).waitFor();
  await page.reload();await page.getByText('Нет закладок',{exact:true}).waitFor();
  assert.equal((await state()).shortcuts.length,0);
  if(out)await page.screenshot({path:resolve(out,'home-native-bookmarks-320.png'),fullPage:true,animations:'disabled'});
  assert(imports>0);
  console.log('PASS: native bookmarks, homepage creation/editing, touch-friendly ordering, drag, folders, safe deletion, migration and backup');
 } finally {await context.close();}
 await checkLegacyBackupRestores(browser,base,errors);
}

async function checkLegacyBackupRestores(browser,base,errors) {
 for(const [index,filename]of ['argon-2026-10-28.json','argon-2026-11-13-before-native.json'].entries()){
  const bytes=await readFile(new URL('../fixtures/homepage/backups/'+filename,import.meta.url));
  const original=JSON.parse(bytes);
  const context=await browser.newContext({viewport:{width:320,height:850},locale:'ru-RU',acceptDownloads:true});
  try {
   await context.route('https://**/*',route=>route.abort());
   await context.route(base+'browser-bookmarks.js',route=>route.fulfill({contentType:'text/javascript',body:nativeMock}));
   const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
   await page.goto(base);await page.waitForFunction(()=>JSON.parse(localStorage.getItem('argon-start-v1')??'null')?.bookmarksMigrated);
   const existing={id:'900',name:'Existing native bookmark',url:'https://existing.example/'};
   await page.evaluate(item=>{nativeBookmarks.next=1000;nativeBookmarks.replace([item]);},existing);
   const wait=async()=>{await page.waitForFunction(()=>!document.body.inert);await page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
   const state=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));
   const native=()=>page.evaluate(()=>structuredClone(nativeBookmarks.roots));
   const openData=async()=>{await page.locator('#settings-button').click();await page.locator('#data-settings').click();};
   const upload=async()=>{await page.locator(index?'#bookmarks-file':'#backup-file').setInputFiles({name:filename,mimeType:'application/json',buffer:index?Buffer.concat([Buffer.from('\ufeff'),bytes]):bytes});};
   const shape=items=>items.map(item=>item.children?{name:item.name,type:'folder',children:shape(item.children)}:{name:item.name,url:new URL(item.url).href});
   const expected=shape([existing,...original.state.shortcuts]);
   await openData();const before=await state();await upload();
   assert((await page.locator('.restore-note').textContent()).includes('без удаления существующих'));
   await page.locator('#cancel-restore').click();assert.deepEqual(await state(),before);assert.deepEqual(await native(),[existing]);
   await upload();await page.locator('#confirm-restore').click();await wait();
   await page.waitForFunction(()=>document.querySelector('#modal-root').children.length===0);
   assert.deepEqual(shape(await native()),expected,filename+': native restoration preserves both copies of an address and empty folders');
   let current=await state();assert.deepEqual(current.shortcuts,[]);assert.equal(current.bookmarksMigrated,true);
   assert.deepEqual(current.bookmarkMigrationBackup,original.state.shortcuts);
   for(const key of ['theme','engine','customEngines','density','showLabels','newTab','shortcutScale','shortcutScaleMode','background'])assert.deepEqual(current[key],original.state[key],key);
   const all=items=>items.flatMap(item=>[item,...(item.children?all(item.children):[])]);
   const items=all(await native());
   const uploaded=items.find(item=>item.name==='Общий сайт в работе'),preset=items.find(item=>item.name==='Общий сайт в личном');
   assert.notEqual(uploaded.id,'old-image');
   const appearance=current.bookmarkIcons[uploaded.id];
   const storedImage=appearance.customIcon??current.bookmarkMigrationBackup.flatMap(item=>item.children??[item]).find(item=>item.id===appearance.legacyId)?.customIcon;
   assert.equal(storedImage,original.state.shortcuts[1].children[0].customIcon);
   assert.equal(current.bookmarkIcons[preset.id].icon,'music');
   await openData();const download=page.waitForEvent('download');await page.locator('#backup-export').click();
   const exported=JSON.parse(await readFile(await(await download).path(),'utf8'));
   assert.equal(exported.state.browserBookmarks.filter(record=>record.url==='https://shared.example/page?q=one#part').length,2);
   assert(exported.state.browserBookmarks.some(record=>record.folder&&record.name==='Пустая папка'));
   await upload();await page.locator('#confirm-restore').click();await wait();
   assert.deepEqual(shape(await native()),expected,'restoring the same old file twice does not multiply bookmarks');
   await page.reload();await page.getByRole('link',{name:'Existing native bookmark',exact:true}).waitFor();
   assert.deepEqual(shape(await native()),expected);assert.equal(await page.evaluate(()=>nativeBookmarks.imports.length),0,'reload must not re-import the archived old shortcuts');
   current=await state();const nativeBefore=await native();
   await openData();const damaged=structuredClone(original);damaged.state.shortcuts[1].children[0].url='javascript:alert(1)';
   await page.locator('#backup-file').setInputFiles({name:'damaged.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(damaged))});
   await page.locator('#backup-error').waitFor({state:'visible'});assert.equal(await page.locator('#confirm-restore').count(),0);
   assert.deepEqual(await state(),current);assert.deepEqual(await native(),nativeBefore);
   await page.evaluate(()=>{nativeBookmarks.canEdit=false;nativeBookmarks.replace(nativeBookmarks.roots);});
   await upload();await page.locator('#confirm-restore').click();await wait();
   await page.locator('#backup-error').waitFor({state:'visible'});assert.deepEqual(await state(),current);assert.deepEqual(await native(),nativeBefore);
   console.log('PASS: historical backup '+filename+', native folders, alias URLs, icons, preferences, cancel, repeat, re-export, reload and rejection');
  } finally {await context.close();}
 }
}

async function checkBookmarkEdgeCases(page,wait) {
 const a={id:'10',name:'A',url:'https://a.example/'};
 const hidden={id:'11',name:'Hidden',url:'chrome://settings/'};
 const b={id:'12',name:'B',url:'https://b.example/'};
 const folderA={id:'20',name:'Folder A',type:'folder',children:[]};
 const folderB={id:'21',name:'Folder B',type:'folder',children:[]};
 const replace=roots=>page.evaluate(roots=>{nativeBookmarks.fail=false;nativeBookmarks.canEdit=true;nativeBookmarks.replace(roots);},roots);
 const order=()=>page.evaluate(()=>nativeBookmarks.roots.map(item=>item.id));
 const edit=async id=>{await page.locator('[data-browser-bookmark="'+id+'"]').click();await page.locator('#native-edit').click();};
 const move=async id=>{await page.locator('[data-browser-bookmark="'+id+'"]').click();await page.locator('#native-move').click();};
 const open=async id=>{await page.locator('[data-browser-bookmark="'+id+'"]').click();await page.locator('#native-open-folder').click();};
 const save=async()=>{await page.locator('#native-bookmark-form button[type=submit]').click();await wait();};
 await replace([a,hidden,b]);
 await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();
 assert.equal(await page.locator('[data-browser-bookmark="11"]').count(),0);
 await edit('10');await save();assert.deepEqual(await order(),['10','11','12'],'unchanged save retains hidden native positions');
 await edit('10');await page.locator('#native-title').fill('Renamed A');await page.locator('#native-url').fill('https://a.example/changed');
 await save();assert.deepEqual(await order(),['10','11','12'],'content-only edits never relocate bookmarks');
 assert.equal(await page.evaluate(()=>nativeBookmarks.lastOperation.preservePosition),true);
 await edit('10');await page.locator('#native-position').selectOption('');await save();
 assert.deepEqual(await order(),['11','12','10'],'an explicit position change still moves the bookmark');

 await replace([a,hidden]);await edit('10');await page.locator('#native-title').fill('Unsaved');
 await page.keyboard.press('Escape');assert.deepEqual(await order(),['10','11']);
 assert.equal(await page.evaluate(()=>nativeBookmarks.locate('10').item.name),'A');
 await edit('10');await page.locator('#native-title').fill('Retained draft');
 await page.locator('#native-url').fill('javascript:alert(1)');await save();assert(await page.locator('#native-edit-error').isVisible());
 await page.locator('#native-url').fill('https://a.example/');await page.evaluate(()=>nativeBookmarks.fail=true);
 await save();assert(await page.locator('#native-edit-error').isVisible());
 assert.equal(await page.locator('#native-title').inputValue(),'Retained draft');
 assert.deepEqual(await order(),['10','11']);
 await page.evaluate(()=>nativeBookmarks.fail=false);await save();assert.deepEqual(await order(),['10','11'],'hidden tail remains after a successful retry');

 await replace([folderA,hidden,b]);await edit('20');await page.locator('#native-title').fill('Renamed folder');await save();
 assert.deepEqual(await order(),['20','11','12'],'renaming a folder retains hidden sibling order');
 await replace([{...folderA,children:[a,hidden]}]);await open('20');await edit('10');await save();
 assert.deepEqual(await page.evaluate(()=>nativeBookmarks.locate('20').item.children.map(item=>item.id)),['10','11']);
 await page.locator('#browser-folder-back').click();

 await replace([folderA,folderB]);await move('20');
 await page.locator('[data-browser-bookmark="20"]').dragTo(page.locator('[data-browser-bookmark="21"]'));await wait();
 assert.deepEqual(await order(),['21']);
 assert.deepEqual(await page.evaluate(()=>nativeBookmarks.locate('21').item.children.map(item=>item.id)),['20'],'folder drag nests it inside the target');
 await open('21');await move('20');await page.locator('#native-move-cancel').click();
 assert(await page.locator('#browser-folder-edit').isVisible());
 await move('20');await page.locator('#browser-folder-back').click();
 assert(await page.locator('#move-toolbar').isHidden(),'leaving the source folder cancels its pending move');
 assert.equal(await page.locator('[draggable="true"]').count(),0);
 await open('21');await move('20');await page.locator('#native-move-target').click();
 await page.locator('#native-parent').selectOption('');await save();
 assert.deepEqual(await order(),['21','20'],'the destination picker moves nested folders back to favorites');
 await move('20');await page.locator('[data-browser-bookmark="21"]').click();await wait();
 assert.deepEqual(await order(),['21']);
 assert.equal(await page.evaluate(()=>nativeBookmarks.locate('20').parentId),'21','tap-to-move nests folders too');
 await move('21');await page.locator('#move-destination').click();
 assert.equal(await page.locator('#native-parent option[value="20"]').count(),0,'descendants are excluded from folder destinations');
 await page.keyboard.press('Escape');assert(await page.locator('#move-toolbar').isHidden());

 await replace([folderA,folderB]);await move('20');
 const from=await page.locator('[data-browser-bookmark="20"]').boundingBox(),to=await page.locator('[data-browser-bookmark="21"]').boundingBox();
 const cdp=await page.context().newCDPSession(page);
 await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:from.x+from.width/2,y:from.y+from.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:to.x+to.width/2,y:to.y+to.height/2}]});
 await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await wait();await cdp.detach();
 assert.deepEqual(await order(),['21']);assert.equal(await page.evaluate(()=>nativeBookmarks.locate('20').parentId),'21','touch dragging nests folders');

 await replace([a,folderA]);await move('10');
 await page.evaluate(()=>{nativeBookmarks.canEdit=false;nativeBookmarks.replace(nativeBookmarks.roots);});
 assert(await page.locator('#move-toolbar').isHidden(),'a policy change clears the active move');
 assert.equal(await page.locator('[draggable="true"]').count(),0);
 await replace([a,folderA]);await move('10');await replace([folderA]);
 assert(await page.locator('#move-toolbar').isHidden(),'external deletion clears the active move');
 await replace([a,folderA]);await edit('10');await page.locator('#native-parent').selectOption('20');await replace([a]);
 await save();assert(await page.locator('#native-edit-error').isVisible(),'a removed destination cannot redirect the edit to favorites');
 await page.keyboard.press('Escape');

 await replace([{...folderA,children:[a,{...folderB,children:[b]},hidden]}]);
 await page.locator('[data-browser-bookmark="20"]').click();await page.locator('#native-delete').click();
 await page.locator('.dialog-actions [data-close]').click();assert.deepEqual(await order(),['20'],'canceling deletion keeps the folder');
 await page.locator('[data-browser-bookmark="20"]').click();await page.locator('#native-delete').click();
 await page.locator('#native-confirm-delete').click();await wait();
 assert.deepEqual(await order(),['10','21','11'],'deleting a folder retains nested folders and hidden children');
 assert.equal(await page.evaluate(()=>nativeBookmarks.locate('12').parentId),'21');

 const many=Array.from({length:205},(_,index)=>({id:String(10000+index),name:'Site '+index,url:'https://many.example/'+index}));
 await replace(many);assert.equal(await page.locator('#shortcut-grid [data-browser-bookmark]').count(),200);
 await page.locator('[data-native-add=""]').click();await page.locator('#native-title').fill('Visible new bookmark');await page.locator('#native-url').fill('https://new.example/');await save();
 assert.equal(await page.getByRole('button',{name:'Visible new bookmark',exact:true}).count(),1,'creation expands the visible range to include the saved bookmark');
 await replace([{id:'30',name:'Many',type:'folder',children:many}]);await open('30');
 assert.equal(await page.locator('#browser-folder-grid [data-browser-bookmark]').count(),200);
 await page.locator('[data-native-more="30"]').click();assert.equal(await page.locator('#browser-folder-grid [data-browser-bookmark]').count(),205);
 await page.locator('#browser-folder-back').click();await page.locator('#edit-done').click();
}

async function checkBookmarkEditing(page,wait,state,out) {
 await page.evaluate(()=>{nativeBookmarks.next=1000;nativeBookmarks.canEdit=true;nativeBookmarks.replace([
  {id:'10',name:'Первый',url:'https://first.example/'},{id:'11',name:'Второй',url:'https://second.example/'}]);});
 await page.locator('#settings-button').click();
 assert((await page.locator('#edit-shortcuts').textContent()).includes('Редактировать закладки'));
 await page.locator('#edit-shortcuts').click();
 if(out)await page.screenshot({path:resolve(out,'home-native-editing-320.png'),fullPage:true,animations:'disabled'});
 await page.locator('[data-native-add=""]').click();
 await page.locator('#native-kind').selectOption('folder');
 await page.locator('#native-title').fill('Рабочая папка');
 if(out)await page.screenshot({path:resolve(out,'home-native-add-320.png'),fullPage:true,animations:'disabled'});
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 const folder=await page.evaluate(()=>nativeBookmarks.roots.find(item=>item.name==='Рабочая папка').id);
 await page.locator('[data-browser-bookmark="'+folder+'"]').click();await page.locator('#native-open-folder').click();
 await page.locator('[data-native-add="'+folder+'"]').click();
 await page.locator('#native-title').fill('Новая закладка');await page.locator('#native-url').fill('new.example');
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 const site=await page.evaluate(id=>nativeBookmarks.locate(id).item.children[0].id,folder);
 assert.equal(await page.evaluate(id=>nativeBookmarks.locate(id).item.url,site),'https://new.example/');
 await page.locator('[data-browser-bookmark="'+site+'"]').click();await page.locator('#native-edit').click();
 await page.locator('#native-title').fill('Изменённая');await page.locator('#native-url').fill('https://changed.example/a?q=b#c');
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 assert.equal(await page.evaluate(id=>nativeBookmarks.locate(id).item.url,site),'https://changed.example/a?q=b#c');
 await page.locator('#browser-folder-edit').click();await page.locator('#native-title').fill('Переименованная папка');
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 assert.equal(await page.locator('#dialog-title').textContent(),'Переименованная папка','renaming an open folder returns to that folder');
 await page.locator('#browser-folder-back').click();
 await page.locator('[data-browser-bookmark="10"]').click();await page.locator('#native-move').click();
 await page.locator('[data-browser-bookmark="11"]').click();await wait();
 assert.deepEqual(await page.evaluate(()=>nativeBookmarks.roots.map(item=>item.id)),['11','10',folder],'tap-to-move retains the requested native order');
 await page.locator('[data-browser-bookmark="11"]').click();await page.locator('#native-move').click();
 await page.locator('[data-browser-bookmark="11"]').dragTo(page.locator('[data-browser-bookmark="10"]'));await wait();
 assert.deepEqual(await page.evaluate(()=>nativeBookmarks.roots.map(item=>item.id)),['10','11',folder],'dragging changes BookmarkModel order too');
 await page.locator('[data-browser-bookmark="10"]').click();await page.locator('#native-move').click();
 await page.locator('#move-destination').click();await page.locator('#native-parent').selectOption(folder);
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 assert.deepEqual(await page.evaluate(id=>nativeBookmarks.locate(id).item.children.map(item=>item.id),folder),[site,'10']);
 await page.locator('#browser-folder-back').click();
 await page.locator('[data-browser-bookmark="'+folder+'"]').click();await page.locator('#native-delete').click();
 assert((await page.locator('.dialog').textContent()).includes('Содержимое папки переместится на уровень выше.'));
 await page.locator('#native-confirm-delete').click();await wait();
 assert.deepEqual(await page.evaluate(()=>nativeBookmarks.roots.map(item=>item.id)),['11',site,'10'],'deleting a folder preserves its bookmarks');
 assert.deepEqual((await state()).shortcuts,[],'homepage editing must not recreate a separate local URL list');
 await page.locator('[data-browser-bookmark="11"]').click();await page.locator('#native-edit').click();
 await page.locator('#native-title').fill('Незавершённый ввод');
 await page.evaluate(()=>{nativeBookmarks.roots.find(item=>item.id==='11').name='Изменено в браузере';nativeBookmarks.replace(nativeBookmarks.roots);});
 assert.equal(await page.locator('#native-title').inputValue(),'Незавершённый ввод');
 await page.locator('#native-bookmark-form button[type=submit]').click();await wait();
 assert(await page.locator('#native-edit-error').isVisible());
 assert.equal(await page.evaluate(()=>nativeBookmarks.roots.find(item=>item.id==='11').name),'Изменено в браузере','a stale editor cannot overwrite a native change');
 await page.locator('.dialog-header [data-close]').click();
 await page.evaluate(()=>{nativeBookmarks.canEdit=false;nativeBookmarks.replace(nativeBookmarks.roots);});
 assert.equal(await page.locator('[data-native-add]').count(),0,'read-only policies hide creation controls');
 await page.locator('[data-browser-bookmark="11"]').click();
 assert.equal(await page.locator('#native-edit').count(),0);assert(await page.locator('#native-icon').isVisible());
 await page.locator('.dialog-header [data-close]').click();
 await page.evaluate(()=>{nativeBookmarks.canEdit=true;nativeBookmarks.replace(nativeBookmarks.roots);});
 await page.locator('#edit-done').click();
}
