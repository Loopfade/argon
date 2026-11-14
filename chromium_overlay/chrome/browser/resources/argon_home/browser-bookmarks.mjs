// BookmarkModel owns URLs, names, folders and order. Local storage owns only appearance.
import {t} from './i18n.js';

export function cachedBookmarkIcon(url, scale=globalThis.devicePixelRatio??1) {
  const page=new URL(url);
  if(!['http:','https:'].includes(page.protocol))return '';
  // No allow_favicon_server_fallback: FaviconSource reads the profile cache only.
  const factor=Math.min(3,Math.max(1,Math.ceil(Number.isFinite(scale)?scale:1)));
  return 'chrome://favicon2/?size=32&scale_factor='+factor+'x&page_url='+encodeURIComponent(page.href);
}
export function bookmarkRecords(items, path = [], appearance = item => item) {
  return items.flatMap(item => item.type === 'folder'
    ? [{name:item.name, path:[...path], folder:true}, ...bookmarkRecords(item.children, [...path,item.name],appearance)]
    : [{name:item.name, url:item.url, path:[...path], ...Object.fromEntries(
      ['iconMode','icon','customIcon','legacyId'].filter(key=>appearance(item)[key]!==undefined).map(key=>[key,appearance(item)[key]]))}]);
}

export function previewBookmarkImport(roots, parsed) {
  const existing=bookmarkRecords(roots);
  const seen=new Set(existing.filter(item=>item.url).map(item=>item.url));
  const folders=new Set(existing.filter(item=>item.folder).map(item=>JSON.stringify([...item.path,item.name])));
  const originalFolders=folders.size;
  let imported=0,duplicates=0;
  for(const record of parsed.records){
    if(!record.folder){const url=new URL(record.url).href;if(seen.has(url)){duplicates++;continue;}seen.add(url);imported++;}
    const path=record.folder?[...record.path,record.name]:record.path;
    for(let i=1;i<=path.length;i++)folders.add(JSON.stringify(path.slice(0,i)));
  }
  return {imported,duplicates,skipped:parsed.skipped,folders:folders.size-originalFolders};
}

export function bookmarkMetadata(shortcuts) {
  const result=new Map();
  for(const item of shortcuts) {
    if(item.type==='folder') {
      for(const [url,value] of bookmarkMetadata(item.children))if(!result.has(url))result.set(url,value);
    } else {
      try {const url=new URL(item.url).href;if(!result.has(url))result.set(url,item);}catch{}
    }
  }
  return result;
}

function validateSnapshot(value) {
  if(!value || !Number.isSafeInteger(value.revision) || value.revision<0
      || typeof value.available!=='boolean' || typeof value.ready!=='boolean'
      || typeof value.canEdit!=='boolean' || !Array.isArray(value.roots))return null;
  const ids=new Set();
  function node(item,depth) {
    if(!item || depth>64 || ids.size>=20000 || typeof item.id!=='string'
        || !/^\d+$/.test(item.id) || ids.has(item.id) || typeof item.name!=='string')throw Error();
    ids.add(item.id);
    const result={id:item.id,name:item.name};
    if(typeof item.parentId==='string'&&/^\d+$/.test(item.parentId))result.parentId=item.parentId;
    if(Number.isSafeInteger(item.index)&&item.index>=0)result.index=item.index;
    if(typeof item.editable==='boolean')result.editable=item.editable;
    if(typeof item.canAdd==='boolean')result.canAdd=item.canAdd;
    if(item.type==='folder') {
      if(!Array.isArray(item.children))throw Error();
      result.type='folder';result.children=item.children.map(child=>node(child,depth+1));
    } else {
      const url=new URL(item.url);
      if(!['http:','https:'].includes(url.protocol))throw Error();
      result.url=url.href;
      if(result.url!==item.url)result.rawUrl=item.url;
      if(!result.name.trim()){result.rawName=item.name;result.name=url.hostname;}
    }
    return result;
  }
  try {return {...value,roots:value.roots.map(item=>node(item,0)),truncated:value.truncated===true};}
  catch{return null;}
}

export class BrowserBookmarks {
  constructor(bindings,onState) {
    this.bindings=bindings;this.onState=onState;this.state=null;this.revision=-1;
    bindings.addWebUiListener('argon-bookmarks-changed',value=>this.update(value));
  }
  get available(){return this.state?.available===true;}
  get ready(){return this.state?.ready===true;}
  update(value) {
    const next=validateSnapshot(value);
    if(!next || next.revision<this.revision)return false;
    this.state=next;this.revision=next.revision;this.onState(next);return true;
  }
  async refresh() {
    const next=await this.bindings.sendWithPromise('getArgonBookmarks');
    if(!validateSnapshot(next))throw Error('Invalid bookmark snapshot');
    this.update(next); // An older, valid reply must not overwrite a newer event.
    return this.ready;
  }
  async import(records,{restore=false}={}) {
    if(!this.ready || !this.state.canEdit)throw Error(t("Изменение закладок запрещено настройками браузера."));
    let next;
    try {next=await this.bindings.sendWithPromise('importArgonBookmarks',records,...(restore?['restore']:[]));}
    catch{throw Error(t("Не удалось сохранить закладки браузера."));}
    if(!validateSnapshot(next))throw Error('Invalid bookmark snapshot');
    this.update(next);
    return this.state;
  }
  async openManager(folderId='') {
    const error=()=>new Error(t("Не удалось открыть закладки браузера."));
    if(!this.available || typeof folderId!=='string'
        || (folderId!=='' && this.find(folderId)?.type!=='folder'))throw error();
    try {
      if(await this.bindings.sendWithPromise('openArgonBookmarkManager',folderId)!==true)throw error();
    } catch {throw error();}
  }
  editable(item) {return this.state?.canEdit===true && item?.editable===true;}
  canAdd(parentId='') {
    const id=parentId||this.state?.defaultParentId;
    return this.ready && this.state.canEdit && typeof id==='string' && /^\d+$/.test(id)
      && (id===this.state.defaultParentId || this.find(id)?.canAdd===true);
  }
  children(parentId='') {
    const id=parentId||this.state?.defaultParentId;
    return id===this.state?.defaultParentId
      ? (this.state?.roots??[]).filter(item=>item.parentId===id)
      : this.find(id)?.children??[];
  }
  folders(excludeId=null) {
    const result=[];
    const visit=(items,path=[])=>items.forEach(item=>{
      if(item.id===excludeId || item.type!=='folder')return;
      const next=[...path,item.name];
      if(this.canAdd(item.id))result.push({id:item.id,name:next.join(' / ')});
      visit(item.children,next);
    });
    visit(this.state?.roots??[]);return result;
  }
  pathTo(id,items=this.state?.roots??[],path=[]) {
    for(const item of items){const next=[...path,item.id];if(item.id===id)return next;
      if(item.children){const found=this.pathTo(id,item.children,next);if(found.length)return found;}}
    return [];
  }
  expected(item) {
    return {name:item.rawName??item.name,parentId:item.parentId,index:item.index,...(item.url?{url:item.rawUrl??item.url}:{})};
  }
  async edit(operation) {
    if(!this.ready || !this.state.canEdit)throw Error(t("Изменение закладок запрещено настройками браузера."));
    const fail=()=>Error(t("Закладки изменились или не удалось сохранить изменения. Откройте редактор заново."));
    if(operation.action!=='create') {
      const current=this.find(operation.id);
      if(!this.editable(current) || JSON.stringify(this.expected(current))!==JSON.stringify(operation.expected))throw fail();
    }
    if(operation.action!=='remove'&&!this.canAdd(operation.parentId))throw fail();
    let result;try{result=await this.bindings.sendWithPromise('editArgonBookmarks',operation);}catch{throw fail();}
    if(!result || !validateSnapshot(result.snapshot) || typeof result.id!=='string' || !/^\d+$/.test(result.id))throw fail();
    this.update(result.snapshot);return result.id;
  }
  find(id,items=this.state?.roots??[]) {
    for(const item of items){if(item.id===id)return item;if(item.children){const found=this.find(id,item.children);if(found)return found;}}
    return null;
  }
}

export async function connectBrowserBookmarks(onState,bindings=null) {
  if(!bindings) {
    if(location.protocol!=='chrome:' || typeof globalThis.chrome?.send!=='function')return null;
    const {addWebUiListener,sendWithPromise}=await import('chrome://resources/js/cr.js');
    bindings={addWebUiListener,sendWithPromise};
  }
  const bookmarks=new BrowserBookmarks(bindings,onState);
  await bookmarks.refresh();
  return bookmarks;
}
