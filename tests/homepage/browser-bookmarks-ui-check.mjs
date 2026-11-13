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
   snapshot(){const nodes=(items,parentId='0')=>items.map((item,index)=>({...item,parentId,index,editable:item.editable!==false,canAdd:item.type==='folder'&&item.canAdd!==false,...(item.children?{children:nodes(item.children,item.id)}:{})}));return structuredClone({available:true,ready:true,canEdit:this.canEdit,revision:this.revision,roots:nodes(this.roots),defaultParentId:'0',truncated:false});},
   replace(roots){this.roots=structuredClone(roots);this.revision++;this.publish();},
   all(items=this.roots){return items.flatMap(item=>[item,...(item.children?this.all(item.children):[])]);},
   locate(id,items=this.roots){for(let index=0;index<items.length;index++){const item=items[index];if(item.id===id)return {item,index,items};if(item.children){const found=this.locate(id,item.children);if(found)return found;}}},
   edit(operation){
    if(!this.canEdit||this.fail)throw Error('native write failure');
    const source=operation.id&&this.locate(operation.id);
    if(operation.action!=='create'&&(!source||source.item.editable===false))throw Error('not editable');
    const parent=operation.parentId&&operation.parentId!=='0'?this.locate(operation.parentId)?.item:null;
    const target=parent?parent.children:this.roots;
    if(parent&&parent.canAdd===false)throw Error('not writable');
    let item=source?.item;
    if(operation.action==='create')item={id:String(this.next++),name:operation.name,...(operation.folder?{type:'folder',children:[]}:{url:operation.url})};
    if(operation.action==='remove')source.items.splice(source.index,1,...(item.children??[]));
    else {
     if(operation.action==='update'){item.name=operation.name;if(!operation.folder)item.url=operation.url;}
     if(source)source.items.splice(source.index,1);
     const index=operation.beforeId?target.findIndex(entry=>entry.id===operation.beforeId):target.length;
     if(index<0)throw Error('missing anchor');target.splice(index,0,item);
    }
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
  await checkBookmarkEditing(page,wait,state);
  await page.evaluate(()=>nativeBookmarks.replace([]));
  await page.getByText('Нет закладок',{exact:true}).waitFor();
  await page.reload();await page.getByText('Нет закладок',{exact:true}).waitFor();
  assert.equal((await state()).shortcuts.length,0);
  if(out)await page.screenshot({path:resolve(out,'home-native-bookmarks-320.png'),fullPage:true});
  assert(imports>0);
  console.log('PASS: native bookmarks, homepage creation/editing, touch-friendly ordering, drag, folders, safe deletion, migration and backup');
 } finally {await context.close();}
}

async function checkBookmarkEditing(page,wait,state) {
 await page.evaluate(()=>{nativeBookmarks.next=1000;nativeBookmarks.canEdit=true;nativeBookmarks.replace([
  {id:'10',name:'Первый',url:'https://first.example/'},{id:'11',name:'Второй',url:'https://second.example/'}]);});
 await page.locator('#settings-button').click();
 assert((await page.locator('#edit-shortcuts').textContent()).includes('Редактировать закладки'));
 await page.locator('#edit-shortcuts').click();
 await page.locator('[data-native-add=""]').click();
 await page.locator('#native-kind').selectOption('folder');
 await page.locator('#native-title').fill('Рабочая папка');
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
 await page.locator('.dialog [data-close]').click();
 await page.evaluate(()=>{nativeBookmarks.canEdit=false;nativeBookmarks.replace(nativeBookmarks.roots);});
 assert.equal(await page.locator('[data-native-add]').count(),0,'read-only policies hide creation controls');
 await page.locator('[data-browser-bookmark="11"]').click();
 assert.equal(await page.locator('#native-edit').count(),0);assert(await page.locator('#native-icon').isVisible());
 await page.locator('.dialog [data-close]').click();
 await page.evaluate(()=>{nativeBookmarks.canEdit=true;nativeBookmarks.replace(nativeBookmarks.roots);});
 await page.locator('#edit-done').click();
}
