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
   snapshot(){return structuredClone({available:true,ready:true,canEdit:this.canEdit,revision:this.revision,roots:this.roots,truncated:false});},
   replace(roots){this.roots=structuredClone(roots);this.revision++;this.publish();},
   all(items=this.roots){return items.flatMap(item=>[item,...(item.children?this.all(item.children):[])]);},
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
 const bindings={addWebUiListener:(name,callback)=>listeners.set(name,callback),
   sendWithPromise:async(name,value,mode)=>name==='getArgonBookmarks'?backend.snapshot():backend.add(value,mode)};
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
  assert.equal(await page.locator('#browser-bookmarks-manager').getAttribute('href'),'chrome-native://bookmarks/');
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
  await page.locator('#browser-folder-back').click();
  assert.equal(await page.locator('#dialog-title').textContent(),'Глубоко');
  await page.locator('#browser-folder-back').click();
  assert.equal(await page.locator('#modal-root').innerHTML(),'');
  await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();
  await page.locator('[data-browser-bookmark="1"]').click();
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
  await page.evaluate(()=>nativeBookmarks.replace([]));
  await page.getByText('Нет закладок',{exact:true}).waitFor();
  await page.reload();await page.getByText('Нет закладок',{exact:true}).waitFor();
  assert.equal((await state()).shortcuts.length,0);
  if(out)await page.screenshot({path:resolve(out,'home-native-bookmarks-320.png'),fullPage:true});
  assert(imports>0);
  console.log('PASS: native bookmarks, safe migration, live changes, nested folders, custom icons, portable backup and scale validation');
 } finally {await context.close();}
}
