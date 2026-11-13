import {t, language, localizePage} from './i18n.js';
import {MAX_DUMP_BYTES, HomepageStore, createDump, parseDump,
  dumpStats, shortcutList, moveShortcut, navigationTarget, mergeBookmarks} from './state.js';
import {imageSource, faviconSources, engineIcon} from './icons.js';
import {MAX_BOOKMARK_BYTES, parseBookmarks} from './bookmarks.js';
import {connectBrowserSearch} from './browser-search.js';
import {discoverSiteIcons} from './site-icons.js';

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
  let modalKind=null, currentFolder=null, returnFocus=null, toastTimer, drag=null, touchDrag=null, suppressClick=false, moving=null;
  const editing = () => store.editing;
  const homeTheme = globalThis.argonHomeTheme;
  let browserSearch=null, browserSearchState=null, selectingEngine=false, searchBoundsFrame=0;
  let connectingSearch=null, nativeSearchRetries=0;
  let initialThemeReady=false, startupFinished=false;
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
    if(engine.id==='browser:current') {
      const matching=state.customEngines.find(item=>{
        try{return new URL(item.url.replaceAll('{query}','example')).href===new URL(engine.url).href;}catch{return false;}
      });
      if(matching)engine.id=matching.id;
    }
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
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void connectSearch();});
  window.addEventListener('scroll',scheduleSearchBounds,{passive:true});
  window.addEventListener('resize',scheduleSearchBounds);
  new ResizeObserver(scheduleSearchBounds).observe($('search-form'));
  function notify(message, undo) {
    const toast=$('toast'); toast.replaceChildren(document.createTextNode(message));
    if (undo) { const b=document.createElement('button'); b.type='button'; b.textContent=t("Отменить"); b.addEventListener('click',undo); toast.append(b); }
    toast.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{toast.hidden=true;},undo?7000:3200);
  }
  let cancelIconSearch = () => {};
  let writing = false;
  let deferredStorageSync = false;
  function syncStoredState() {
    try {
      state=store.state=store.latest();
      if(modalKind)closeModal();
      setEditing(false);refresh();
    } catch {
      notify(t("Не удалось прочитать настройки из другой вкладки."));
    }
  }
  async function writeState(operation) {
    if (writing) throw new Error(t("Не удалось сохранить изменения."));
    writing = true;
    document.body.inert = true;
    try {
      return await store.exclusive(operation);
    } finally {
      document.body.inert = false;
      writing = false;
      if (deferredStorageSync) {
        deferredStorageSync = false;
        syncStoredState();
      }
    }
  }
  async function commit(change, layout=false, errorId=null) {
    try { await writeState(() => { state=store.update(change,{shortcuts:layout}); }); return true; }
    catch (error) { const target=errorId && $(errorId); if(target){target.textContent=error.message||t("Не удалось сохранить изменения.");target.hidden=false;}else notify(error.message||t("Не удалось сохранить изменения.")); return false; }
  }
  function applyTheme() {
    homeTheme.apply(state.theme);
    document.documentElement.dataset.density=state.shortcutScaleMode==='manual'?'manual':state.density==='compact'?'compact':'comfortable';
    document.documentElement.style.setProperty('--shortcut-scale',String((state.shortcutScale??100)/100));
    document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===state.theme)));
    updateThemeHelp();
  }
  const connectTheme = () => homeTheme.connect().catch(error => console.warn('Argon theme:', error));
  const initialTheme=connectTheme();
  window.addEventListener('pageshow', event => { if (event.persisted) void connectTheme(); });
  document.addEventListener('click',async e=>{const b=e.target.closest('[data-theme-choice]');if(b&&await commit(s=>{s.theme=b.dataset.themeChoice;}))applyTheme();});
  function updateEditing() {
    document.body.classList.toggle('editing',editing());
    $('edit-done').hidden=!editing();
    $('editing-hint').hidden=!editing()||!!moving;
    $('move-toolbar').hidden=!moving;
    $('move-title').textContent=moving?t("Переместить «")+(findItem(moving.id,moving.parent)?.name||t("ярлык"))+'»':'';
  }
  function setEditing(enabled) {
    store.setEditing(enabled);moving=null;touchDrag=null;clearDrag();
    if(!enabled)$('toast').hidden=true;
    updateEditing();renderShortcuts();
  }
  $('edit-done').addEventListener('click',()=>setEditing(false));
  $('move-cancel').addEventListener('click',cancelMove);
  const validIcon = value => typeof value==='string' && /^(data:image\/(png|jpeg|webp|gif|svg\+xml);base64,|https?:\/\/)/i.test(value);
  function imageMarkup(source,fallback='',eager=false) {
    return '<img src="'+esc(source)+'" data-icon-fallback="'+esc(fallback)+'" class="favicon" loading="'+(eager?'eager':'lazy')+'" alt="" draggable="false">';
  }
  function iconMarkup(item,eager=false) {
    if(item.iconMode!=='favicon'&&item.customIcon&&validIcon(item.customIcon))return imageMarkup(imageSource(item.customIcon,location.protocol),'',eager);
    if(item.iconMode!=='favicon'&&item.icon)return svg(item.icon);
    const [source,fallback]=faviconSources(item.url,location.protocol);
    return source?imageMarkup(source,fallback,eager):svg('globe');
  }
  function fallbackImages(root) { root.querySelectorAll('img.favicon').forEach(img=>{
    let fallback=img.dataset.iconFallback;delete img.dataset.iconFallback;
    const fail=()=>{if(fallback){const next=fallback;fallback='';img.src=next;return;}const wrap=document.createElement('span');setHtml(wrap,svg('globe'));img.replaceWith(wrap.firstElementChild);};
    img.addEventListener('error',fail);
  }); }
  function folderMarkup(folder) {
    const children=folder.children||[];
    return children.length?'<span class="folder-stack">'+Array.from({length:4},(_,i)=>children[i]?'<span>'+iconMarkup(children[i])+'</span>':'<span class="blank"></span>').join('')+'</span>':svg('folder');
  }
  function shortcutMarkup(item,parent=null) {
    const folder=item.type==='folder',tag=folder||editing()?'button':'a',selected=moving?.id===item.id;
    const attrs=tag==='button'?'type="button" draggable="'+selected+'"':'href="'+esc(item.url)+'" draggable="false"'+(state.newTab?' target="_blank" rel="noopener noreferrer"':'');
    const label=moving?t("Переместить: ")+item.name:editing()?t("Действия с ярлыком ")+item.name:(folder?t("Папка "):'')+item.name;
    return '<div class="shortcut-holder'+(selected?' moving':'')+'" data-holder="'+esc(item.id)+'" data-parent="'+esc(parent||'')+'"><'+tag+' '+attrs+' class="shortcut" data-item="'+esc(item.id)+'" aria-label="'+esc(label)+'"><span class="shortcut-icon">'+(folder?folderMarkup(item):iconMarkup(item))+'</span><span class="shortcut-label">'+esc(item.name)+'</span></'+tag+'></div>';
  }
  function addMarkup(parent=null) { return editing()&&!moving?'<button type="button" class="shortcut shortcut-add" data-add data-parent="'+esc(parent||'')+t("\" aria-label=\"Добавить сайт")+(parent?t(" в папку"):t(" или папку"))+'"><span class="shortcut-icon">'+svg('plus')+t("</span><span class=\"shortcut-label\">Добавить</span></button>"):''; }
  function renderShortcuts() {
    const positions=new Map([...document.querySelectorAll('[data-holder]')].map(el=>[el.dataset.holder,el.getBoundingClientRect()]));
    const content=state.shortcuts.map(s=>shortcutMarkup(s)).join('')+addMarkup();
    setHtml($('shortcut-grid'),content||'<div class="empty-favorites"><span class="empty-favorites-icon">'+svg('folder')+t("</span><span>Нет ярлыков</span><small>Добавьте сайты через «Настроить → Редактировать ярлыки».</small></div>"));
    document.body.classList.toggle('hidden-labels',!state.showLabels); fallbackImages($('shortcut-grid'));
    if(modalKind==='folder')renderFolderContents();
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches)document.querySelectorAll('[data-holder]').forEach(el=>{const from=positions.get(el.dataset.holder),to=el.getBoundingClientRect();if(from?.width&&typeof el.animate==='function'&&(from.x!==to.x||from.y!==to.y))el.animate([{transform:'translate('+(from.x-to.x)+'px,'+(from.y-to.y)+'px)'},{transform:'translate(0,0)'}],{duration:230,easing:'ease-out'});});
  }
  function findItem(id,parent=null) { return shortcutList(state.shortcuts,parent).find(item=>item.id===id); }
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
    fallbackImages($('selected-engine-icon'));fallbackImages($('engine-menu'));
    $('engine-trigger').disabled=selectingEngine;
    if(nativeSearch()&&!browserSearchState.canChange) {
      $('engine-menu').querySelectorAll('button').forEach(button=>{button.disabled=true;});
      const note=document.createElement('p');note.className='popover-title';
      note.textContent=t("Поисковик задан политикой или расширением.");$('engine-menu').append(note);
    }
  }
  function closeEngine() { $('engine-menu').hidden=true;$('engine-trigger').setAttribute('aria-expanded','false'); }
  $('engine-trigger').addEventListener('click',()=>{const open=$('engine-menu').hidden;$('engine-menu').hidden=!open;$('engine-trigger').setAttribute('aria-expanded',String(open));if(open)$('engine-menu').querySelector('button')?.focus();});
  async function selectEngine(engine) {
    if(engine.id===selectedEngine().id){closeEngine();void focusSearch();return;}
    if(!nativeSearch()) {
      if(await commit(s=>{s.engine=engine.id;})){renderEngine();closeEngine();void focusSearch();}
      return;
    }
    selectingEngine=true;closeEngine();renderEngine();
    try{await browserSearch.select(engine);void focusSearch();}
    catch{notify(t("Не удалось изменить поисковик браузера."));}
    finally{selectingEngine=false;renderEngine();}
  }
  $('engine-menu').addEventListener('click',e=>{const b=e.target.closest('[data-engine]');if(b&&!b.disabled){const engine=engines().find(item=>item.id===b.dataset.engine);if(engine)void selectEngine(engine);}if(e.target.closest('#add-engine'))openEngineDialog();});
  $('engine-menu').addEventListener('keydown',e=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();const list=[...$('engine-menu').querySelectorAll('button')];let index=list.indexOf(document.activeElement);index=e.key==='Home'?0:e.key==='End'?list.length-1:(index+(e.key==='ArrowDown'?1:-1)+list.length)%list.length;list[index]?.focus();});
  document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))closeEngine();});
  $('search-form').addEventListener('submit',e=>{e.preventDefault();if(nativeSearch()){void focusSearch();return;}try{const target=navigationTarget($('search-input').value,selectedEngine().url);if(target)location.assign(target);else void focusSearch();}catch{notify(t("Проверьте адрес сайта или поисковой системы."));}});

  function openModal(content,kind,extra='') {
    cancelIconSearch();cancelIconSearch=()=>{};
    closeEngine();if(!modalKind)returnFocus=document.activeElement;modalKind=kind;
    setHtml($('modal-root'),'<div class="overlay"><section class="dialog '+extra+'" role="dialog" aria-modal="true" aria-labelledby="dialog-title">'+content+'</section></div>');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=true;});
    $('modal-root').querySelector('.overlay').addEventListener('click',e=>{if(e.target.classList.contains('overlay'))closeModal();});
    $('modal-root').querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',closeModal));
    // A delayed focus can steal the cursor after the user selects another field.
    const field=$('modal-root').querySelector('[autofocus]')||$('modal-root').querySelector('button,input,select');field?.focus();
  }
  function closeModal() {
    cancelIconSearch();cancelIconSearch=()=>{};
    const cancelFolderMove=modalKind==='folder'&&moving?.parent===currentFolder;
    if(cancelFolderMove){moving=null;touchDrag=null;clearDrag();}
    const focus=returnFocus;modalKind=null;currentFolder=null;returnFocus=null;setHtml($('modal-root'),'');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=false;});
    if(cancelFolderMove){updateEditing();renderShortcuts();}
    if(focus?.isConnected)focus.focus();else (editing()?$('edit-done'):$('settings-button')).focus();
  }
  const closeButton = () => t("<button class=\"icon-button\" type=\"button\" data-close aria-label=\"Закрыть\">")+svg('close')+'</button>';
  const heading = title => '<div class="dialog-header"><h2 id="dialog-title">'+esc(title)+'</h2>'+closeButton()+'</div>';
  function normalizedUrl(value) { const url=new URL(/^[a-z][a-z\d+.-]*:/i.test(value)?value:'https://'+value);if(!['http:','https:'].includes(url.protocol)||!url.hostname)throw Error(t("Укажите корректный адрес сайта."));return url.href; }
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
    $('open-folder')?.addEventListener('click',()=>openFolder(item.id));
    $('shortcut-edit').addEventListener('click',()=>openShortcutDialog(item,parent));
    $('shortcut-delete').addEventListener('click',()=>{if(item.type==='folder'&&item.children.length)openDeleteFolder(item);else deleteShortcut(item.id,parent);});
    $('shortcut-move').addEventListener('click',()=>{closeModal();moving={id:item.id,parent};if(parent)openFolder(parent);updateEditing();renderShortcuts();document.querySelector('[data-holder="'+CSS.escape(item.id)+'"] [data-item]')?.focus();});
  }
  function cancelMove() {moving=null;touchDrag=null;clearDrag();updateEditing();renderShortcuts();}

  function openShortcutDialog(item,parent=null) {
    if(!editing())return;
    const editId=item?.id,originalParent=parent,original=item?JSON.stringify(item):null;
    let type=item?.type==='folder'?'folder':'site',draftIcon=item?.customIcon||'',draftMode=item?.iconMode||(item?.customIcon?'custom':'favicon');
    const folders=state.shortcuts.filter(s=>s.type==='folder');
    const position=item?shortcutList(state.shortcuts,parent).findIndex(s=>s.id===item.id):null;
    openModal(heading(item?t("Изменить ")+(type==='folder'?t("папку"):t("ярлык")):t("Добавить в избранное"))+'<form id="shortcut-form">'+(!item&&!parent?'<div class="tabs"><button type="button" class="active" data-type="site">'+svg('globe')+t("Сайт</button><button type=\"button\" data-type=\"folder\">")+svg('folder')+t("Папка</button></div>"):'')+t("<label class=\"field\"><span class=\"field-label\">Название</span><input id=\"shortcut-title\" value=\"")+esc(item?.name||'')+t("\" maxlength=\"40\" required autofocus placeholder=\"Название ")+(type==='folder'?t("папки"):t("сайта"))+'"></label><div id="site-fields"'+(type==='folder'?' hidden':'')+t("><label class=\"field\"><span class=\"field-label\">Адрес сайта</span><input id=\"shortcut-url\" value=\"")+esc(item?.url||'')+'" placeholder="example.com" spellcheck="false"'+(type==='site'?' required':'')+t("></label><div class=\"field\"><span class=\"field-label\">Иконка</span><div class=\"icon-choices\"><button type=\"button\" id=\"favicon-choice\" class=\"icon-choice")+(draftMode==='favicon'?' active':'')+'">'+svg('globe')+t("С сайта</button><button type=\"button\" id=\"custom-choice\" class=\"icon-choice")+(draftMode==='custom'?' active':'')+'">'+svg('image')+t("Своя</button><span id=\"icon-preview\" class=\"icon-preview\">")+(item?iconMarkup(item):svg('globe'))+'</span></div><button type="button" id="upload-button" class="upload-zone"'+(draftMode==='custom'?'':' hidden')+'>'+svg('upload')+t("<span>Загрузить изображение</span></button><input type=\"file\" id=\"icon-file\" accept=\"image/png,image/jpeg,image/webp,image/gif,image/svg+xml\" hidden><p id=\"icon-help\" class=\"field-help\">PNG, JPG, WebP, GIF или SVG, до 1 МБ.</p></div>")+(folders.length?t("<label class=\"field\"><span class=\"field-label\">Расположение</span><select id=\"shortcut-folder\"><option value=\"\">Избранное</option>")+folders.map(f=>'<option value="'+esc(f.id)+'"'+(f.id===parent?' selected':'')+'>'+esc(f.name)+'</option>').join('')+'</select></label>':'')+'</div>'+(item?t("<label class=\"field\"><span class=\"field-label\">Позиция</span><select id=\"shortcut-position\">")+shortcutList(state.shortcuts,parent).map((s,i)=>'<option value="'+i+'"'+(i===position?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></label>':'')+'<p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions">'+(item?t("<button type=\"button\" class=\"btn btn-delete\" id=\"delete-shortcut\">Удалить</button>"):'')+t("<button type=\"button\" class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">")+(item?t("Сохранить"):t("Добавить"))+'</button></div></form>','shortcut');
    const form=$('shortcut-form');
    $('icon-preview').parentElement.insertAdjacentElement('afterend',document.createElement('div')).id='site-icon-finder';
    setHtml($('site-icon-finder'),'<button type="button" id="find-site-icons" class="upload-zone">'+svg('search')+esc(t('Найти иконки'))+'</button><section id="site-icon-results" class="site-icon-results" hidden><p id="site-icon-status" class="field-help" role="status" aria-live="polite"></p><div id="site-icon-options"></div><p id="site-icon-selection" class="field-help" aria-live="polite"></p></section>');
    $('modal-root').querySelectorAll('[data-type]').forEach(b=>b.addEventListener('click',()=>{type=b.dataset.type;$('modal-root').querySelectorAll('[data-type]').forEach(t=>t.classList.toggle('active',t===b));$('site-fields').hidden=type==='folder';$('shortcut-url').required=type==='site';}));
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
    function preview() { clearTimeout(previewTimer);if($('shortcut-form')!==form)return;const p=$('icon-preview');if(draftMode==='custom')setHtml(p,draftIcon?iconMarkup({customIcon:draftIcon,iconMode:'custom'},true):(item?.icon?svg(item.icon):svg('image')));else{try{setHtml(p,iconMarkup({url:normalizedUrl($('shortcut-url').value.trim()),iconMode:'favicon'},true));}catch{setHtml(p,svg('globe'));}}fallbackImages(p); }
    function chooseIcon(mode) { draftMode=mode;$('favicon-choice').classList.toggle('active',mode==='favicon');$('custom-choice').classList.toggle('active',mode==='custom');$('upload-button').hidden=mode!=='custom';$('icon-help').hidden=mode!=='custom';preview();updateIconSelection(); }
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
    form.querySelectorAll('[data-type]').forEach(button=>button.addEventListener('click',resetIconSearch));
    $('icon-file').addEventListener('change',resetIconSearch);
    $('icon-help').hidden=draftMode!=='custom';
    $('favicon-choice').addEventListener('click',()=>chooseIcon('favicon'));$('custom-choice').addEventListener('click',()=>chooseIcon('custom'));
    $('shortcut-url').addEventListener('input',()=>{clearTimeout(previewTimer);previewTimer=setTimeout(preview,250);});
    $('shortcut-url').addEventListener('change',preview);$('upload-button').addEventListener('click',()=>$('icon-file').click());
    $('icon-file').addEventListener('change',()=>{const file=$('icon-file').files[0];if(!file)return;if(file.size>1024*1024||!/^image\/(png|jpeg|webp|gif|svg\+xml)$/.test(file.type)){showError(t("Выберите изображение подходящего формата размером до 1 МБ."));return;}const reader=new FileReader();reader.onload=()=>{if($('shortcut-form')!==form)return;draftIcon=String(reader.result);chooseIcon('custom');$('form-error').hidden=true;};reader.onerror=()=>{if($('shortcut-form')===form)showError(t("Не удалось прочитать изображение."));};reader.readAsDataURL(file);});
    form.addEventListener('submit',async e=>{
      e.preventDefault();const name=$('shortcut-title').value.trim();if(!name){showError(t("Укажите название."));return;}let url;
      if(type==='site'){try{url=normalizedUrl($('shortcut-url').value.trim());}catch(error){showError(error.message);return;}if(draftMode==='custom'&&!draftIcon&&!item?.icon){showError(t("Загрузите иконку или выберите «С сайта»."));return;}}
      const folderSelect=$('shortcut-folder'),targetParent=type==='site'?(folderSelect?(folderSelect.value||null):parent):null;
      const desiredPosition=item?Number($('shortcut-position').value):null;
      if(!await commit(next=>{
        let current;
        if(editId){const from=shortcutList(next.shortcuts,originalParent),index=from.findIndex(s=>s.id===editId);if(index<0||JSON.stringify(from[index])!==original)throw Error(t("Ярлык изменился в другой вкладке. Откройте его заново."));current=from.splice(index,1)[0];}
        const result=type==='folder'?{id:editId||uid(),type:'folder',name,children:current?.children||[]}:{id:editId||uid(),name,url,iconMode:draftMode,...(draftMode==='custom'&&draftIcon?{customIcon:draftIcon}:{}),...(draftMode==='custom'&&item?.icon&&normalizedUrl(item.url)===url&&!draftIcon?{icon:item.icon}:{})};
        const to=shortcutList(next.shortcuts,targetParent);
        const index=editId?Math.max(0,Math.min(desiredPosition,to.length)):to.length;to.splice(index,0,result);
      },true,'form-error'))return;
      closeModal();renderShortcuts();notify(item?t("Изменения сохранены"):type==='folder'?t("Папка добавлена"):t("Сайт добавлен"));
    });
    $('delete-shortcut')?.addEventListener('click',()=>{if(item.type==='folder'&&item.children.length){openDeleteFolder(item);return;}deleteShortcut(editId,originalParent);});
    preview();
  }
  async function deleteShortcut(id,parent) {
    let previous,after;
    if(!await commit(next=>{previous=structuredClone(next.shortcuts);const list=shortcutList(next.shortcuts,parent),index=list.findIndex(s=>s.id===id);if(index<0)throw Error(t("Ярлык больше не существует."));const item=list.splice(index,1)[0];if(item.type==='folder')next.shortcuts.push(...item.children);after=JSON.stringify(next.shortcuts);},true,'form-error'))return;
    closeModal();renderShortcuts();notify(t("Удалено. Изменения сохранены"),async()=>{if(!await commit(next=>{if(JSON.stringify(next.shortcuts)!==after)throw Error(t("После удаления избранное уже изменилось."));next.shortcuts=previous;},true))return;renderShortcuts();notify(t("Удаление отменено"));});
  }
  function openDeleteFolder(item) { openModal(heading(t("Удалить папку?"))+t("<p class=\"dialog-subtitle\">Сайты из «")+esc(item.name)+t("» переместятся в избранное.</p><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" id=\"confirm-delete\">Удалить папку</button></div>"),'delete');$('confirm-delete').addEventListener('click',()=>deleteShortcut(item.id,null)); }
  function renderFolderContents() {
    const folder=state.shortcuts.find(s=>s.id===currentFolder&&s.type==='folder');if(!folder){closeModal();return;}
    $('dialog-title').textContent=folder.name;const target=$('folder-grid');
    if(target){setHtml(target,folder.children.map(s=>shortcutMarkup(s,folder.id)).join('')+addMarkup(folder.id)+(folder.children.length||editing()?'':t("<p class=\"folder-empty\">В папке пока нет сайтов</p>")));fallbackImages(target);}
    const tools=$('folder-move-toolbar');
    if(tools){tools.hidden=!moving;setHtml(tools,moving?t("<p class=\"editing-hint\">Нажмите новое место или перетащите ярлык.</p><div class=\"dialog-actions\"><button type=\"button\" class=\"btn btn-secondary\" id=\"move-to-root\">В избранное</button><button type=\"button\" class=\"btn btn-secondary\" id=\"cancel-folder-move\">Отмена переноса</button></div>"):'');
      $('cancel-folder-move')?.addEventListener('click',cancelMove);
      $('move-to-root')?.addEventListener('click',async()=>{const source=moving;if(!source||!await commit(next=>moveShortcut(next.shortcuts,source.id,source.parent,null,next.shortcuts.length),true))return;moving=null;closeModal();updateEditing();renderShortcuts();notify(t("Ярлык перемещён"));});}
  }
  function openFolder(id) {const folder=state.shortcuts.find(s=>s.id===id);if(!folder)return;openModal(heading(folder.name)+'<div id="folder-move-toolbar" hidden></div><div class="shortcut-grid" id="folder-grid"></div><div class="dialog-actions">'+(editing()&&!moving?t("<button class=\"btn btn-secondary\" id=\"edit-folder\">Изменить папку</button>"):'')+t("<button class=\"btn btn-primary\" data-close>Готово</button></div>"),'folder','folder-dialog');currentFolder=id;renderFolderContents();$('edit-folder')?.addEventListener('click',()=>openShortcutDialog(folder));}

  function clearDrag() {document.querySelectorAll('.drag-source,.drop-target').forEach(el=>el.classList.remove('drag-source','drop-target'));drag=null;}
  async function reorder(source,target) {
    if(!editing()||!moving||!source||source.id!==moving.id||!target||source.id===target.id)return;
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

  function openEngineDialog() {
    openModal(heading(t("Свой поисковик"))+(nativeSearch()?'<p class="field-help">'+t("После добавления этот поисковик будет выбран для браузера.")+'</p>':'')+t("<form id=\"engine-form\"><label class=\"field\"><span class=\"field-label\">Название</span><input id=\"engine-title\" placeholder=\"Например, Brave Search\" maxlength=\"40\" required autofocus></label><label class=\"field\"><span class=\"field-label\">Адрес поиска</span><input id=\"engine-url\" placeholder=\"https://example.com/?q={query}\" required spellcheck=\"false\"><span class=\"field-help\">Вставьте {query} вместо поискового запроса.</span></label><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" type=\"button\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">Добавить и выбрать</button></div></form>"),'engine');
    $('engine-form').addEventListener('submit',async e=>{e.preventDefault();const name=$('engine-title').value.trim(),template=$('engine-url').value.trim();if(!name||!template.includes('{query}')){showError(t("Укажите название и {query} в адресе поиска."));return;}let url;try{url=normalizedUrl(template.replaceAll('{query}','ARGON_QUERY')).replaceAll('ARGON_QUERY','{query}');}catch{showError(t("Укажите корректный адрес поиска."));return;}const engine={id:uid(),name,url};if(!await commit(next=>{next.customEngines.push(engine);next.engine=engine.id;},false,'form-error'))return;renderEngine();closeModal();if(nativeSearch()){try{await browserSearch.select(engine);}catch{notify(t("Поисковик добавлен, но сменить поисковик браузера не удалось."));return;}}notify(t("Поисковик добавлен"));});
  }
  function exportBackup() {
    try{const dump=createDump(state),url=URL.createObjectURL(new Blob([JSON.stringify(dump,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='argon-dump-'+dump.createdAt.slice(0,16).replace('T','_').replace(':','-')+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify(t("Резервная копия подготовлена для скачивания"));}catch(error){backupError(error.message||t("Не удалось создать резервную копию."));}
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
      '<p class="restore-note">'+t("Текущие ярлыки, папки, свои иконки и настройки страницы будут заменены.")+'</p>'+
      '<p class="field-help">'+t("Загруженные изображения встроены в файл JSON.")+' '+t("Активный поисковик браузера выбирается отдельно.")+'</p>'+
      '<p id="backup-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" id="cancel-restore" autofocus>'+t("Отмена")+'</button><button class="btn btn-primary" type="button" id="confirm-restore">'+t("Заменить и восстановить")+'</button></div>','restore');
    $('cancel-restore').addEventListener('click',openDataSettings);
    $('confirm-restore').addEventListener('click',async()=>{
      try {
        await writeState(() => { state=store.replace(dump.state); });
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
  function bookmarkError(message) {
    const target=$('bookmarks-error');
    if(target){target.textContent=message;target.hidden=false;}else notify(message);
  }
  function openBookmarkImport(parsed,filename) {
    const preview=mergeBookmarks(store.latest(),parsed,uid);
    const rows=[[t("Файл"),filename],[t("Формат"),parsed.format],[t("Новые закладки"),preview.imported],
      [t("Новые папки"),preview.folders],[t("Повторные ссылки"),preview.duplicates],[t("Пропущенные записи"),preview.skipped]];
    let notes='<p class="restore-note">'+t("Закладки добавятся к вашим ярлыкам. Настройки страницы сохранятся. Повторяющиеся адреса будут пропущены.")+'</p>';
    if(parsed.records.some(record=>record.path.length>1))notes+='<p class="field-help">'+t("Вложенные папки будут показаны отдельно с путём в названии.")+'</p>';
    if(preview.shortened)notes+='<p class="field-help">'+t("Длинные названия будут сокращены: {count}.",{count:preview.shortened})+'</p>';
    if(!preview.imported)notes+='<p class="field-help">'+t("Все эти закладки уже есть на домашней странице.")+'</p>';
    openModal(heading(t("Импортировать закладки?"))+'<div class="backup-summary">'+rows.map(([label,value])=>'<div><span>'+esc(label)+'</span><strong>'+esc(value)+'</strong></div>').join('')+'</div>'+notes+'<p id="bookmarks-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" id="cancel-bookmarks" autofocus>'+t("Отмена")+'</button><button class="btn btn-primary" type="button" id="confirm-bookmarks"'+(preview.imported?'':' disabled')+'>'+t("Добавить закладки")+'</button></div>','bookmarks');
    $('cancel-bookmarks').addEventListener('click',openDataSettings);
    $('confirm-bookmarks').addEventListener('click',async()=>{
      try {
        const result=await writeState(() => {
          const imported=store.importBookmarks(parsed,uid);
          state=imported.state;
          return imported;
        });
        closeModal();setEditing(false);refresh();notify(t("Добавлено закладок: {count}",{count:result.imported}));
      } catch(error) {bookmarkError(error.message||t("Не удалось сохранить закладки. Прежние данные остаются на месте."));}
    });
  }
  function openDataSettings() {
    openModal(heading(t("Перенос данных"))+
      '<div class="backup-section"><span class="setting-label">'+t("Резервная копия Argon")+'</span>'+
      '<p class="backup-description">'+t("Ярлыки, папки, свои поисковики и оформление домашней страницы.")+'</p>'+
      '<div class="backup-included">'+svg('image')+'<div><strong>'+t("Свои иконки включены")+'</strong><p class="field-help">'+t("Загруженные изображения встроены в файл JSON.")+'</p></div></div>'+
      '<p class="field-help">'+t("Копия Argon .json, до 10 МБ.")+'</p><div class="backup-actions">'+
      '<button class="btn btn-secondary" id="backup-export" type="button">'+svg('download')+t("Сохранить копию")+'</button>'+
      '<button class="btn btn-secondary" id="backup-import" type="button">'+svg('upload')+t("Восстановить из копии")+'</button></div>'+
      '<p class="field-help">'+t("Восстановление заменит текущие данные страницы.")+' '+t("Активный поисковик браузера выбирается отдельно.")+'</p>'+
      '<input type="file" id="backup-file" accept=".json,application/json" hidden><p id="backup-error" class="error" role="alert" hidden></p></div>'+
      bookmarkSettingsMarkup()+'<div class="dialog-actions"><button class="btn btn-secondary" id="data-back" type="button">'+t("Назад к настройкам")+'</button></div>','data');
    $('data-back').addEventListener('click',openSettings);
    $('backup-export').addEventListener('click',exportBackup);
    $('backup-import').addEventListener('click',()=>$('backup-file').click());
    $('bookmarks-import').addEventListener('click',()=>$('bookmarks-file').click());
    $('backup-file').addEventListener('change',readTransfer);
    $('bookmarks-file').addEventListener('change',readTransfer);
  }
  function updateThemeHelp() {
    const target=$('theme-help');if(!target)return;
    const descriptions={light:t("Светлое оформление домашней страницы."),dark:t("Тёмное оформление домашней страницы."),amoled:t("Чисто чёрный фон домашней страницы."),system:t("Тема меняется вместе с темой браузера.")};
    target.textContent=descriptions[state.theme];
  }
  function updateDensityHelp() {
    const manual=state.shortcutScaleMode==='manual';
    const field=$('manual-shortcut-scale');if(field)field.hidden=!manual;
    const target=$('density-help');if(target)target.textContent=state.density==='compact'
      ?t("Мелкие ярлыки расположены плотнее: в строке помещается больше сайтов.")
      :t("Крупные ярлыки легче различать: в строке помещается меньше сайтов.");
    if(manual&&target)target.textContent=t("Масштаб ярлыков: {percent}%. Количество колонок подстраивается под экран.",{percent:state.shortcutScale});
  }
  function openSettings() {
    if(moving)cancelMove();
    openModal(heading(t("Настроить страницу"))+'<p class="field-help settings-autosave">'+t("Изменения сохраняются автоматически")+'</p>'+
      '<button type="button" id="edit-shortcuts" class="setting-row edit-shortcuts"><span><strong>'+(editing()?t("Завершить редактирование"):t("Редактировать ярлыки"))+'</strong><small>'+t("Добавление, изменение, перемещение и удаление ярлыков.")+'</small></span>'+svg('pencil')+'</button>'+
      '<span class="setting-label">'+t("Оформление")+'</span><div class="theme-options">'+
      [['light','sun',t("Светлая")],['dark','moon',t("Тёмная")],['amoled','amoled',t("Тёмная AMOLED")],['system','monitor',t("Как в браузере")]].map(([theme,icon,name])=>'<button class="theme-card" type="button" data-theme-choice="'+theme+'" aria-pressed="'+(state.theme===theme)+'">'+svg(icon)+name+'</button>').join('')+
      '</div><p id="theme-help" class="field-help"></p>'+
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
        $('shortcut-scale').value=state.shortcutScale??100;
        $('shortcut-scale-error').hidden=true;$('shortcut-scale').removeAttribute('aria-invalid');
        applyTheme();renderShortcuts();updateDensityHelp();
      } else {
        e.target.value=densityValue();
      }
    });
    const scaleInput=$('shortcut-scale');
    const saveShortcutScale=async()=>{
      if(writing||!scaleInput.isConnected||state.shortcutScaleMode!=='manual')return;
      const percent=scaleInput.valueAsNumber,error=$('shortcut-scale-error');
      if(!Number.isInteger(percent)||percent<50||percent>200){
        error.textContent=t("Укажите целое число от 50 до 200.");error.hidden=false;scaleInput.setAttribute('aria-invalid','true');return;
      }
      error.hidden=true;scaleInput.removeAttribute('aria-invalid');
      if(percent===state.shortcutScale)return;
      if(await commit(s=>{s.shortcutScale=percent;},false,'shortcut-scale-error')){applyTheme();renderShortcuts();updateDensityHelp();}
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
    $('data-settings').addEventListener('click',openDataSettings);
  }
  $('settings-button').addEventListener('click',openSettings);
  window.addEventListener('storage',event=>{
    if(event.key!==storageKey&&event.key!==null)return;
    // A queued save still owns the current edit. Refreshing now would close its
    // dialog and revoke editing before the write obtains the shared lock.
    if(writing){deferredStorageSync=true;return;}
    syncStoredState();
  });
  document.addEventListener('keydown',e=>{
    if(writing)return;
    if(e.key==='Escape'){if(moving){cancelMove();return;}if(modalKind){closeModal();return;}if(!$('engine-menu').hidden){closeEngine();$('engine-trigger').focus();return;}if(editing()){setEditing(false);return;}$('search-input').blur();}
    const typing=e.target.matches('input,textarea,select,[contenteditable=true]');
    if(((e.key==='/'&&!typing)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'))&&!modalKind){e.preventDefault();closeEngine();void focusSearch();}
    if(e.key==='Tab'&&modalKind){const controls=[...$('modal-root').querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]),select')].filter(el=>el.offsetParent!==null);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  });
  refresh();
  // Normal startup is released by the native provider reply, followed by a
  // visual-state acknowledgement. startup.js owns the independent fail-open.
  void Promise.all([initialTheme,connectSearch()]).then(()=>{
    initialThemeReady=true;
    if(!browserSearch||browserSearch.available)finishStartup();
  });
  if(store.loadError)notify(t("Не удалось прочитать настройки. Исходные данные сохранены; можно восстановить дамп."));
})();

