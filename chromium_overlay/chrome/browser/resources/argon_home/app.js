import {t, language, localizePage} from './i18n.js';
import {MAX_DUMP_BYTES, HomepageStore, createDump, parseDump,
  dumpStats, shortcutList, moveShortcut, navigationTarget, mergeBookmarks, restoreState, shortcutDraftFromHash} from './state.js';
import {imageSource, faviconSources, engineIcon} from './icons.js';
import {MAX_BOOKMARK_BYTES, parseBookmarks} from './bookmarks.js';
import {connectBrowserSearch} from './browser-search.js';
import {connectBrowserBookmarks, bookmarkRecords, bookmarkMetadata, previewBookmarkImport, cachedBookmarkIcon} from './browser-bookmarks.js';
import {discoverSiteIcons} from './site-icons.js';
import {loadBingWallpapers,downloadBingWallpaper,prepareBackground,backgroundBlob} from './backgrounds.js';

(() => {
  'use strict';
  localizePage();
  // The only HTML sink. Every variable text/URL goes through esc(); imported
  // icon identifiers and URLs are validated by state.js before rendering.
  const htmlPolicy = globalThis.trustedTypes?.createPolicy('argon-home', {createHTML:value=>value});
  function setHtml(node, markup) { node.innerHTML=htmlPolicy ? htmlPolicy.createHTML(markup) : markup; }
  const $ = id => document.getElementById(id);
  const svg = (id, cls='') => '<svg' + (cls ? ' class="'+cls+'"' : '') + ' aria-hidden="true"><use href="#i-'+id+'"/></svg>';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid = () => globalThis.crypto?.randomUUID?.() || 'id-'+Date.now()+'-'+Math.random().toString(36).slice(2);
  const storageKey = 'argon-start-v1';
  const storage = {getItem:key=>localStorage.getItem(key), setItem:(key,value)=>localStorage.setItem(key,value)};
  const store = new HomepageStore(storage, storageKey);
  let state = store.state;
  let modalKind=null, modalBack=null, currentFolder=null, returnFocus=null, toastTimer, drag=null, touchDrag=null, suppressClick=false, moving=null;
  let syncModalState=()=>{};
  const editing = () => store.editing;
  const homeTheme = globalThis.argonHomeTheme;
  let browserSearch=null, browserSearchState=null, selectingEngine=false, searchBoundsFrame=0;
  let connectingSearch=null, nativeSearchRetries=0;
  let browserBookmarks=null, browserBookmarksState=null, connectingBookmarks=null, migratingBookmarks=null;
  let browserFolderPath=[];
  const bookmarkVisibleLimits=new Map();
  let metadataState=null, metadataByUrl=new Map(), metadataById=new Map();
  const nativeBookmarks = () => browserBookmarksState?.available === true;
  let initialThemeReady=false, startupFinished=false;
  let backgroundReady=Promise.resolve(),backgroundVersion=0,currentBackgroundImage;
  const nativeSearch = () => browserSearchState?.available === true;
  const originalSearchInput=$('search-input').cloneNode(true);
  const builtins = [
    {id:'google',name:'Google',icon:'google',url:'https://www.google.com/search?q={query}'},
    {id:'bing',name:'Bing',url:'https://www.bing.com/search?q={query}'},
    {id:'duck',name:'DuckDuckGo',icon:'duck',url:'https://duckduckgo.com/?q={query}'},
    {id:'noai',name:'DuckDuckGo NoAI',short:'DDG NoAI',icon:'duck',url:'https://noai.duckduckgo.com/?q={query}'}
  ];
  const pageEngines = () => [...builtins,...state.customEngines];
  function selectedEngine() {
    if(!nativeSearch())return pageEngines().find(e=>e.id===state.engine)||builtins[0];
    const engine={...browserSearchState.engine};
    if(!engine.name)engine.name=t("Поиск отключён");
    // Chromium may classify a custom Google/Bing URL as a built-in provider.
    // An exact template match must still identify the user's editable entry.
    const matching=state.customEngines.find(item=>{
      try{return new URL(item.url.replaceAll('{query}','example')).href===new URL(engine.url).href;}catch{return false;}
    });
    if(matching)engine.id=matching.id;
    return engine;
  }
  function engines() {
    if(!nativeSearch())return pageEngines();
    const current=selectedEngine();
    return [current,...pageEngines().filter(engine=>engine.id!==current.id)];
  }
  function reportSearchBounds() {
    // The tablet toolbar owns the visible omnibox; it has no WebUI fakebox.
    if(browserSearch?.available&&!browserSearch.tablet)
      browserSearch.bounds($('search-form').getBoundingClientRect(),window.scrollY);
  }
  function scheduleSearchBounds() {
    if(searchBoundsFrame)return;
    searchBoundsFrame=requestAnimationFrame(()=>{searchBoundsFrame=0;reportSearchBounds();});
  }
  function reportHomeFrame() {
    reportSearchBounds();
    globalThis.argonHomeStartup.finish(()=>browserSearch?.bindings.send('argonHomeReady',[]));
  }
  function finishStartup() {
    if(startupFinished)return;
    startupFinished=true;
    reportHomeFrame();
  }
  async function focusSearch(text = '') {
    if(!nativeSearch()){$('search-input').focus();return;}
    closeEngine();
    try{
      await browserSearch.refresh();
      if(!nativeSearch()){$('search-input').focus();return;}
      reportSearchBounds();
      if(!await browserSearch.focus(text))throw Error();
    }
    catch{notify(t("Не удалось открыть адресную строку. Повторите нажатие."));}
  }
  function applyBrowserSearch(value) {
    browserSearchState=value;
    document.documentElement.classList.toggle('native-tablet-search',value.tablet===true);
    if(value.available)nativeSearchRetries=0;
    if(value.available&&$('search-input').tagName==='INPUT') {
      const draft=$('search-input').value, editingSearch=document.activeElement===$('search-input');
      const button=document.createElement('button');
      button.id='search-input';button.type='button';button.className='search-input native-search-box';
      $('search-input').replaceWith(button);button.addEventListener('click',()=>void focusSearch());
      document.documentElement.classList.add('browser-search-ready');
      if(draft||editingSearch)requestAnimationFrame(()=>void focusSearch(draft));
    } else if(!value.available&&$('search-input').tagName==='BUTTON') {
      $('search-input').replaceWith(originalSearchInput.cloneNode(true));
      document.documentElement.classList.remove('browser-search-ready');
      $('search-form').style.opacity='';$('search-form').inert=false;
    }
    if(value.available) {
      const label=browserSearchState.enabled
        ? t("Запрос в {name} или URL",{name:selectedEngine().name}) : t("Введите адрес сайта");
      $('search-input').textContent=label;$('search-input').setAttribute('aria-label',label);
      scheduleSearchBounds();
    }
    renderEngine();
    if(initialThemeReady&&value.available)finishStartup();
  }
  const connectSearch = async () => {
    if(connectingSearch)return connectingSearch;
    connectingSearch=(async()=>{
      const reconnecting=startupFinished;
      try {
        if(browserSearch)await browserSearch.refresh();
        else browserSearch=await connectBrowserSearch(applyBrowserSearch,(alpha,focused)=>{
          if(!nativeSearch())return;
          $('search-form').style.opacity=String(focused?0:alpha);
          $('search-form').inert=focused;
        });
        scheduleSearchBounds();
        // A restored document may acquire a new native bridge without running
        // this module again. Acknowledge its current frame without hiding it.
        if(reconnecting&&browserSearch?.available)reportHomeFrame();
        if(browserSearch&&!browserSearch.available&&!document.hidden&&nativeSearchRetries++<20)
          setTimeout(()=>void connectSearch(),250);
      }catch(error){console.warn('Argon search:',error);}
    })();
    try{await connectingSearch;}finally{connectingSearch=null;}
  };
  window.addEventListener('pageshow',()=>void connectSearch());
  window.addEventListener('pageshow',()=>void connectBookmarks());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void connectSearch();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void connectBookmarks();});
  window.addEventListener('scroll',scheduleSearchBounds,{passive:true});
  window.addEventListener('resize',scheduleSearchBounds);
  new ResizeObserver(scheduleSearchBounds).observe($('search-form'));
  function notify(message, undo) {
    const toast=$('toast'); toast.replaceChildren(document.createTextNode(message));
    if (undo) { const b=document.createElement('button'); b.type='button'; b.textContent=t("Отменить"); b.addEventListener('click',undo); toast.append(b); }
    toast.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{toast.hidden=true;},undo?7000:3200);
  }
  let cancelIconSearch = () => {};
  let cancelBackgroundRequest = () => {};
  let writing = 0, blockingWrites = 0;
  const pendingWrites = new Set();
  let deferredStorageSync = false;
  function syncStoredState() {
    try {
      state=store.state=store.latest();
      store.loadError=null;
      if(moving){try{if(!findItem(moving.id,moving.parent))cancelMove();}catch{cancelMove();}}
      refresh();
      syncModalState();
    } catch (error) {
      store.loadError=error;
      notify(t("Не удалось прочитать настройки из другой вкладки."));
    }
  }
  async function writeState(operation, block=true) {
    writing++;
    if(block){blockingWrites++;document.body.inert=true;}
    const task=store.exclusive(operation);
    pendingWrites.add(task);
    try {
      return await task;
    } finally {
      pendingWrites.delete(task);writing--;
      if(block){blockingWrites--;if(!blockingWrites)document.body.inert=false;}
      if (!writing&&deferredStorageSync) {
        deferredStorageSync = false;
        syncStoredState();
      }
    }
  }
  async function commit(change, layout=false, errorId=null, block=true) {
    try { await writeState(() => { state=store.update(change,{shortcuts:layout}); },block); return true; }
    catch (error) { const target=errorId && $(errorId); if(target){target.textContent=error.message||t("Не удалось сохранить изменения.");target.hidden=false;}else notify(error.message||t("Не удалось сохранить изменения.")); return false; }
  }
  function applyTheme() {
    homeTheme.apply(state.theme);
    document.documentElement.dataset.density=state.shortcutScaleMode==='manual'?'manual':state.density==='compact'?'compact':'comfortable';
    document.documentElement.style.setProperty('--shortcut-scale',String((state.shortcutScale??100)/100));
    document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===state.theme)));
    const themes={light:['sun',t("Светлая")],dark:['moon',t("Тёмная")],amoled:['amoled',t("Тёмная AMOLED")],system:['monitor',t("Как в браузере")]};
    $('theme-trigger-icon').setAttribute('href','#i-'+themes[state.theme][0]);
    $('theme-trigger').title=$('theme-trigger').ariaLabel=t("Тема: {name}",{name:themes[state.theme][1]});
    applyBackground();
    updateThemeHelp();
  }
  function applyBackground() {
    const background=state.background,layer=$('home-background'),credit=$('background-credit');
    credit.hidden=background?.source!=='bing';
    credit.textContent=background?.source==='bing'?background.credit:'';
    if(background?.source==='bing')credit.href=background.creditUrl;else credit.removeAttribute('href');
    if(currentBackgroundImage===background?.image)return;
    currentBackgroundImage=background?.image;const version=++backgroundVersion;
    if(!background){layer.hidden=true;layer.style.backgroundImage='';document.body.classList.remove('has-background');backgroundReady=Promise.resolve();return;}
    const image=new Image();image.src=background.image;
    backgroundReady=image.decode().then(()=>{
      if(version!==backgroundVersion)return;
      layer.style.backgroundImage='url('+JSON.stringify(background.image)+')';layer.hidden=false;document.body.classList.add('has-background');
    }).catch(()=>{
      if(version!==backgroundVersion)return;
      layer.hidden=true;document.body.classList.remove('has-background');notify(t("Не удалось показать фон. Выберите другое изображение в настройках."));
    });
  }
  const connectTheme = () => homeTheme.connect().catch(error => console.warn('Argon theme:', error));
  const initialTheme=connectTheme();
  window.addEventListener('pageshow', event => { if (event.persisted) void connectTheme(); });
  function closeThemeMenu(focus=false) {$('theme-menu').hidden=true;$('theme-trigger').setAttribute('aria-expanded','false');if(focus)$('theme-trigger').focus();}
  function openThemeMenu() {$('theme-menu').hidden=false;$('theme-trigger').setAttribute('aria-expanded','true');$('theme-menu').querySelector('[aria-pressed=true]')?.focus();}
  $('theme-trigger').addEventListener('click',()=>{$('theme-menu').hidden?openThemeMenu():closeThemeMenu(true);});
  $('theme-trigger').addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openThemeMenu();}});
  $('theme-menu').addEventListener('keydown',event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;event.preventDefault();
    const options=[...$('theme-menu').querySelectorAll('button')],index=options.indexOf(document.activeElement);
    options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:-1)+options.length)%options.length].focus();
  });
  $('theme-trigger').parentElement.addEventListener('focusout',event=>{if(!$('theme-trigger').parentElement.contains(event.relatedTarget))closeThemeMenu();});
  document.addEventListener('click',async event=>{
    if(!event.target.closest('.appearance'))closeThemeMenu();
    const button=event.target.closest('[data-theme-choice]');
    if(button&&await commit(next=>{next.theme=button.dataset.themeChoice;})){applyTheme();if(button.closest('#theme-menu'))closeThemeMenu(true);}
  });
  function updateEditing() {
    if(nativeBookmarks())$('editing-hint').textContent=browserBookmarksState.canEdit?t("Нажмите на закладку, чтобы изменить, переместить или удалить её."):t("Нажмите на закладку, чтобы изменить её иконку.");
    let movingItem;try{movingItem=moving&&findItem(moving.id,moving.parent);}catch{}
    if(moving&&(!movingItem||(nativeBookmarks()&&!browserBookmarks.editable(movingItem))))moving=null;
    document.body.classList.toggle('editing',editing());
    $('edit-done').hidden=!editing();
    $('editing-hint').hidden=!editing()||!!moving;
    $('move-toolbar').hidden=!moving;
    $('move-title').textContent=moving?t("Переместить «")+movingItem.name+'»':'';
    $('move-destination').hidden=!moving||(!nativeBookmarks()&&movingItem.type==='folder');
    $('move-hint').textContent=nativeBookmarks()?t("Нажмите новое место, перетащите закладку или выберите папку."):movingItem?.type==='folder'
      ?t("Нажмите новое место или перетащите папку. Папки располагаются только в избранном.")
      :t("Нажмите новое место или перетащите ярлык. На папку — перенести внутрь.");
  }
  function setEditing(enabled) {
    store.setEditing(enabled);moving=null;touchDrag=null;clearDrag();
    if(!enabled)$('toast').hidden=true;
    updateEditing();renderShortcuts();
  }
  $('edit-done').addEventListener('click',()=>setEditing(false));
  $('move-cancel').addEventListener('click',cancelMove);
  $('move-destination').addEventListener('click',openMoveDestination);
  const validIcon = value => typeof value==='string' && /^(data:image\/(png|jpeg|webp|gif|svg\+xml);base64,|https?:\/\/)/i.test(value);
  function imageMarkup(source,fallback='',eager=false) {
    return '<img src="'+esc(source)+'" data-icon-fallback="'+esc(fallback)+'" class="favicon" loading="'+(eager?'eager':'lazy')+'" alt="" draggable="false">';
  }
  function iconMarkup(item,eager=false,native=false) {
    if(item.iconMode!=='favicon'&&item.customIcon&&validIcon(item.customIcon))return imageMarkup(imageSource(item.customIcon,location.protocol),'',eager);
    if(item.iconMode!=='favicon'&&item.icon)return svg(item.icon);
    if(native&&location.protocol==='chrome:')return imageMarkup(cachedBookmarkIcon(item.url),'',eager);
    const [source,fallback]=faviconSources(item.url,location.protocol);
    return source?imageMarkup(source,fallback,eager):svg('globe');
  }
  function fallbackImages(root) { root.querySelectorAll('img.favicon').forEach(img=>{
    let fallback=img.dataset.iconFallback;delete img.dataset.iconFallback;
    const fail=()=>{if(fallback){const next=fallback;fallback='';img.src=next;return;}const wrap=document.createElement('span');setHtml(wrap,svg('globe'));img.replaceWith(wrap.firstElementChild);};
    img.addEventListener('error',fail);
  }); }
  function folderMarkup(folder,native=false) {
    const children=folder.children||[];
    return children.length?'<span class="folder-stack">'+Array.from({length:4},(_,i)=>children[i]?'<span>'+(children[i].type==='folder'?svg('folder'):iconMarkup(children[i],false,native))+'</span>':'<span class="blank"></span>').join('')+'</span>':svg('folder');
  }
  function decoratedBookmark(item) {
    if(metadataState!==state){
      metadataState=state;metadataByUrl=bookmarkMetadata(state.bookmarkMigrationBackup??state.shortcuts);metadataById=new Map();
      const visit=items=>items.forEach(entry=>{metadataById.set(entry.id,entry);if(entry.children)visit(entry.children);});
      visit(state.bookmarkMigrationBackup??state.shortcuts);
    }
    let metadata=state.bookmarkIcons?.[item.id] ?? metadataByUrl.get(item.url);
    if(!metadata)return item;
    if(metadata.legacyId)metadata={...metadataById.get(metadata.legacyId),...metadata};
    return {...item,iconMode:metadata.iconMode,icon:metadata.icon,customIcon:metadata.customIcon};
  }
  function bookmarkIconsFor(roots,lookup,archive=[]) {
    const originals=bookmarkMetadata(archive),icons={};
    const byId=new Map();
    for(const item of archive)for(const entry of item.children??[item])byId.set(entry.id,entry);
    const visit=(items,path=[])=>items.forEach(item=>{
      if(item.children){visit(item.children,[...path,item.name]);return;}
      let old=lookup(item,path);if(!old||!(old.icon||old.customIcon||old.iconMode))return;
      const original=byId.get(old.legacyId)??originals.get(item.url);
      if(old.legacyId)old={...original,...old};
      icons[item.id]={iconMode:old.iconMode??'custom',...(old.icon?{icon:old.icon}:{}),
        ...(old.customIcon?(original?.customIcon===old.customIcon?{legacyId:original.id}:{customIcon:old.customIcon}):{})};
    });
    visit(roots);return icons;
  }
  function browserBookmarkMarkup(item) {
    const folder=item.type==='folder',button=folder||editing(),tag=button?'button':'a',selected=moving?.id===item.id;
    const attrs=button?'type="button"':'href="'+esc(item.url)+'"'+(state.newTab?' target="_blank" rel="noopener noreferrer"':'');
    return '<div class="shortcut-holder'+(selected?' moving':'')+'" data-holder="'+esc(item.id)+'" data-parent="'+esc(item.parentId===browserBookmarksState.defaultParentId?'':item.parentId||'')+'"><'+tag+' '+attrs+' draggable="'+selected+'" class="shortcut" data-browser-bookmark="'+esc(item.id)+'" aria-label="'+esc((folder?t("Папка "):'')+item.name)+'"><span class="shortcut-icon">'+(folder?folderMarkup({...item,children:item.children.map(decoratedBookmark)},true):iconMarkup(decoratedBookmark(item),false,true))+'</span><span class="shortcut-label">'+esc(item.name)+'</span></'+tag+'></div>';
  }
  function nativeAddMarkup(parent='') {
    return editing()&&!moving&&browserBookmarks.canAdd(parent)?'<button type="button" class="shortcut shortcut-add" data-native-add="'+esc(parent)+'" aria-label="'+t("Добавить сайт или папку")+'"><span class="shortcut-icon">'+svg('plus')+'</span><span class="shortcut-label">'+t("Добавить")+'</span></button>':'';
  }
  const bookmarkLimit=parent=>bookmarkVisibleLimits.get(parent)||200;
  function moreBookmarksMarkup(parent,total) {
    return total>bookmarkLimit(parent)?'<button type="button" class="btn btn-secondary bookmark-notice" data-native-more="'+esc(parent)+'">'+t("Показать ещё")+'</button>':'';
  }
  function ensureBookmarkVisible(id) {
    const item=browserBookmarks.find(id);if(!item)return;
    const parent=item.parentId===browserBookmarksState.defaultParentId?'':item.parentId;
    const siblings=parent?browserBookmarks.children(parent):browserBookmarksState.roots;
    const index=siblings.findIndex(entry=>entry.id===id);
    if(index>=bookmarkLimit(parent))bookmarkVisibleLimits.set(parent,Math.ceil((index+1)/200)*200);
  }
  function renderBrowserBookmarks() {
    const ready=browserBookmarksState.ready;
    const roots=browserBookmarksState.roots;
    const hint=browserBookmarksState.canEdit?t("Добавляйте сайты звёздочкой или через «Настроить → Редактировать закладки»."):t("Изменение закладок запрещено настройками браузера.");
    let content=ready?roots.slice(0,bookmarkLimit('')).map(browserBookmarkMarkup).join(''):'<p class="folder-empty">'+t("Загрузка закладок…")+'</p>';
    if(ready&&!roots.length&&(!editing()||!browserBookmarks.canAdd()))content='<div class="empty-favorites"><span class="empty-favorites-icon">'+svg('book')+'</span><span>'+t("Нет закладок")+'</span><small>'+hint+'</small></div>';
    if(ready)content+=nativeAddMarkup();
    if(ready)content+=moreBookmarksMarkup('',roots.length);
    if(ready&&roots.length)content+='<p id="bookmark-add-hint" class="bookmark-notice">'+hint+'</p>';
    if(browserBookmarksState.truncated)content+='<p class="bookmark-notice">'+t("Остальные закладки доступны на штатном экране браузера.")+'</p>';
    if(!state.bookmarksMigrated&&state.shortcuts.length)content+='<p class="bookmark-notice">'+t("Старые ярлыки сохранены. Они появятся в закладках после переноса.")+'</p>'+state.shortcuts.map(item=>shortcutMarkup(item)).join('');
    setHtml($('shortcut-grid'),content);
    fallbackImages($('shortcut-grid'));
    let manage=$('browser-bookmarks-manager');
    if(!manage){manage=document.createElement('button');manage.type='button';manage.id='browser-bookmarks-manager';manage.className='text-button';manage.addEventListener('click',()=>void openBookmarkManager());$('favorites-heading').parentElement.append(manage);}
    manage.textContent=t("Управлять закладками");
    if(modalKind==='browser-folder')renderBrowserFolder();
  }
  async function openBookmarkManager(folderId='') {
    try {if(!browserBookmarks)throw Error();await browserBookmarks.openManager(folderId);}
    catch {notify(t("Не удалось открыть закладки браузера."));}
  }
  function openBrowserFolder(id) {
    const folder=browserBookmarks?.find(id);
    if(!folder||folder.type!=='folder')return;
    const previous=modalKind==='browser-folder'?browserFolderPath:[];
    browserFolderPath=[...previous,id];
    openModal(heading(folder.name)+'<div id="browser-folder-move" hidden></div><div id="browser-folder-grid" class="shortcut-grid"></div><div class="dialog-actions"><button class="btn btn-secondary" id="browser-folder-back" type="button">'+t("Назад")+'</button><button class="btn btn-secondary" id="browser-folder-edit" type="button" hidden>'+t("Изменить папку")+'</button><button class="btn btn-secondary" id="browser-folder-manager" type="button">'+t("Управлять закладками")+'</button></div>','browser-folder','folder-dialog');
    $('browser-folder-edit').addEventListener('click',()=>{const current=browserBookmarks.find(folder.id);if(current)openNativeEditor(current);});
    $('browser-folder-manager').addEventListener('click',()=>void openBookmarkManager(id));
    $('browser-folder-back').addEventListener('click',()=>{const path=browserFolderPath.slice(0,-1);closeModal();browserFolderPath=[];if(path.length){openBrowserFolder(path.at(-1));browserFolderPath=path;}});
    renderBrowserFolder();
  }
  function renderBrowserFolder() {
    const folder=browserBookmarks?.find(browserFolderPath.at(-1));
    if(!folder||folder.type!=='folder'){closeModal();browserFolderPath=[];return;}
    $('dialog-title').textContent=folder.name;
    const grid=$('browser-folder-grid');if(!grid)return;
    setHtml(grid,(folder.children.slice(0,bookmarkLimit(folder.id)).map(browserBookmarkMarkup).join('')+nativeAddMarkup(folder.id)||'<p class="folder-empty">'+t("В папке пока нет сайтов")+'</p>')+moreBookmarksMarkup(folder.id,folder.children.length));
    $('browser-folder-edit').hidden=!editing()||!!moving||!browserBookmarks.editable(folder);
    const toolbar=$('browser-folder-move');toolbar.hidden=!moving;
    if(moving){setHtml(toolbar,'<p class="field-help">'+t("Нажмите новое место или перетащите ярлык. На папку — перенести внутрь.")+'</p><div class="dialog-actions"><button type="button" class="btn btn-secondary" id="native-move-target">'+t("Выбрать папку")+'</button><button type="button" class="btn btn-secondary" id="native-move-cancel">'+t("Отмена")+'</button></div>');$('native-move-target').addEventListener('click',openMoveDestination);$('native-move-cancel').addEventListener('click',cancelMove);}
    fallbackImages(grid);
  }
  function returnNativeParent(parentId='') {
    closeModal();browserFolderPath=[];
    const path=browserBookmarks.pathTo(parentId);
    if(path.length){openBrowserFolder(path.at(-1));browserFolderPath=path;}
  }
  function openNativeActions(item) {
    const folder=item.type==='folder',editable=browserBookmarks.editable(item),parent=item.parentId;
    openModal(heading(item.name)+'<div class="shortcut-actions">'+
      (folder?'<button type="button" class="engine-option" id="native-open-folder">'+svg('folder')+t("Открыть папку")+'</button>':'<button type="button" class="engine-option" id="native-icon">'+svg('image')+t("Иконка закладки")+'</button>')+
      (editable?'<button type="button" class="engine-option" id="native-edit">'+svg('pencil')+t("Изменить")+'</button><button type="button" class="engine-option" id="native-move">'+svg('arrow')+t("Переместить")+'</button><button type="button" class="engine-option danger" id="native-delete">'+svg('close')+t("Удалить")+'</button>':'')+'</div>','native-actions','',()=>returnNativeParent(parent));
    $('native-open-folder')?.addEventListener('click',()=>{closeModal();browserFolderPath=[];openBrowserFolder(item.id);browserFolderPath=browserBookmarks.pathTo(item.id);});
    $('native-icon')?.addEventListener('click',()=>{openShortcutDialog(null,null,null,null,decoratedBookmark(item));modalBack=()=>returnNativeParent(parent);});
    $('native-edit')?.addEventListener('click',()=>openNativeEditor(item));
    $('native-delete')?.addEventListener('click',()=>openNativeDelete(item));
    $('native-move')?.addEventListener('click',()=>{
      closeModal();setEditing(true);moving={id:item.id,parent:parent===browserBookmarksState.defaultParentId?null:parent,expected:browserBookmarks.expected(item)};
      returnNativeParent(parent);updateEditing();renderShortcuts();
      document.querySelector('[data-holder="'+CSS.escape(item.id)+'"] [data-browser-bookmark]')?.focus();
    });
  }
  function openNativeDelete(item) {
    const expected=browserBookmarks.expected(item),parent=item.parentId;
    openModal(heading(item.type==='folder'?t("Удалить папку?"):t("Удалить закладку?"))+
      '<p class="restore-note">'+esc(item.name)+'</p><p class="field-help">'+(item.type==='folder'?t("Содержимое папки переместится на уровень выше."):t("Закладка будет удалена из браузера."))+'</p><p id="native-edit-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="btn btn-secondary" data-close>'+t("Отмена")+'</button><button type="button" class="btn btn-delete" id="native-confirm-delete">'+t("Удалить")+'</button></div>','native-remove','',()=>returnNativeParent(parent));
    $('native-confirm-delete').addEventListener('click',async()=>{
      try {await writeState(()=>browserBookmarks.edit({action:'remove',id:item.id,expected}));returnNativeParent(parent);notify(t("Изменения сохранены"));}
      catch(error){const field=$('native-edit-error');if(field){field.textContent=error.message;field.hidden=false;}}
    });
  }
  function openNativeEditor(item=null,parentId='',moveOnly=false) {
    parentId=item?.parentId??parentId;
    const origin=modalKind==='browser-folder'?browserFolderPath.at(-1):parentId,expected=item&&browserBookmarks.expected(item),defaultParent=browserBookmarksState.defaultParentId;
    if(item?!browserBookmarks.editable(item):!browserBookmarks.canAdd(parentId)){notify(t("Изменение закладок запрещено настройками браузера."));return;}
    const folders=browserBookmarks.folders(item?.id),folder=item?.type==='folder';
    openModal(heading(moveOnly?t("Переместить"):item?t("Изменить закладку"):t("Добавить сайт или папку"))+
      '<form id="native-bookmark-form">'+(!item?'<label class="field"><span class="field-label">'+t("Тип")+'</span><select id="native-kind"><option value="site">'+t("Сайт")+'</option><option value="folder">'+t("Папка")+'</option></select></label>':'')+
      '<div id="native-content"'+(moveOnly?' hidden':'')+'><label class="field"><span class="field-label">'+t("Название")+'</span><input id="native-title" maxlength="4096" value="'+esc(item?.name??'')+'"'+(moveOnly?'':' required autofocus')+'></label><label class="field" id="native-url-field"'+(folder?' hidden':'')+'><span class="field-label">'+t("Адрес сайта")+'</span><input id="native-url" maxlength="16384" value="'+esc(item?.url??'')+'" placeholder="example.com"'+(moveOnly||folder?'':' required')+'></label></div>'+
      '<label class="field"><span class="field-label">'+t("Расположение")+'</span><select id="native-parent"><option value="">'+t("Избранное")+'</option>'+folders.map(entry=>'<option value="'+esc(entry.id)+'">'+esc(entry.name)+'</option>').join('')+'</select></label><label class="field"><span class="field-label">'+t("Позиция")+'</span><select id="native-position"></select></label><p id="native-edit-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="btn btn-secondary" data-close>'+t("Отмена")+'</button><button type="submit" class="btn btn-primary">'+(moveOnly?t("Переместить"):item?t("Сохранить"):t("Добавить"))+'</button></div></form>','native-edit','',()=>{if(moveOnly)cancelMove();returnNativeParent(origin);});
    const form=$('native-bookmark-form'),parent=$('native-parent'),position=$('native-position');
    parent.value=parentId===browserBookmarksState.defaultParentId?'':parentId;
    function positions(initial=false) {
      const children=browserBookmarks.children(parent.value||defaultParent),siblings=children.filter(entry=>entry.id!==item?.id);
      setHtml(position,[...siblings.map(entry=>entry.id),''].map((id,index)=>'<option value="'+esc(id)+'">'+(index+1)+'</option>').join(''));
      const index=initial&&item?children.findIndex(entry=>entry.id===item.id):-1;
      position.value=index>=0?(siblings[index]?.id??''):'';
    }
    positions(true);parent.addEventListener('change',()=>positions());
    const initialParent=parent.value,initialPosition=position.value;
    $('native-kind')?.addEventListener('change',()=>{const folder=$('native-kind').value==='folder';$('native-url-field').hidden=folder;$('native-url').required=!folder;});
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      const operation={action:moveOnly?'move':item?'update':'create',parentId:parent.value||defaultParent,beforeId:position.value,...(item?{id:item.id,expected}:{})};
      if(item&&parent.value===initialParent&&position.value===initialPosition)operation.preservePosition=true;
      if(!moveOnly){
        operation.name=$('native-title').value.trim();operation.folder=item?folder:$('native-kind').value==='folder';
        if(!operation.name){const field=$('native-edit-error');field.textContent=t("Укажите название.");field.hidden=false;return;}
        if(!operation.folder){try{operation.url=normalizedUrl($('native-url').value.trim());}catch(error){const field=$('native-edit-error');field.textContent=error.message;field.hidden=false;return;}}
      }
      try {
        const savedId=await writeState(()=>browserBookmarks.edit(operation));ensureBookmarkVisible(savedId);
        moving=null;touchDrag=null;clearDrag();returnNativeParent(!moveOnly&&item?.type==='folder'&&origin===item.id?item.id:operation.parentId);updateEditing();renderShortcuts();notify(t("Изменения сохранены"));
      } catch(error){const field=$('native-edit-error');if(field){field.textContent=error.message;field.hidden=false;}}
    });
  }
  document.addEventListener('click',event=>{
    const more=event.target.closest('[data-native-more]');if(more){bookmarkVisibleLimits.set(more.dataset.nativeMore,bookmarkLimit(more.dataset.nativeMore)+200);renderShortcuts();return;}
    const add=event.target.closest('[data-native-add]');if(add){openNativeEditor(null,add.dataset.nativeAdd);return;}
    const target=event.target.closest('[data-browser-bookmark]');if(!target)return;
    const item=browserBookmarks?.find(target.dataset.browserBookmark);if(!item)return;
    if(suppressClick){event.preventDefault();return;}
    if(editing()){event.preventDefault();if(moving){if(moving.id===item.id)cancelMove();else void reorder(moving,{id:item.id,parent:item.parentId===browserBookmarksState.defaultParentId?null:item.parentId});}else openNativeActions(item);}
    else if(item.type==='folder'){event.preventDefault();openBrowserFolder(item.id);}
  });
  document.addEventListener('contextmenu',event=>{
    const target=event.target.closest('[data-browser-bookmark]');
    const item=target&&browserBookmarks?.find(target.dataset.browserBookmark);
    if(!item)return;
    event.preventDefault();event.stopImmediatePropagation();if(!moving)openNativeActions(item);
  });
  async function migrateBookmarks() {
    if(migratingBookmarks||!browserBookmarks?.ready)return migratingBookmarks;
    migratingBookmarks=store.exclusive(async()=>{
      const latest=store.latest();
      if(latest.bookmarksMigrated)return;
      if(latest.shortcuts.length&&!browserBookmarks.state.canEdit)return;
      const metadata=bookmarkMetadata(latest.shortcuts);
      if(latest.shortcuts.length)await browserBookmarks.import(bookmarkRecords(latest.shortcuts));
      state=store.update(next=>{
        // Keep a recovery copy: BookmarkModel persists asynchronously. It never
        // participates in rendering or automatic re-import after this migration.
        if(next.shortcuts.length)next.bookmarkMigrationBackup=next.shortcuts;
        next.bookmarkIcons={...bookmarkIconsFor(browserBookmarks.state.roots,item=>metadata.get(item.url),next.bookmarkMigrationBackup),...next.bookmarkIcons};
        next.shortcuts=[];next.bookmarksMigrated=true;
      });
    });
    try{await migratingBookmarks;renderShortcuts();}
    catch{notify(t("Не удалось перенести ярлыки. Исходные данные сохранены."));}
    finally{migratingBookmarks=null;}
  }
  async function connectBookmarks() {
    if(connectingBookmarks)return connectingBookmarks;
    connectingBookmarks=(async()=>{
      try {
        if(browserBookmarks)await browserBookmarks.refresh();
        else browserBookmarks=await connectBrowserBookmarks(value=>{
          const activating=!nativeBookmarks()&&value.available;
          browserBookmarksState=value;
          if(activating){moving=null;setEditing(false);}else {updateEditing();renderShortcuts();}
          if(value.ready&&browserBookmarks)void migrateBookmarks();
        });
        await migrateBookmarks();
      }catch{notify(t("Не удалось прочитать закладки браузера."));}
    })();
    try{await connectingBookmarks;}finally{connectingBookmarks=null;}
  }
  function shortcutMarkup(item,parent=null) {
    const folder=item.type==='folder',tag=folder||editing()?'button':'a',selected=moving?.id===item.id;
    const attrs=tag==='button'?'type="button" draggable="'+selected+'"':'href="'+esc(item.url)+'" draggable="false"'+(state.newTab?' target="_blank" rel="noopener noreferrer"':'');
    const label=moving?t("Переместить: ")+item.name:editing()?t("Действия с ярлыком ")+item.name:(folder?t("Папка "):'')+item.name;
    return '<div class="shortcut-holder'+(selected?' moving':'')+'" data-holder="'+esc(item.id)+'" data-parent="'+esc(parent||'')+'"><'+tag+' '+attrs+' class="shortcut" data-item="'+esc(item.id)+'" aria-label="'+esc(label)+'"><span class="shortcut-icon">'+(folder?folderMarkup(item):iconMarkup(item))+'</span><span class="shortcut-label">'+esc(item.name)+'</span></'+tag+'></div>';
  }
  function addMarkup(parent=null) { return editing()&&!moving?'<button type="button" class="shortcut shortcut-add" data-add data-parent="'+esc(parent||'')+t("\" aria-label=\"Добавить сайт")+(parent?t(" в папку"):t(" или папку"))+'"><span class="shortcut-icon">'+svg('plus')+t("</span><span class=\"shortcut-label\">Добавить</span></button>"):''; }
  function renderShortcuts() {
    document.body.classList.toggle('hidden-labels',!state.showLabels);
    if(nativeBookmarks()){renderBrowserBookmarks();return;}
    const positions=new Map([...document.querySelectorAll('[data-holder]')].map(el=>[el.dataset.holder,el.getBoundingClientRect()]));
    const content=state.shortcuts.map(s=>shortcutMarkup(s)).join('')+addMarkup();
    setHtml($('shortcut-grid'),content||'<div class="empty-favorites"><span class="empty-favorites-icon">'+svg('folder')+t("</span><span>Нет ярлыков</span><small>Добавьте сайты через «Настроить → Редактировать ярлыки».</small></div>"));
    document.body.classList.toggle('hidden-labels',!state.showLabels); fallbackImages($('shortcut-grid'));
    if(modalKind==='folder')renderFolderContents();
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches)document.querySelectorAll('[data-holder]').forEach(el=>{const from=positions.get(el.dataset.holder),to=el.getBoundingClientRect();if(from?.width&&typeof el.animate==='function'&&(from.x!==to.x||from.y!==to.y))el.animate([{transform:'translate('+(from.x-to.x)+'px,'+(from.y-to.y)+'px)'},{transform:'translate(0,0)'}],{duration:230,easing:'ease-out'});});
  }
  function findItem(id,parent=null) { return nativeBookmarks()?browserBookmarks?.find(id):shortcutList(state.shortcuts,parent).find(item=>item.id===id); }
  function refresh() { applyTheme();renderEngine();updateEditing();renderShortcuts(); }
  function engineMarkup(engine) {
    if(!engine.url)return svg('globe');
    const url=engine.url.replaceAll('{query}','example');
    const source=engineIcon(url,location.protocol),fallback=faviconSources(url,location.protocol)[1]||'';
    return source?imageMarkup(source,fallback,true):svg('globe');
  }
  function renderEngine() {
    const engine=selectedEngine();setHtml($('selected-engine-icon'),engineMarkup(engine));$('selected-engine-name').textContent=engine.short||engine.name;
    $('engine-trigger').title=(nativeSearch()?t("Поисковик браузера: "):t("Поисковая система: "))+engine.name;
    setHtml($('engine-menu'),'<div class="popover-title">'+(nativeSearch()?t("Поисковик браузера"):t("Поисковая система"))+'</div>'+(nativeSearch()?'<p class="popover-title">'+t("Используется и для поиска в адресной строке.")+'</p>':'')+engines().map(e=>'<button type="button" class="engine-option'+(e.id===engine.id?' active':'')+'" data-engine="'+esc(e.id)+'" aria-pressed="'+(e.id===engine.id)+'"><span class="engine-icon">'+engineMarkup(e)+'</span><span>'+esc(e.id==='noai'?'DuckDuckGo':e.name)+'</span>'+(e.id==='noai'?t("<span class=\"noai-badge\">БЕЗ ИИ</span>"):'')+(e.id===engine.id?svg('check','check'):'')+'</button>').join('')+'<div class="popover-divider"></div><button type="button" class="engine-option engine-add" id="add-engine">'+svg('plus')+t("Добавить свою</button>"));
    if(state.customEngines.length){const manage=document.createElement('button');manage.type='button';manage.id='manage-engines';manage.className='engine-option';manage.textContent=t("Управлять своими поисковиками");$('engine-menu').append(manage);}
    $('engine-trigger').disabled=selectingEngine;
    if(nativeSearch()&&!browserSearchState.canChange) {
      $('engine-menu').querySelectorAll('button').forEach(button=>{button.disabled=true;});
      const note=document.createElement('p');note.className='popover-title';
      note.textContent=t("Поисковик задан политикой или расширением.");$('engine-menu').append(note);
    }
    if($('settings-engine-name'))$('settings-engine-name').textContent=engine.name;
    if(modalKind==='search-settings')renderSearchOptions();
    fallbackImages($('selected-engine-icon'));fallbackImages($('engine-menu'));
  }
  function closeEngine() { $('engine-menu').hidden=true;$('engine-trigger').setAttribute('aria-expanded','false'); }
  $('engine-trigger').addEventListener('click',()=>{const open=$('engine-menu').hidden;$('engine-menu').hidden=!open;$('engine-trigger').setAttribute('aria-expanded',String(open));if(open)$('engine-menu').querySelector('button')?.focus();});
  async function selectEngine(engine) {
    if(engine.id===selectedEngine().id){if(modalKind==='search-settings')closeModal();closeEngine();void focusSearch();return;}
    if(!nativeSearch()) {
      if(await commit(s=>{s.engine=engine.id;})){if(modalKind==='search-settings')closeModal();renderEngine();closeEngine();void focusSearch();}
      return;
    }
    if(modalKind==='search-settings')closeModal();
    selectingEngine=true;closeEngine();renderEngine();
    try{await browserSearch.select(engine);void focusSearch();}
    catch{notify(t("Не удалось изменить поисковик браузера."));}
    finally{selectingEngine=false;renderEngine();}
  }
  $('engine-menu').addEventListener('click',e=>{const b=e.target.closest('[data-engine]');if(b&&!b.disabled){const engine=engines().find(item=>item.id===b.dataset.engine);if(engine)void selectEngine(engine);}if(e.target.closest('#add-engine'))openEngineDialog();if(e.target.closest('#manage-engines'))openManageEngines();});
  $('engine-menu').addEventListener('keydown',e=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();const list=[...$('engine-menu').querySelectorAll('button')];let index=list.indexOf(document.activeElement);index=e.key==='Home'?0:e.key==='End'?list.length-1:(index+(e.key==='ArrowDown'?1:-1)+list.length)%list.length;list[index]?.focus();});
  document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))closeEngine();});
  $('search-form').addEventListener('submit',e=>{e.preventDefault();if(nativeSearch()){void focusSearch();return;}try{const target=navigationTarget($('search-input').value,selectedEngine().url);if(target)location.assign(target);else void focusSearch();}catch{notify(t("Проверьте адрес сайта или поисковой системы."));}});

  function openModal(content,kind,extra='',back=null) {
    syncModalState=()=>{};
    cancelIconSearch();cancelIconSearch=()=>{};
    cancelBackgroundRequest();cancelBackgroundRequest=()=>{};
    closeEngine();closeThemeMenu();if(!modalKind)returnFocus=document.activeElement;modalKind=kind;modalBack=back;
    setHtml($('modal-root'),'<div class="overlay"><section class="dialog '+extra+'" role="dialog" aria-modal="true" aria-labelledby="dialog-title">'+content+'</section></div>');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=true;});
    $('modal-root').querySelector('.overlay').addEventListener('click',e=>{if(e.target.classList.contains('overlay')&&!['shortcut','engine','move-destination','native-edit'].includes(modalKind))dismissModal();});
    $('modal-root').querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',dismissModal));
    // A delayed focus can steal the cursor after the user selects another field.
    const field=$('modal-root').querySelector('[autofocus]')||$('modal-root').querySelector('button,input,select');field?.focus();
  }
  function closeModal() {
    syncModalState=()=>{};
    cancelIconSearch();cancelIconSearch=()=>{};
    cancelBackgroundRequest();cancelBackgroundRequest=()=>{};
    const cancelFolderMove=(modalKind==='folder'&&moving?.parent===currentFolder)||
      (modalKind==='browser-folder'&&moving?.parent===browserFolderPath.at(-1));
    if(cancelFolderMove){moving=null;touchDrag=null;clearDrag();}
    const focus=returnFocus;modalKind=null;modalBack=null;currentFolder=null;returnFocus=null;setHtml($('modal-root'),'');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=false;});
    if(cancelFolderMove){updateEditing();renderShortcuts();}
    if(focus?.isConnected)focus.focus();else (editing()?$('edit-done'):$('settings-button')).focus();
  }
  function dismissModal() {const back=modalBack;closeModal();back?.();}
  function returnToFolder(parent) {closeModal();if(parent&&state.shortcuts.some(item=>item.id===parent&&item.type==='folder'))openFolder(parent);}
  const closeButton = () => t("<button class=\"icon-button\" type=\"button\" data-close aria-label=\"Закрыть\">")+svg('close')+'</button>';
  const heading = title => '<div class="dialog-header"><h2 id="dialog-title">'+esc(title)+'</h2>'+closeButton()+'</div>';
  function normalizedUrl(value) {
    const explicit=/^[a-z][a-z\d+.-]*:\/\//i.test(value);
    const hostPort=/^(?:[^\s/:?#]+|\[[0-9a-f:]+\]):\d+(?:[/?#]|$)/i.test(value);
    if(!explicit&&!hostPort&&/^[a-z][a-z\d+.-]*:/i.test(value))throw Error(t("Укажите корректный адрес сайта."));
    const url=new URL(explicit?value:'https://'+value);
    if(!['http:','https:'].includes(url.protocol)||!url.hostname)throw Error(t("Укажите корректный адрес сайта."));
    return url.href;
  }
  function showError(message) { const error=$('form-error');if(error){error.textContent=message;error.hidden=false;} }
  document.addEventListener('click',e=>{
    if(suppressClick){e.preventDefault();return;}
    const add=e.target.closest('[data-add]');if(add){if(editing())openShortcutDialog(null,add.dataset.parent||null);return;}
    const holder=e.target.closest('[data-holder]');if(!holder)return;
    const id=holder.dataset.holder,parent=holder.dataset.parent||null;
    let item;try{item=findItem(id,parent);}catch{return;}if(!item)return;
    if(!e.target.closest('[data-item]'))return;
    if(editing()){e.preventDefault();if(moving){if(moving.id===id)cancelMove();else reorder(moving,{id,parent});}else openShortcutActions(item,parent);}
    else if(item.type==='folder'){e.preventDefault();openFolder(item.id);}
  });
  document.addEventListener('contextmenu',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!editing()||moving)return;e.preventDefault();try{const item=findItem(holder.dataset.holder,holder.dataset.parent||null);if(item)openShortcutActions(item,holder.dataset.parent||null);}catch{notify(t("Избранное изменилось в другой вкладке."));}});

  function openShortcutActions(item,parent=null) {
    if(!editing())return;
    openModal(heading(item.name)+'<div class="shortcut-actions">'+(item.type==='folder'?'<button type="button" class="engine-option" id="open-folder">'+svg('folder')+t("Открыть папку</button>"):'')+'<button type="button" class="engine-option" id="shortcut-move">'+svg('arrow')+t("Переместить</button><button type=\"button\" class=\"engine-option\" id=\"shortcut-edit\">")+svg('pencil')+t("Изменить</button><button type=\"button\" class=\"engine-option danger\" id=\"shortcut-delete\">")+svg('close')+t("Удалить</button></div>"),'shortcut-actions');
    modalBack=parent?()=>openFolder(parent):null;
    $('open-folder')?.addEventListener('click',()=>openFolder(item.id));
    $('shortcut-edit').addEventListener('click',()=>openShortcutDialog(item,parent));
    $('shortcut-delete').addEventListener('click',()=>{if(item.type==='folder'&&item.children.length)openDeleteFolder(item);else deleteShortcut(item.id,parent);});
    $('shortcut-move').addEventListener('click',()=>{closeModal();moving={id:item.id,parent};if(parent)openFolder(parent);updateEditing();renderShortcuts();document.querySelector('[data-holder="'+CSS.escape(item.id)+'"] [data-item]')?.focus();});
  }
  function cancelMove() {moving=null;touchDrag=null;clearDrag();updateEditing();renderShortcuts();}

  function openShortcutDialog(item,parent=null,originFolder=parent,draft=null,bookmark=null) {
    if(!editing()&&!draft&&!bookmark)return;
    const initial=bookmark||item||draft;
    const bookmarkOrigin=bookmark&&modalKind==='browser-folder'?[...browserFolderPath]:[];
    const editId=item?.id,originalParent=parent,original=item?JSON.stringify(item):null;
    let type=item?.type==='folder'?'folder':'site',draftIcon=initial?.customIcon||'',draftMode=initial?.iconMode||(initial?.customIcon?'custom':'favicon');
    let iconReadTask=null,iconReadVersion=0,iconReadFailed=false;
    function cancelIconRead() {iconReadVersion++;iconReadTask=null;iconReadFailed=false;}
    const folders=bookmark?[]:state.shortcuts.filter(s=>s.type==='folder');
    const position=item?shortcutList(state.shortcuts,parent).findIndex(s=>s.id===item.id):null;
    openModal(heading(item?t("Изменить ")+(type==='folder'?t("папку"):t("ярлык")):draft?t("Добавить на главную Argon"):t("Добавить в избранное"))+'<form id="shortcut-form">'+(!item&&!parent&&!draft?'<div class="tabs"><button type="button" class="active" data-type="site">'+svg('globe')+t("Сайт</button><button type=\"button\" data-type=\"folder\">")+svg('folder')+t("Папка</button></div>"):'')+t("<label class=\"field\"><span class=\"field-label\">Название</span><input id=\"shortcut-title\" value=\"")+esc(initial?.name||'')+t("\" maxlength=\"40\" required autofocus placeholder=\"Название ")+(type==='folder'?t("папки"):t("сайта"))+'"></label><div id="site-fields"'+(type==='folder'?' hidden':'')+t("><label class=\"field\"><span class=\"field-label\">Адрес сайта</span><input id=\"shortcut-url\" value=\"")+esc(initial?.url||'')+'" placeholder="example.com" spellcheck="false"'+(type==='site'?' required':'')+t("></label><div class=\"field\"><span class=\"field-label\">Иконка</span><div class=\"icon-choices\"><button type=\"button\" id=\"favicon-choice\" class=\"icon-choice")+(draftMode==='favicon'?' active':'')+'">'+svg('globe')+t("С сайта</button><button type=\"button\" id=\"custom-choice\" class=\"icon-choice")+(draftMode==='custom'?' active':'')+'">'+svg('image')+t("Своя</button><span id=\"icon-preview\" class=\"icon-preview\">")+(item?iconMarkup(item):svg('globe'))+'</span></div><button type="button" id="upload-button" class="upload-zone"'+(draftMode==='custom'?'':' hidden')+'>'+svg('upload')+t("<span>Загрузить изображение</span></button><input type=\"file\" id=\"icon-file\" accept=\"image/png,image/jpeg,image/webp,image/gif,image/svg+xml\" hidden><p id=\"icon-help\" class=\"field-help\">PNG, JPG, WebP, GIF или SVG, до 1 МБ.</p></div>")+(folders.length?t("<label class=\"field\"><span class=\"field-label\">Расположение</span><select id=\"shortcut-folder\"><option value=\"\">Избранное</option>")+folders.map(f=>'<option value="'+esc(f.id)+'"'+(f.id===parent?' selected':'')+'>'+esc(f.name)+'</option>').join('')+'</select></label>':'')+'</div>'+(item?t("<label class=\"field\"><span class=\"field-label\">Позиция</span><select id=\"shortcut-position\">")+shortcutList(state.shortcuts,parent).map((s,i)=>'<option value="'+i+'"'+(i===position?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></label>':'')+'<p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions">'+(item?t("<button type=\"button\" class=\"btn btn-delete\" id=\"delete-shortcut\">Удалить</button>"):'')+t("<button type=\"button\" class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">")+(item?t("Сохранить"):t("Добавить"))+'</button></div></form>','shortcut');
    modalBack=originFolder?()=>openFolder(originFolder):null;
    const form=$('shortcut-form');
    if(bookmark){
      $('dialog-title').textContent=t("Иконка закладки");
      $('shortcut-title').readOnly=true;$('shortcut-url').readOnly=true;
      form.querySelector('.tabs')?.remove();
      form.querySelector('button[type=submit]').textContent=t("Сохранить");
      $('favicon-choice').focus();
      if(bookmarkOrigin.length)modalBack=()=>{browserFolderPath=[];openBrowserFolder(bookmarkOrigin.at(-1));browserFolderPath=bookmarkOrigin;};
    }
    $('icon-preview').parentElement.insertAdjacentElement('afterend',document.createElement('div')).id='site-icon-finder';
    setHtml($('site-icon-finder'),'<button type="button" id="find-site-icons" class="upload-zone">'+svg('search')+esc(t('Найти иконки'))+'</button><section id="site-icon-results" class="site-icon-results" hidden><p id="site-icon-status" class="field-help" role="status" aria-live="polite"></p><div id="site-icon-options"></div><p id="site-icon-selection" class="field-help" aria-live="polite"></p></section>');
    $('modal-root').querySelectorAll('[data-type]').forEach(b=>b.addEventListener('click',()=>{type=b.dataset.type;$('modal-root').querySelectorAll('[data-type]').forEach(t=>t.classList.toggle('active',t===b));$('site-fields').hidden=type==='folder';$('shortcut-url').required=type==='site';$('shortcut-title').placeholder=type==='folder'?t("Название папки"):t("Название сайта");}));
    function updatePositions(initial = false) {
      const select=$('shortcut-position');if(!select)return;
      const destination=type==='folder'?null:($('shortcut-folder')?.value||null);
      const count=shortcutList(state.shortcuts,destination).filter(site=>site.id!==editId).length;
      const selected=initial&&destination===originalParent?position:count;
      setHtml(select,Array.from({length:count+1},(_,index)=>'<option value="'+index+'"'+(index===selected?' selected':'')+'>'+(index+1)+'</option>').join(''));
    }
    $('shortcut-folder')?.addEventListener('change',()=>updatePositions());
    updatePositions(true);
    let previewTimer;
    function preview() { clearTimeout(previewTimer);if($('shortcut-form')!==form)return;const p=$('icon-preview');if(draftMode==='custom')setHtml(p,draftIcon?iconMarkup({customIcon:draftIcon,iconMode:'custom'},true):(initial?.icon?svg(initial.icon):svg('image')));else{try{setHtml(p,iconMarkup({url:normalizedUrl($('shortcut-url').value.trim()),iconMode:'favicon'},true,!!bookmark));}catch{setHtml(p,svg('globe'));}}fallbackImages(p); }
    function chooseIcon(mode) { cancelIconRead();draftMode=mode;$('favicon-choice').classList.toggle('active',mode==='favicon');$('custom-choice').classList.toggle('active',mode==='custom');$('upload-button').hidden=mode!=='custom';$('icon-help').hidden=mode!=='custom';$('form-error').hidden=true;preview();updateIconSelection(); }
    const finder=$('site-icon-finder'), findButton=$('find-site-icons'), results=$('site-icon-results');
    let iconJob=null, foundIcons=[];
    const iconDescription=icon=>icon.vector?'SVG':icon.width+' × '+icon.height;
    const updateIconSelection=()=>{
      finder.querySelectorAll('[data-site-icon]').forEach(button=>button.setAttribute('aria-pressed',String(draftMode==='custom'&&foundIcons[Number(button.dataset.siteIcon)]?.data===draftIcon)));
    };
    const resetIconSearch=()=>{
      iconJob?.abort();iconJob=null;foundIcons=[];
      results.hidden=true;$('site-icon-options').replaceChildren();$('site-icon-selection').textContent='';
      findButton.disabled=false;findButton.textContent=t('Найти иконки');results.removeAttribute('aria-busy');
    };
    cancelIconSearch=resetIconSearch;
    // Decode HTML entities using an inert title containing escaped angle
    // brackets. The downloaded site's elements never enter any document.
    const decodeIconAttribute=value=>{
      const markup='<title>'+value.replaceAll('<','&lt;').replaceAll('>','&gt;')+'</title>';
      return new DOMParser().parseFromString(htmlPolicy?htmlPolicy.createHTML(markup):markup,'text/html').querySelector('title').textContent;
    };
    findButton.addEventListener('click',async()=>{
      let url;try{url=normalizedUrl($('shortcut-url').value.trim());}catch(error){showError(error.message);return;}
      resetIconSearch();const job=iconJob=new AbortController();
      results.hidden=false;results.setAttribute('aria-busy','true');findButton.disabled=true;findButton.textContent=t('Ищем иконки…');
      $('site-icon-status').textContent=t('Ищем иконки…');$('form-error').hidden=true;
      try {
        const icons=await discoverSiteIcons(url,{decodeAttribute:decodeIconAttribute,signal:job.signal});
        if(job.signal.aborted||$('shortcut-form')!==form)return;
        foundIcons=icons;
        $('site-icon-status').textContent=icons.length?t('Найдено иконок: {count}',{count:icons.length}):t('Не удалось найти иконки. Можно загрузить свою.');
        const group=vector=>{
          const entries=icons.map((icon,index)=>({icon,index})).filter(entry=>entry.icon.vector===vector);
          if(!entries.length)return '';
          return '<p class="site-icon-group">'+esc(t(vector?'Масштабируемые':'Растровые · по форме и разрешению'))+'</p><div class="site-icon-grid">'+entries.map(({icon,index})=>'<button type="button" class="site-icon-option" data-site-icon="'+index+'" aria-pressed="false" aria-label="'+esc(t('Выбрать иконку {size}',{size:iconDescription(icon)}))+'"><span class="site-icon-check" aria-hidden="true">'+svg('check')+'</span><img src="'+esc(icon.data)+'" alt=""><span>'+esc(iconDescription(icon))+'</span><small>'+esc(icon.format||(vector?'SVG':'PNG'))+'</small></button>').join('')+'</div>';
        };
        setHtml($('site-icon-options'),group(true)+group(false));updateIconSelection();
        $('site-icon-selection').textContent=icons.length?t('Выберите вариант. Он применится после сохранения ярлыка.'):'';
      } catch(error) {
        if(!job.signal.aborted&&$('shortcut-form')===form)$('site-icon-status').textContent=t('Не удалось найти иконки. Можно загрузить свою.');
      } finally {
        if(iconJob===job&&$('shortcut-form')===form){iconJob=null;findButton.disabled=false;findButton.textContent=t('Найти иконки');results.removeAttribute('aria-busy');}
      }
    });
    finder.addEventListener('click',event=>{
      const button=event.target.closest('[data-site-icon]');if(!button)return;
      const icon=foundIcons[Number(button.dataset.siteIcon)];if(!icon)return;
      draftIcon=icon.data;chooseIcon('custom');updateIconSelection();
      $('site-icon-selection').textContent=t('Выбрано: {size}. Сохраните ярлык.',{size:iconDescription(icon)});$('form-error').hidden=true;
    });
    $('shortcut-url').addEventListener('input',resetIconSearch);
    form.querySelectorAll('[data-type]').forEach(button=>button.addEventListener('click',()=>{resetIconSearch();cancelIconRead();$('form-error').hidden=true;}));
    $('icon-file').addEventListener('change',resetIconSearch);
    $('icon-help').hidden=draftMode!=='custom';
    $('favicon-choice').addEventListener('click',()=>chooseIcon('favicon'));$('custom-choice').addEventListener('click',()=>chooseIcon('custom'));
    $('shortcut-url').addEventListener('input',()=>{clearTimeout(previewTimer);previewTimer=setTimeout(preview,250);});
    $('shortcut-url').addEventListener('change',preview);$('upload-button').addEventListener('click',()=>$('icon-file').click());
    $('icon-file').addEventListener('change',()=>{
      const file=$('icon-file').files[0];$('icon-file').value='';if(!file)return;
      cancelIconRead();const version=iconReadVersion;
      if(file.size>1024*1024||!/^image\/(png|jpeg|webp|gif|svg\+xml)$/.test(file.type)){iconReadFailed=true;showError(t("Выберите изображение подходящего формата размером до 1 МБ."));return;}
      iconReadTask=(async()=>{
        try {
          const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(Error());reader.readAsDataURL(file);});
          const image=new Image();image.src=data;await image.decode();
          if(!image.naturalWidth||!image.naturalHeight)throw Error();
          if(version!==iconReadVersion||$('shortcut-form')!==form)return false;
          draftIcon=data;chooseIcon('custom');$('form-error').hidden=true;return true;
        } catch {
          if(version===iconReadVersion&&$('shortcut-form')===form){iconReadFailed=true;showError(t("Не удалось прочитать изображение. Выберите исправный файл."));}
          return false;
        } finally {if(version===iconReadVersion)iconReadTask=null;}
      })();
    });
    form.addEventListener('submit',async e=>{
      e.preventDefault();if(type==='site'&&iconReadTask&&!await iconReadTask)return;if($('shortcut-form')!==form)return;
      if(type==='site'&&iconReadFailed){showError(t("Не удалось прочитать изображение. Выберите исправный файл."));return;}
      const name=$('shortcut-title').value.trim();if(!name){showError(t("Укажите название."));return;}let url;
      if(type==='site'){try{url=normalizedUrl($('shortcut-url').value.trim());}catch(error){showError(error.message);return;}if(draftMode==='custom'&&!draftIcon&&!initial?.icon){showError(t("Загрузите иконку или выберите «С сайта»."));return;}}
      const folderSelect=$('shortcut-folder'),targetParent=type==='site'?(folderSelect?(folderSelect.value||null):parent):null;
      const desiredPosition=item?Number($('shortcut-position').value):null;
      if(bookmark) {
        if(!browserBookmarks?.find(bookmark.id)){showError(t("Закладка больше не существует."));return;}
        if(!await commit(next=>{
          const current=browserBookmarks?.find(bookmark.id);
          if(!current||current.url!==bookmark.url)throw Error(t("Закладки изменились или не удалось сохранить изменения. Откройте редактор заново."));
          next.bookmarkIcons??={};
          const previous=next.bookmarkIcons[bookmark.id];
          next.bookmarkIcons[bookmark.id]={iconMode:draftMode,...(draftMode==='custom'&&draftIcon?(previous?.legacyId&&draftIcon===bookmark.customIcon?{legacyId:previous.legacyId}:{customIcon:draftIcon}):{}),...(draftMode==='custom'&&bookmark.icon&&!draftIcon?{icon:bookmark.icon}:{})};
        },false,'form-error'))return;
        dismissModal();renderShortcuts();notify(t("Изменения сохранены"));return;
      } else if(draft) {
        try {
          await writeState(async()=>{
            if(nativeBookmarks()) {
              await browserBookmarks.import([{name,url,path:[]}]);
              const find=items=>{for(const entry of items){if(entry.url===url)return entry;if(entry.children){const found=find(entry.children);if(found)return found;}}};
              const added=find(browserBookmarks.state.roots);
              if(!added)throw Error();
              state=store.update(next=>{next.bookmarkIcons??={};next.bookmarkIcons[added.id]={iconMode:draftMode,...(draftMode==='custom'?{customIcon:draftIcon}:{})};});
            } else state=store.addShortcut({id:uid(),name,url,iconMode:draftMode,...(draftMode==='custom'?{customIcon:draftIcon}:{})},targetParent);
          });
        } catch(error) {showError(error.message||t("Не удалось сохранить изменения."));return;}
      } else if(!await commit(next=>{
        let current;
        if(editId){const from=shortcutList(next.shortcuts,originalParent),index=from.findIndex(s=>s.id===editId);if(index<0||JSON.stringify(from[index])!==original)throw Error(t("Ярлык изменился в другой вкладке. Откройте его заново."));current=from.splice(index,1)[0];}
        const result=type==='folder'?{id:editId||uid(),type:'folder',name,children:current?.children||[]}:{id:editId||uid(),name,url,iconMode:draftMode,...(draftMode==='custom'&&draftIcon?{customIcon:draftIcon}:{}),...(draftMode==='custom'&&item?.icon&&!draftIcon?{icon:item.icon}:{})};
        const to=shortcutList(next.shortcuts,targetParent);
        const index=editId?Math.max(0,Math.min(desiredPosition,to.length)):to.length;to.splice(index,0,result);
      },true,'form-error'))return;
      returnToFolder(type==='site'&&targetParent!==originalParent?targetParent:originFolder);renderShortcuts();notify(item?t("Изменения сохранены"):type==='folder'?t("Папка добавлена"):t("Сайт добавлен"));
    });
    $('delete-shortcut')?.addEventListener('click',()=>{if(item.type==='folder'&&item.children.length){openDeleteFolder(item);return;}deleteShortcut(editId,originalParent);});
    preview();
  }
  async function deleteShortcut(id,parent) {
    let previous,after;
    if(!await commit(next=>{previous=structuredClone(next.shortcuts);const list=shortcutList(next.shortcuts,parent),index=list.findIndex(s=>s.id===id);if(index<0)throw Error(t("Ярлык больше не существует."));const item=list.splice(index,1)[0];if(item.type==='folder')next.shortcuts.push(...item.children);after=JSON.stringify(next.shortcuts);},true,'form-error'))return;
    returnToFolder(parent);renderShortcuts();notify(t("Удалено. Изменения сохранены"),async()=>{if(!await commit(next=>{if(JSON.stringify(next.shortcuts)!==after)throw Error(t("После удаления избранное уже изменилось."));next.shortcuts=previous;},true))return;renderShortcuts();notify(t("Удаление отменено"));});
  }
  function openDeleteFolder(item) { openModal(heading(t("Удалить папку?"))+t("<p class=\"dialog-subtitle\">Сайты из «")+esc(item.name)+t("» переместятся в избранное.</p><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" id=\"confirm-delete\">Удалить папку</button></div>"),'delete');$('confirm-delete').addEventListener('click',()=>deleteShortcut(item.id,null)); }
  function renderFolderContents() {
    const folder=state.shortcuts.find(s=>s.id===currentFolder&&s.type==='folder');if(!folder){closeModal();return;}
    $('dialog-title').textContent=folder.name;const target=$('folder-grid');
    if(target){setHtml(target,folder.children.map(s=>shortcutMarkup(s,folder.id)).join('')+addMarkup(folder.id)+(folder.children.length||editing()?'':t("<p class=\"folder-empty\">В папке пока нет сайтов</p>")));fallbackImages(target);}
    const tools=$('folder-move-toolbar');
    if(tools){tools.hidden=!moving;setHtml(tools,moving?t("<p class=\"editing-hint\">Нажмите новое место или перетащите ярлык.</p><div class=\"dialog-actions\"><button type=\"button\" class=\"btn btn-secondary\" id=\"move-to-root\">В избранное</button><button type=\"button\" class=\"btn btn-secondary\" id=\"folder-move-destination\">Выбрать папку</button><button type=\"button\" class=\"btn btn-secondary\" id=\"cancel-folder-move\">Отмена переноса</button></div>"):'');
      $('cancel-folder-move')?.addEventListener('click',cancelMove);
      $('folder-move-destination')?.addEventListener('click',openMoveDestination);
      $('move-to-root')?.addEventListener('click',async()=>{const source=moving;if(!source||!await commit(next=>moveShortcut(next.shortcuts,source.id,source.parent,null,next.shortcuts.length),true))return;moving=null;closeModal();updateEditing();renderShortcuts();notify(t("Ярлык перемещён"));});}
    const actions=$('folder-actions');
    if(actions){actions.hidden=!editing()||!!moving;setHtml(actions,editing()&&!moving?t("<button class=\"btn btn-secondary\" id=\"edit-folder\">Изменить папку</button>"):'');$('edit-folder')?.addEventListener('click',()=>openShortcutDialog(folder,null,folder.id));}
  }
  function openFolder(id) {
    const folder=state.shortcuts.find(s=>s.id===id&&s.type==='folder');if(!folder)return;
    openModal(heading(folder.name)+'<div id="folder-move-toolbar" hidden></div><div class="shortcut-grid" id="folder-grid"></div><div class="dialog-actions" id="folder-actions" hidden></div>','folder','folder-dialog');
    currentFolder=id;renderFolderContents();
  }
  function openMoveDestination() {
    if(nativeBookmarks()){const item=browserBookmarks.find(moving?.id);if(item)openNativeEditor(item,item.parentId,true);return;}
    const source=moving;if(!source||findItem(source.id,source.parent)?.type==='folder')return;
    const folders=state.shortcuts.filter(item=>item.type==='folder');
    openModal(heading(t("Куда переместить ярлык?"))+'<form id="move-destination-form"><label class="field"><span class="field-label">'+t("Расположение")+'</span><select id="move-target"><option value="">'+t("Избранное")+'</option>'+folders.map(folder=>'<option value="'+esc(folder.id)+'"'+(folder.id===source.parent?' selected':'')+'>'+esc(folder.name)+'</option>').join('')+'</select></label><p id="move-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="btn btn-secondary" data-close>'+t("Отмена")+'</button><button type="submit" class="btn btn-primary">'+t("Переместить")+'</button></div></form>','move-destination','',()=>{cancelMove();if(source.parent)openFolder(source.parent);});
    $('move-destination-form').addEventListener('submit',async event=>{
      event.preventDefault();const target=$('move-target').value||null;
      if(!await commit(next=>moveShortcut(next.shortcuts,source.id,source.parent,target,shortcutList(next.shortcuts,target).length),true,'move-error'))return;
      moving=null;touchDrag=null;closeModal();updateEditing();renderShortcuts();if(target)openFolder(target);notify(t("Ярлык перемещён"));
    });
  }

  function clearDrag() {document.querySelectorAll('.drag-source,.drop-target').forEach(el=>el.classList.remove('drag-source','drop-target'));drag=null;}
  async function reorder(source,target) {
    if(!editing()||!moving||!source||source.id!==moving.id||!target||source.id===target.id)return;
    if(nativeBookmarks()){
      try {
        const item=browserBookmarks.find(source.id),onto=browserBookmarks.find(target.id);
        if(!item||!onto)throw Error(t("Избранное изменилось. Повторите перенос."));
        let parentId=onto.parentId,beforeId=onto.id;
        if(onto.type==='folder'){parentId=onto.id;beforeId='';}
        else {
          if(item.parentId!==onto.parentId)throw Error(t("Выберите папку в настройках ярлыка."));
          const siblings=browserBookmarks.children(parentId);
          if(siblings.indexOf(item)<siblings.indexOf(onto))beforeId=siblings[siblings.indexOf(onto)+1]?.id??'';
        }
        await writeState(()=>browserBookmarks.edit({action:'move',id:item.id,parentId,beforeId,expected:moving.expected??browserBookmarks.expected(item)}));
        ensureBookmarkVisible(item.id);
        cancelMove();notify(t("Ярлык перемещён"));
      } catch(error){cancelMove();notify(error.message);}
      return;
    }
    if(!await commit(next=>{
      const from=shortcutList(next.shortcuts,source.parent),item=from.find(s=>s.id===source.id),to=shortcutList(next.shortcuts,target.parent),onto=to.find(s=>s.id===target.id);
      if(!item||!onto)throw Error(t("Избранное изменилось. Повторите перенос."));
      if(onto.type==='folder'&&item.type!=='folder')moveShortcut(next.shortcuts,item.id,source.parent,onto.id,onto.children.length);
      else {if(source.parent!==target.parent)throw Error(t("Выберите папку в настройках ярлыка."));moveShortcut(next.shortcuts,item.id,source.parent,target.parent,to.indexOf(onto));}
    },true))return;
    moving=null;touchDrag=null;clearDrag();updateEditing();renderShortcuts();notify(t("Ярлык перемещён"));
  }
  const holderInfo=holder=>({id:holder.dataset.holder,parent:holder.dataset.parent||null});
  document.addEventListener('dragstart',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!editing()||moving?.id!==holder.dataset.holder){if(holder)e.preventDefault();return;}drag=holderInfo(holder);holder.classList.add('drag-source');e.dataTransfer?.setData('text/plain',drag.id);if(e.dataTransfer)e.dataTransfer.effectAllowed='move';});
  document.addEventListener('dragover',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!drag)return;e.preventDefault();document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'));if(holder.dataset.holder!==drag.id)holder.classList.add('drop-target');});
  document.addEventListener('drop',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!drag)return;e.preventDefault();const source=drag,target=holderInfo(holder);clearDrag();reorder(source,target);});
  document.addEventListener('dragend',clearDrag);
  document.addEventListener('pointerdown',e=>{const tile=e.target.closest('[data-holder]');if(!tile||!editing()||moving?.id!==tile.dataset.holder||e.pointerType==='mouse')return;touchDrag={...holderInfo(tile),x:e.clientX,y:e.clientY,active:false,target:null};tile.setPointerCapture?.(e.pointerId);});
  document.addEventListener('pointermove',e=>{if(!touchDrag)return;const p=touchDrag;if(!p.active&&Math.hypot(e.clientX-p.x,e.clientY-p.y)<7)return;p.active=true;const holder=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-holder]');p.target=holder?holderInfo(holder):null;document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'));if(holder&&holder.dataset.holder!==p.id)holder.classList.add('drop-target');});
  document.addEventListener('pointerup',()=>{if(!touchDrag)return;const p=touchDrag;touchDrag=null;clearDrag();if(p.active){suppressClick=true;setTimeout(()=>{suppressClick=false;},0);if(p.target)reorder(p,p.target);}});
  document.addEventListener('pointercancel',()=>{if(touchDrag){touchDrag=null;clearDrag();}});

  function renderSearchOptions() {
    const options=$('engine-menu').cloneNode(true);
    options.hidden=false;options.removeAttribute('id');options.className='';
    options.querySelector('.popover-title')?.remove();
    options.querySelectorAll('[id]').forEach(node=>{node.id='settings-'+node.id;});
    $('search-settings-options').replaceChildren(options);fallbackImages(options);
  }
  function openSearchSettings() {
    openModal(heading(t("Поисковая система"))+'<div id="search-settings-options"></div>','search-settings','',openSettings);
    renderEngine();
    $('search-settings-options').addEventListener('click',event=>{
      const button=event.target.closest('button');if(!button||button.disabled)return;
      if(button.dataset.engine){const engine=engines().find(item=>item.id===button.dataset.engine);if(engine)void selectEngine(engine);}
      else if(button.id==='settings-add-engine')openEngineDialog(null,false,openSearchSettings);
      else if(button.id==='settings-manage-engines')openManageEngines(openSearchSettings);
    });
  }
  function openEngineDialog(item=null,fromManager=false,back=null) {
    const original=item?JSON.stringify(item):null;
    openModal(heading(t("Свой поисковик"))+(nativeSearch()?'<p class="field-help">'+t("После добавления этот поисковик будет выбран для браузера.")+'</p>':'')+t("<form id=\"engine-form\"><label class=\"field\"><span class=\"field-label\">Название</span><input id=\"engine-title\" placeholder=\"Например, Brave Search\" maxlength=\"40\" required autofocus></label><label class=\"field\"><span class=\"field-label\">Адрес поиска</span><input id=\"engine-url\" placeholder=\"https://example.com/?q={query}\" required spellcheck=\"false\"><span class=\"field-help\">Вставьте {query} вместо поискового запроса.</span></label><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" type=\"button\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">Добавить и выбрать</button></div></form>"),'engine');
    returnFocus=browserSearchState?.tablet?$('settings-button'):$('engine-trigger');
    if(item){$('dialog-title').textContent=t("Изменить поисковик");$('engine-title').value=item.name;$('engine-url').value=item.url;$('engine-form').querySelector('button[type=submit]').textContent=t("Сохранить");}
    modalBack=item||fromManager?()=>openManageEngines(back):back;
    if(item&&nativeSearch())$('engine-form').previousElementSibling.textContent=t("Изменение выбранного поисковика обновит и поиск браузера.");
    $('engine-form').addEventListener('submit',async event=>{
      event.preventDefault();const form=$('engine-form'),name=$('engine-title').value.trim(),template=$('engine-url').value.trim();
      if(!name||!template.includes('{query}')){showError(t("Укажите название и {query} в адресе поиска."));return;}
      let url;try{url=searchTemplate(template);}catch{showError(t("Укажите корректный адрес поиска."));return;}
      const engine={id:item?.id||uid(),name,url},activate=!item||selectedEngine().id===item.id;
      try {
        await writeState(async()=>{
          await saveEngineChange(next=>{
            checkEngineEdit(next,item,original,url);
            if(item)next.customEngines[next.customEngines.findIndex(value=>value.id===item.id)]=engine;else next.customEngines.push(engine);
            if(activate)next.engine=engine.id;
          },activate?engine:null);
        });
      } catch(error){if(form.isConnected)showError(error.message||t("Не удалось сохранить изменения."));return;}
      renderEngine();closeModal();if(item||fromManager)openManageEngines(back);else if(back)back();notify(item?t("Изменения сохранены"):t("Поисковик добавлен"));
    });
  }
  function searchTemplate(value) {
    let marker='argonqueryplaceholder';while(value.toLowerCase().includes(marker))marker+='x';
    return normalizedUrl(value.replaceAll('{query}',marker)).replaceAll(marker,'{query}');
  }
  async function saveEngineChange(change,engine) {
    const previous=store.latest();
    // Validate and persist first: quota/validation errors must never change the
    // browser's provider. The shared lock also covers a rejected native change.
    state=store.update(change);
    if(nativeSearch()&&engine) {
      try{await browserSearch.select(engine);}
      catch {
        state=store.state=restoreState(storage,storageKey,previous);
        renderEngine();
        throw Error(t("Не удалось изменить поисковик браузера."));
      }
    }
  }
  function checkEngineEdit(latest,item,original,url) {
    if(item&&JSON.stringify(latest.customEngines.find(value=>value.id===item.id))!==original)throw Error(t("Поисковик изменился в другой вкладке. Откройте его заново."));
    if([...builtins,...latest.customEngines].some(value=>value.id!==item?.id&&searchTemplate(value.url)===url))throw Error(t("Такой адрес поиска уже есть в списке."));
  }
  function openManageEngines(back=null) {
    openModal(heading(t("Свои поисковики"))+'<p class="field-help">'+t("Удаление выбранной системы из списка переключит поиск на Google.")+'</p><div class="custom-engine-list">'+state.customEngines.map(engine=>'<article class="custom-engine-row"><div><strong>'+esc(engine.name)+'</strong><small>'+esc(engine.url)+'</small></div><div class="dialog-actions"><button type="button" class="btn btn-secondary" data-edit-engine="'+esc(engine.id)+'">'+t("Изменить")+'</button><button type="button" class="btn btn-delete" data-delete-engine="'+esc(engine.id)+'">'+t("Удалить из списка")+'</button></div></article>').join('')+'</div><p id="engines-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="btn btn-secondary" id="new-engine">'+t("Добавить свою")+'</button><button type="button" class="btn btn-primary" data-close>'+t("Готово")+'</button></div>','engines');
    returnFocus=browserSearchState?.tablet?$('settings-button'):$('engine-trigger');
    modalBack=back;
    syncModalState=()=>{const focused=document.activeElement?.dataset,edit=focused?.editEngine,remove=focused?.deleteEngine;openManageEngines(back);const id=edit||remove;if(id)$('modal-root').querySelector('['+(edit?'data-edit-engine':'data-delete-engine')+'="'+CSS.escape(id)+'"]')?.focus();};
    $('new-engine').addEventListener('click',()=>openEngineDialog(null,true,back));
    $('modal-root').querySelectorAll('[data-edit-engine]').forEach(button=>button.addEventListener('click',()=>{const engine=state.customEngines.find(value=>value.id===button.dataset.editEngine);if(engine)openEngineDialog(engine,true,back);}));
    $('modal-root').querySelectorAll('[data-delete-engine]').forEach(button=>button.addEventListener('click',async()=>{
      const id=button.dataset.deleteEngine,original=JSON.stringify(state.customEngines.find(value=>value.id===id)),active=selectedEngine().id===id;
      try {
        await writeState(async()=>{
          await saveEngineChange(next=>{if(JSON.stringify(next.customEngines.find(value=>value.id===id))!==original)throw Error(t("Поисковик изменился в другой вкладке. Откройте его заново."));next.customEngines=next.customEngines.filter(value=>value.id!==id);if(next.engine===id)next.engine='google';},active?builtins[0]:null);
        });
      } catch(error){const target=$('engines-error');if(target){target.textContent=error.message||t("Не удалось сохранить изменения.");target.hidden=false;}return;}
      renderEngine();openManageEngines(back);
    }));
  }
  function saveDownload(blob,filename) {
    const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  const downloadStamp=()=>new Date().toISOString().slice(0,19).replaceAll(':','-');
  async function exportBackup() {
    await Promise.allSettled([...pendingWrites]);
    try {
      const latest=store.latest();
      if(nativeBookmarks()) {
        await browserBookmarks.refresh();
        if(!browserBookmarks.ready||browserBookmarks.state.truncated)throw Error();
        latest.browserBookmarks=bookmarkRecords(browserBookmarks.state.roots,[],item=>state.bookmarkIcons?.[item.id]?.legacyId?state.bookmarkIcons[item.id]:decoratedBookmark(item));
      }
      const dump=createDump(latest);
      saveDownload(new Blob([JSON.stringify(dump,null,2)],{type:'application/json'}),'argon-dump-'+downloadStamp()+'.json');
      notify(t("Резервная копия подготовлена для скачивания"));
    } catch {
      backupError(t("Настройки не удалось прочитать. Сохраните исходные данные для восстановления."));
      if($('backup-recovery'))$('backup-recovery').hidden=false;
    }
  }
  function backupError(message) { const target=$('backup-error');if(target){target.textContent=message;target.hidden=false;}else notify(message); }
  async function readTransfer(event) {
    const input=event.currentTarget,file=input.files[0];input.value='';if(!file)return;
    const backup=input.id==='backup-file',button=$(backup?'backup-import':'bookmarks-import');
    const report=backup?backupError:bookmarkError;button.disabled=true;
    try {
      if(file.size>Math.min(MAX_DUMP_BYTES,MAX_BOOKMARK_BYTES))throw Error(t("Выберите файл размером до 10 МБ."));
      const source=(await file.text()).replace(/^\ufeff/,'').trim();
      if(!input.isConnected)return;
      let candidate;try{candidate=JSON.parse(source);}catch{}
      if(candidate?.app==='argon-start')openRestoreDialog(parseDump(source),file.name);
      else openBookmarkImport(parseBookmarks(source),file.name);
    } catch(error) {if(input.isConnected)report(error.message||t("Не удалось прочитать файл."));}
    finally {if(button.isConnected)button.disabled=false;}
  }
  function openRestoreDialog(dump,filename) {
    const stats=dumpStats(dump.state),date=new Intl.DateTimeFormat(language() === 'ru' ? 'ru-RU' : 'en-US',{dateStyle:'medium',timeStyle:'short'}).format(new Date(dump.createdAt));
    const rows=[[t("Файл"),filename],[t("Создан"),date],[t("Ярлыки"),stats.sites],[t("Папки"),stats.folders],[t("Загруженные иконки"),stats.icons],[t("Свои поисковики"),stats.engines]];
    openModal(heading(t("Восстановить Argon?"))+'<div class="backup-summary">'+rows.map(([label,value])=>'<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('')+'</div>'+
      '<p class="restore-note">'+(nativeBookmarks()?t("Настройки страницы будут заменены. Закладки из копии добавятся к закладкам браузера без удаления существующих."):t("Текущие ярлыки, папки, свои иконки и настройки страницы будут заменены."))+'</p>'+
      '<p class="field-help">'+t("Загруженные изображения встроены в файл JSON.")+' '+t("Активный поисковик браузера выбирается отдельно.")+'</p>'+
      '<p id="backup-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" id="cancel-restore" autofocus>'+t("Отмена")+'</button><button class="btn btn-primary" type="button" id="confirm-restore">'+t("Заменить и восстановить")+'</button></div>','restore');
    $('cancel-restore').addEventListener('click',openDataSettings);
    $('confirm-restore').addEventListener('click',async()=>{
      try {
        await writeState(async() => {
          const next=structuredClone(dump.state);
          if(nativeBookmarks()) {
            const records=next.browserBookmarks??bookmarkRecords(next.shortcuts);
            await browserBookmarks.import(records,{restore:true});
            const metadata=bookmarkMetadata(records);
            const locations=new Map(records.filter(record=>record.url).map(record=>[JSON.stringify([record.path,record.name,record.url]),record]));
            if(next.shortcuts.length)next.bookmarkMigrationBackup=next.shortcuts;
            next.bookmarkIcons=bookmarkIconsFor(browserBookmarks.state.roots,(item,path)=>locations.get(JSON.stringify([path,item.name,item.url]))??metadata.get(item.url),next.bookmarkMigrationBackup);
            next.shortcuts=[];next.bookmarksMigrated=true;delete next.browserBookmarks;
          }
          state=store.replace(next);
        });
      } catch {
        backupError(t("Не удалось сохранить резервную копию. Прежние данные остаются на месте."));
        return;
      }
      closeModal();refresh();notify(t("Резервная копия восстановлена"));
    });
  }
  function bookmarkSettingsMarkup() {
    return '<div class="backup-section"><span class="setting-label">'+t("Закладки из другого браузера")+'</span><p class="backup-description">'+t("Добавьте ссылки и папки к вашим ярлыкам.")+'</p><p class="field-help">'+t("HTML, JSON или файл Bookmarks без расширения.")+'</p><button class="btn btn-secondary" id="bookmarks-import" type="button">'+svg('upload')+t("Добавить закладки из файла")+'</button><p class="field-help">'+t("Новые ярлыки получат иконки с сайтов. Ваши ярлыки, свои иконки и настройки сохранятся.")+'</p><input type="file" id="bookmarks-file" hidden><p id="bookmarks-error" class="error" role="alert" hidden></p></div>';
  }
  function migrationSettingsMarkup() {
    if(!nativeBookmarks()||!state.bookmarkMigrationBackup?.length)return '';
    return '<div class="backup-section"><span class="setting-label">'+t("Копия прежних ярлыков")+'</span><p class="backup-description">'+t("Страховочная копия не участвует в рабочем списке. Её можно добавить в закладки вручную.")+'</p><button type="button" class="btn btn-secondary" id="migration-import">'+t("Добавить прежние ярлыки в закладки")+'</button></div>';
  }
  function bookmarkError(message) {
    const target=$('bookmarks-error');
    if(target){target.textContent=message;target.hidden=false;}else notify(message);
  }
  function openBookmarkImport(parsed,filename) {
    const preview=nativeBookmarks()?previewBookmarkImport(browserBookmarksState.roots,parsed):mergeBookmarks(store.latest(),parsed,uid);
    const rows=[[t("Файл"),filename],[t("Формат"),parsed.format],[t("Новые закладки"),preview.imported],
      [t("Новые папки"),preview.folders],[t("Повторные ссылки"),preview.duplicates],[t("Пропущенные записи"),preview.skipped]];
    let notes='<p class="restore-note">'+(nativeBookmarks()?t("Закладки добавятся в браузер. Настройки страницы и существующие закладки сохранятся."):t("Закладки добавятся к вашим ярлыкам. Настройки страницы сохранятся. Повторяющиеся адреса будут пропущены."))+'</p>';
    if(!nativeBookmarks()&&parsed.records.some(record=>record.path.length>1))notes+='<p class="field-help">'+t("Вложенные папки будут показаны отдельно с путём в названии.")+'</p>';
    if(preview.shortened)notes+='<p class="field-help">'+t("Длинные названия будут сокращены: {count}.",{count:preview.shortened})+'</p>';
    if(!preview.imported&&!preview.folders)notes+='<p class="field-help">'+t("Все эти закладки уже есть на домашней странице.")+'</p>';
    openModal(heading(t("Импортировать закладки?"))+'<div class="backup-summary">'+rows.map(([label,value])=>'<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('')+'</div>'+notes+'<p id="bookmarks-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" id="cancel-bookmarks" autofocus>'+t("Отмена")+'</button><button class="btn btn-primary" type="button" id="confirm-bookmarks"'+(preview.imported?'':' disabled')+'>'+t("Добавить закладки")+'</button></div>','bookmarks');
    $('cancel-bookmarks').addEventListener('click',openDataSettings);
    if(nativeBookmarks()&&preview.folders)$('confirm-bookmarks').disabled=false;
    if(nativeBookmarks()&&!browserBookmarksState.canEdit){$('confirm-bookmarks').disabled=true;bookmarkError(t("Изменение закладок запрещено настройками браузера."));}
    $('confirm-bookmarks').addEventListener('click',async()=>{
      try {
        const result=await writeState(async() => {
          if(nativeBookmarks()) {
            const current=previewBookmarkImport(browserBookmarks.state.roots,parsed);
            await browserBookmarks.import(parsed.records);
            const metadata=bookmarkMetadata(parsed.records);
            const icons=bookmarkIconsFor(browserBookmarks.state.roots,item=>metadata.get(item.url),state.bookmarkMigrationBackup);
            if(Object.keys(icons).length)state=store.update(next=>{next.bookmarkIcons={...icons,...next.bookmarkIcons};});
            return current;
          }
          const imported=store.importBookmarks(parsed,uid);
          state=imported.state;
          return imported;
        });
        closeModal();setEditing(false);refresh();notify(t("Добавлено закладок: {count}",{count:result.imported}));
      } catch(error) {bookmarkError(error.message||t("Не удалось сохранить закладки. Прежние данные остаются на месте."));}
    });
  }
  function openDataSettings() {
    let damaged=false;try{store.latest();}catch{damaged=true;}
    openModal(heading(t("Перенос данных"))+
      '<div class="backup-section"><span class="setting-label">'+t("Резервная копия Argon")+'</span>'+
      '<p class="backup-description">'+t("Ярлыки, папки, свои поисковики и оформление домашней страницы.")+'</p>'+
      '<div class="backup-included">'+svg('image')+'<div><strong>'+t("Свои иконки включены")+'</strong><p class="field-help">'+t("Загруженные изображения встроены в файл JSON.")+'</p></div></div>'+
      '<p class="field-help">'+t("Копия Argon .json, до 10 МБ.")+'</p>'+(damaged?'<p class="error">'+t("Настройки не удалось прочитать. Сохраните исходные данные для восстановления.")+'</p>':'')+'<div class="backup-actions">'+
      '<button class="btn btn-secondary" id="backup-export" type="button"'+(damaged?' disabled':'')+'>'+svg('download')+t("Сохранить копию")+'</button>'+
      '<button class="btn btn-secondary" id="backup-import" type="button">'+svg('upload')+t("Восстановить из копии")+'</button></div>'+
      '<p class="field-help">'+t("Восстановление заменит текущие данные страницы.")+' '+t("Активный поисковик браузера выбирается отдельно.")+'</p>'+
      '<button class="btn btn-secondary" id="backup-recovery" type="button"'+(damaged?'':' hidden')+'>'+t("Сохранить исходные данные")+'</button><input type="file" id="backup-file" accept=".json,application/json" hidden><p id="backup-error" class="error" role="alert" hidden></p></div>'+
      bookmarkSettingsMarkup()+migrationSettingsMarkup()+'<div class="dialog-actions"><button class="btn btn-secondary" id="data-back" type="button">'+t("Назад к настройкам")+'</button></div>','data');
    $('data-back').addEventListener('click',openSettings);
    $('backup-export').addEventListener('click',exportBackup);
    $('backup-recovery').addEventListener('click',()=>{
      try{const raw=storage.getItem(storageKey);if(raw===null)throw Error();saveDownload(new Blob([raw],{type:'text/plain;charset=utf-8'}),'argon-recovery-'+downloadStamp()+'.txt');notify(t("Исходные данные сохранены в файл. Это копия для восстановления, а не обычный бэкап."));}
      catch{backupError(t("Не удалось создать резервную копию."));}
    });
    $('backup-import').addEventListener('click',()=>$('backup-file').click());
    $('bookmarks-import').addEventListener('click',()=>$('bookmarks-file').click());
    $('migration-import')?.addEventListener('click',()=>openBookmarkImport({format:'Argon',records:bookmarkRecords(store.latest().bookmarkMigrationBackup??[]),skipped:0},t("Копия прежних ярлыков")));
    $('backup-file').addEventListener('change',readTransfer);
    $('bookmarks-file').addEventListener('change',readTransfer);
  }
  function updateThemeHelp() {
    const target=$('theme-help');if(!target)return;
    const descriptions={light:t("Светлое оформление домашней страницы."),dark:t("Тёмное оформление домашней страницы."),amoled:t("Чисто чёрный фон домашней страницы."),system:t("Тема меняется вместе с темой браузера.")};
    target.textContent=state.background&&state.theme==='amoled'?t("Тёмные элементы страницы поверх выбранного фона."):descriptions[state.theme];
  }
  function updateDensityHelp() {
    const manual=state.shortcutScaleMode==='manual';
    const field=$('manual-shortcut-scale');if(field)field.hidden=!manual;
    const target=$('density-help');if(target)target.textContent=state.density==='compact'
      ?t("Мелкие ярлыки расположены плотнее: в строке помещается больше сайтов.")
      :t("Крупные ярлыки легче различать: в строке помещается меньше сайтов.");
    if(manual&&target)target.textContent=t("Масштаб ярлыков: {percent}%. Количество колонок подстраивается под экран.",{percent:state.shortcutScale});
  }
  function openBackgroundSettings() {
    openModal(heading(t("Фон страницы"))+'<p class="field-help settings-autosave">'+t("Изменения сохраняются автоматически")+'</p><fieldset id="background-controls"><div class="background-preview"><img id="background-preview-image" alt="" hidden><span id="background-preview-empty">'+t("Стандартный фон темы")+'</span></div><p id="background-current" class="field-help"></p><div class="backup-actions"><button type="button" class="btn btn-secondary" id="background-upload">'+svg('upload')+t("Выбрать свою картинку")+'</button><button type="button" class="btn btn-secondary" id="background-clear">'+t("Убрать картинку")+'</button></div><input type="file" id="background-file" accept="image/png,image/jpeg,image/webp" hidden><p class="field-help">'+t("PNG, JPG или WebP, до 10 МБ.")+'</p><button type="button" class="btn btn-secondary" id="background-bing">'+t("Выбрать из Bing")+'</button><p id="background-status" class="field-help" role="status" aria-live="polite"></p><section id="background-gallery" class="bing-gallery" hidden></section></fieldset><p id="background-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button type="button" class="btn btn-secondary" id="background-back">'+t("Назад к настройкам")+'</button></div>','backgrounds');
    const screen=$('background-controls'),controller=new AbortController();let pictures=[];
    cancelBackgroundRequest=()=>controller.abort();
    const alive=()=>screen.isConnected&&!controller.signal.aborted;
    const status=$('background-status'),error=$('background-error');
    function previewBackground() {
      if(!alive())return;
      const selected=state.background,img=$('background-preview-image');img.hidden=!selected;$('background-preview-empty').hidden=!!selected;
      if(selected)img.src=selected.image;else img.removeAttribute('src');
      $('background-clear').disabled=!selected;
      $('background-current').textContent=selected?.source==='bing'?selected.credit:selected?t("Своя картинка"):t("Стандартный фон темы");
    }
    syncModalState=previewBackground;
    async function busy(operation) {
      if(screen.disabled)return;screen.disabled=true;error.hidden=true;status.textContent=t("Загружаем изображение…");
      try{await operation();if(alive())status.textContent='';}
      catch(failure){if(alive()){error.textContent=failure.message||t("Не удалось загрузить изображение.");error.hidden=false;status.textContent='';}}
      finally{if(alive())screen.disabled=false;}
    }
    $('background-upload').addEventListener('click',()=>$('background-file').click());
    $('background-file').addEventListener('change',event=>{
      const file=event.target.files[0];event.target.value='';if(!file)return;
      void busy(async()=>{
        const image=await prepareBackground(file);if(!alive())return;
        if(await commit(next=>{next.background={source:'custom',image};},false,'background-error')){refresh();previewBackground();}
      });
    });
    $('background-clear').addEventListener('click',async()=>{if(await commit(next=>{delete next.background;},false,'background-error')){refresh();previewBackground();}});
    $('background-back').addEventListener('click',openSettings);
    $('background-bing').addEventListener('click',()=>void busy(async()=>{
      status.textContent=t("Загружаем фоны Bing…");
      pictures=await loadBingWallpapers({signal:controller.signal});if(!alive())return;
      const gallery=$('background-gallery');gallery.hidden=false;
      setHtml(gallery,pictures.length?pictures.map((picture,index)=>'<article class="bing-card"><img src="'+esc(imageSource(picture.thumbnail,location.protocol))+'" alt="" loading="lazy"><p class="bing-title">'+esc(picture.title)+'</p><a class="bing-credit" href="'+esc(picture.creditUrl)+'" target="_blank" rel="noopener noreferrer">'+esc(picture.credit)+'</a><div class="bing-actions"><button type="button" class="btn btn-secondary" data-bing-set="'+index+'"'+(picture.downloadable?'':' disabled')+'>'+t("Установить")+'</button><button type="button" class="btn btn-secondary" data-bing-download="'+index+'"'+(picture.downloadable?'':' disabled')+'>'+t("Скачать")+'</button></div>'+(picture.downloadable?'':'<p class="field-help">'+t("Это изображение недоступно для скачивания.")+'</p>')+'</article>').join(''):'<p class="field-help">'+t("Не удалось загрузить фоны Bing. Повторите попытку.")+'</p>');
    }));
    $('background-gallery').addEventListener('click',event=>{
      const button=event.target.closest('[data-bing-set],[data-bing-download]');if(!button)return;
      const download=button.hasAttribute('data-bing-download'),picture=pictures[Number(download?button.dataset.bingDownload:button.dataset.bingSet)];if(!picture)return;
      void busy(async()=>{
        const image=await downloadBingWallpaper(picture,{signal:controller.signal});if(!alive())return;
        if(download){saveDownload(backgroundBlob(image),'bing-'+(picture.date||downloadStamp())+'.jpg');notify(t("Изображение подготовлено для скачивания"));}
        else if(await commit(next=>{next.background={source:'bing',image,credit:picture.credit,creditUrl:picture.creditUrl};},false,'background-error')){refresh();previewBackground();}
      });
    });
    previewBackground();
  }
  function openSettings() {
    if(moving)cancelMove();
    openModal(heading(t("Настроить страницу"))+'<p class="field-help settings-autosave">'+t("Изменения сохраняются автоматически")+'</p>'+
      '<button type="button" id="edit-shortcuts" class="setting-row edit-shortcuts"><span><strong>'+(editing()?t("Завершить редактирование"):nativeBookmarks()?(browserBookmarksState.canEdit?t("Редактировать закладки"):t("Иконки закладок")):t("Редактировать ярлыки"))+'</strong><small>'+(nativeBookmarks()?(browserBookmarksState.canEdit?t("Добавление, изменение, перемещение и удаление закладок."):t("Выберите закладку на главной, чтобы изменить её иконку.")):t("Добавление, изменение, перемещение и удаление ярлыков."))+'</small></span>'+svg('pencil')+'</button>'+
      '<button type="button" id="search-settings" class="setting-row edit-shortcuts"><span><strong>'+t("Поисковая система")+'</strong><small id="settings-engine-name">'+esc(selectedEngine().name)+'</small></span>'+svg('globe')+'</button>'+
      '<span class="setting-label">'+t("Оформление")+'</span><div class="theme-options">'+
      [['light','sun',t("Светлая")],['dark','moon',t("Тёмная")],['amoled','amoled',t("Тёмная AMOLED")],['system','monitor',t("Как в браузере")]].map(([theme,icon,name])=>'<button class="theme-card" type="button" data-theme-choice="'+theme+'" aria-pressed="'+(state.theme===theme)+'">'+svg(icon)+name+'</button>').join('')+
      '</div><p id="theme-help" class="field-help"></p>'+
      '<button type="button" id="background-settings" class="setting-row edit-shortcuts"><span><strong>'+t("Фон страницы")+'</strong><small>'+t("Своя картинка или фотографии Bing.")+'</small></span>'+svg('image')+'</button>'+
      '<label class="setting-row"><span>'+t("Размер ярлыков")+'</span><select id="density-select" aria-label="'+t("Размер ярлыков")+'" aria-describedby="density-help"><option value="comfortable">'+t("Крупные")+'</option><option value="compact">'+t("Мелкие")+'</option><option value="manual">'+t("Вручную")+'</option></select></label>'+
      '<div id="manual-shortcut-scale" hidden><label class="setting-row"><span>'+t("Масштаб ярлыков, %")+'</span><input id="shortcut-scale" type="number" inputmode="numeric" min="50" max="200" step="1" value="'+(state.shortcutScale??100)+'" aria-describedby="shortcut-scale-help shortcut-scale-error"></label><p class="field-help" id="shortcut-scale-help">'+t("От 50 до 200%. 100% — стандартный размер. Сохраняется после ввода и выхода из поля или нажатия Enter.")+'</p><p id="shortcut-scale-error" class="error" role="alert" hidden></p></div>'+
      '<div id="size-preview" class="shortcut-grid size-preview" aria-label="'+t("Предпросмотр ярлыков")+'"></div><p id="density-help" class="field-help"></p>'+
      '<div class="setting-row"><span>'+t("Показывать названия ярлыков")+'</span><button id="labels-toggle" class="toggle" role="switch" aria-label="'+t("Показывать названия ярлыков")+'" aria-checked="'+state.showLabels+'"></button></div>'+
      '<div class="setting-row"><span>'+t("Открывать сайты из ярлыков в новой вкладке")+'</span><button id="newtab-toggle" class="toggle" role="switch" aria-label="'+t("Открывать сайты из ярлыков в новой вкладке")+'" aria-checked="'+state.newTab+'"></button></div>'+
      '<button type="button" id="data-settings" class="setting-row edit-shortcuts"><span><strong>'+t("Перенос данных")+'</strong><small>'+t("Копия Argon с иконками и импорт закладок.")+'</small></span>'+svg('arrow')+'</button>'+
      '<div class="dialog-actions"><button class="btn btn-primary" data-close type="button">'+t("Готово")+'</button></div>','settings');
    $('edit-shortcuts').addEventListener('click',()=>{const enabled=!editing();closeModal();setEditing(enabled);if(enabled)$('edit-done').focus();});
    const densityValue=()=>state.shortcutScaleMode==='manual'?'manual':state.density==='compact'?'compact':'comfortable';
    $('density-select').value=densityValue();
    setHtml($('size-preview'),[{name:'Argon'},...builtins.filter(e=>['noai','google','bing'].includes(e.id))].map(e=>'<span class="shortcut"><span class="shortcut-icon">'+(e.url?engineMarkup(e):svg('brand'))+'</span><span class="shortcut-label">'+esc(e.name==='DuckDuckGo NoAI'?'DuckDuckGo':e.name)+'</span></span>').join(''));
    fallbackImages($('size-preview'));updateThemeHelp();updateDensityHelp();
    $('density-select').addEventListener('change',async e=>{
      const density=e.target.value;
      if(await commit(s=>{
        if(density==='manual'){s.shortcutScaleMode='manual';s.shortcutScale??=100;}
        else{s.density=density;if(s.shortcutScaleMode)s.shortcutScaleMode='preset';}
      })){
        scaleDirty=false;$('shortcut-scale').value=state.shortcutScale??100;
        $('shortcut-scale-error').hidden=true;$('shortcut-scale').removeAttribute('aria-invalid');
        applyTheme();renderShortcuts();updateDensityHelp();
      } else {
        e.target.value=densityValue();
      }
    });
    const scaleInput=$('shortcut-scale');
    let scaleDirty=false;
    scaleInput.addEventListener('input',()=>{
      scaleDirty=true;
      const value=scaleInput.valueAsNumber;
      if(Number.isInteger(value)&&value>=50&&value<=200){$('shortcut-scale-error').hidden=true;scaleInput.removeAttribute('aria-invalid');}
    });
    syncModalState=()=>{
      $('density-select').value=densityValue();
      if(!scaleDirty)scaleInput.value=state.shortcutScale??100;
      $('labels-toggle').setAttribute('aria-checked',String(state.showLabels));
      $('newtab-toggle').setAttribute('aria-checked',String(state.newTab));
      updateDensityHelp();
    };
    const saveShortcutScale=async()=>{
      if(!scaleInput.isConnected||state.shortcutScaleMode!=='manual')return;
      const percent=scaleInput.valueAsNumber,error=$('shortcut-scale-error');
      if(!Number.isInteger(percent)||percent<50||percent>200){
        error.textContent=t("Укажите целое число от 50 до 200.");error.hidden=false;scaleInput.setAttribute('aria-invalid','true');return;
      }
      error.hidden=true;scaleInput.removeAttribute('aria-invalid');
      if(percent===state.shortcutScale){scaleDirty=false;return;}
      if(await commit(s=>{s.shortcutScale=percent;},false,'shortcut-scale-error',false)){scaleDirty=scaleInput.valueAsNumber!==percent;applyTheme();renderShortcuts();updateDensityHelp();}
    };
    scaleInput.addEventListener('change',()=>void saveShortcutScale());
    scaleInput.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();void saveShortcutScale();}});
    $('labels-toggle').addEventListener('click',async()=>{
      if(await commit(s=>{s.showLabels=!s.showLabels;})){
        $('labels-toggle')?.setAttribute('aria-checked',String(state.showLabels));
        renderShortcuts();
      }
    });
    $('newtab-toggle').addEventListener('click',async()=>{
      if(await commit(s=>{s.newTab=!s.newTab;})){
        $('newtab-toggle')?.setAttribute('aria-checked',String(state.newTab));
        renderShortcuts();
      }
    });
    $('search-settings').addEventListener('click',openSearchSettings);
    $('data-settings').addEventListener('click',openDataSettings);
    $('background-settings').addEventListener('click',openBackgroundSettings);
  }
  $('settings-button').addEventListener('click',openSettings);
  window.addEventListener('storage',event=>{
    if(event.key!==storageKey&&event.key!==null)return;
    // Let a queued save obtain the shared lock before refreshing unrelated
    // state; its mutation and conflict checks must use the latest stored data.
    if(writing){deferredStorageSync=true;return;}
    syncStoredState();
  });
  document.addEventListener('keydown',e=>{
    if(blockingWrites)return;
    if(e.key==='Escape'){if(!$('theme-menu').hidden){closeThemeMenu(true);return;}if(moving&&!['move-destination','native-edit'].includes(modalKind)){cancelMove();return;}if(modalKind){dismissModal();return;}if(!$('engine-menu').hidden){closeEngine();$('engine-trigger').focus();return;}if(editing()){setEditing(false);return;}$('search-input').blur();}
    const typing=e.target.matches('input,textarea,select,[contenteditable=true]');
    if(((e.key==='/'&&!typing)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'))&&!modalKind){e.preventDefault();closeEngine();void focusSearch();}
    if(e.key==='Tab'&&modalKind){const controls=[...$('modal-root').querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]),select')].filter(el=>el.offsetParent!==null);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  });
  refresh();
  if(location.hash.startsWith('#add-site?')) {
    const request=location.hash;
    try {
      // Consume the request before opening the editor: reload/back must never
      // resubmit it. The full page address remains only in the editable draft.
      history.replaceState(history.state,'',location.href.split('#')[0]);
      const draft=shortcutDraftFromHash(request);
      if(draft)void connectBookmarks().then(()=>openShortcutDialog(null,null,null,draft));
    } catch(error) {notify(error.message);}
  }
  // Normal startup is released by the native provider reply, followed by a
  // visual-state acknowledgement. startup.js owns the independent fail-open.
  void Promise.all([initialTheme,connectSearch(),connectBookmarks(),backgroundReady]).then(()=>{
    initialThemeReady=true;
    if(!browserSearch||browserSearch.available)finishStartup();
  });
  if(store.loadError)notify(t("Не удалось прочитать настройки. Исходные данные сохранены; можно восстановить дамп."));
})();

