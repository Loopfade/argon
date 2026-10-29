import {t, language, localizePage} from './i18n.js';
import {MAX_DUMP_BYTES, HomepageStore, createDump, parseDump,
  dumpStats, shortcutList, moveShortcut, navigationTarget, mergeBookmarks} from './state.js';
import {imageSource, faviconSources, engineIcon} from './icons.js';
import {MAX_BOOKMARK_BYTES, parseBookmarks} from './bookmarks.js';
import {connectBrowserSearch} from './browser-search.js';

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
    if(browserSearch?.available)browserSearch.bounds($('search-form').getBoundingClientRect(),window.scrollY);
  }
  function scheduleSearchBounds() {
    if(searchBoundsFrame)return;
    searchBoundsFrame=requestAnimationFrame(()=>{searchBoundsFrame=0;reportSearchBounds();});
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
  }
  const connectSearch = async () => {
    if(connectingSearch)return connectingSearch;
    connectingSearch=(async()=>{
      try {
        if(browserSearch)await browserSearch.refresh();
        else browserSearch=await connectBrowserSearch(applyBrowserSearch,(alpha,focused)=>{
          if(!nativeSearch())return;
          $('search-form').style.opacity=String(alpha);
          $('search-form').inert=focused;
        });
        scheduleSearchBounds();
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
  function commit(change, layout=false, errorId=null) {
    try { state=store.update(change,{shortcuts:layout}); return true; }
    catch (error) { const target=errorId && $(errorId); if(target){target.textContent=error.message||t("Не удалось сохранить изменения.");target.hidden=false;}else notify(error.message||t("Не удалось сохранить изменения.")); return false; }
  }
  function applyTheme() {
    homeTheme.apply(state.theme);
    document.documentElement.dataset.density=state.density==='compact'?'compact':'comfortable';
    document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===state.theme)));
  }
  const connectTheme = () => homeTheme.connect().catch(error => console.warn('Argon theme:', error));
  void connectTheme();
  window.addEventListener('pageshow', event => { if (event.persisted) void connectTheme(); });
  document.addEventListener('click',e=>{const b=e.target.closest('[data-theme-choice]');if(b&&commit(s=>{s.theme=b.dataset.themeChoice;}))applyTheme();});
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
    $('engine-trigger').title=t("Поисковая система: ")+engine.name;
    setHtml($('engine-menu'),t("<div class=\"popover-title\">Поисковая система</div>")+engines().map(e=>'<button type="button" class="engine-option'+(e.id===engine.id?' active':'')+'" data-engine="'+esc(e.id)+'" aria-pressed="'+(e.id===engine.id)+'"><span class="engine-icon">'+engineMarkup(e)+'</span><span>'+esc(e.id==='noai'?'DuckDuckGo':e.name)+'</span>'+(e.id==='noai'?t("<span class=\"noai-badge\">БЕЗ ИИ</span>"):'')+(e.id===engine.id?svg('check','check'):'')+'</button>').join('')+'<div class="popover-divider"></div><button type="button" class="engine-option engine-add" id="add-engine">'+svg('plus')+t("Добавить свою</button>"));
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
      if(commit(s=>{s.engine=engine.id;})){renderEngine();closeEngine();void focusSearch();}
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
    closeEngine();if(!modalKind)returnFocus=document.activeElement;modalKind=kind;
    setHtml($('modal-root'),'<div class="overlay"><section class="dialog '+extra+'" role="dialog" aria-modal="true" aria-labelledby="dialog-title">'+content+'</section></div>');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=true;});
    $('modal-root').querySelector('.overlay').addEventListener('click',e=>{if(e.target.classList.contains('overlay'))closeModal();});
    $('modal-root').querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',closeModal));
    // A delayed focus can steal the cursor after the user selects another field.
    const field=$('modal-root').querySelector('[autofocus]')||$('modal-root').querySelector('button,input,select');field?.focus();
  }
  function closeModal() {
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
    openModal(heading(item?t("Изменить ")+(type==='folder'?t("папку"):t("ярлык")):t("Добавить в избранное"))+'<form id="shortcut-form">'+(!item&&!parent?'<div class="tabs"><button type="button" class="active" data-type="site">'+svg('globe')+t("Сайт</button><button type=\"button\" data-type=\"folder\">")+svg('folder')+t("Папка</button></div>"):'')+t("<label class=\"field\"><span class=\"field-label\">Название</span><input id=\"shortcut-title\" value=\"")+esc(item?.name||'')+t("\" maxlength=\"40\" required autofocus placeholder=\"Название ")+(type==='folder'?t("папки"):t("сайта"))+'"></label><div id="site-fields"'+(type==='folder'?' hidden':'')+t("><label class=\"field\"><span class=\"field-label\">Адрес сайта</span><input id=\"shortcut-url\" value=\"")+esc(item?.url||'')+'" placeholder="example.com" spellcheck="false"'+(type==='site'?' required':'')+t("></label><div class=\"field\"><span class=\"field-label\">Иконка</span><div class=\"icon-choices\"><button type=\"button\" id=\"favicon-choice\" class=\"icon-choice")+(draftMode==='favicon'?' active':'')+'">'+svg('globe')+t("С сайта</button><button type=\"button\" id=\"custom-choice\" class=\"icon-choice")+(draftMode==='custom'?' active':'')+'">'+svg('image')+t("Своя</button><span id=\"icon-preview\" class=\"icon-preview\">")+(item?iconMarkup(item):svg('globe'))+'</span></div><button type="button" id="upload-button" class="upload-zone"'+(draftMode==='custom'?'':' hidden')+'>'+svg('upload')+t("<span>Загрузить изображение</span></button><input type=\"file\" id=\"icon-file\" accept=\"image/png,image/jpeg,image/webp,image/gif,image/svg+xml\" hidden><p class=\"field-help\">PNG, JPG, SVG или WebP, до 1 МБ.</p></div>")+(folders.length?t("<label class=\"field\"><span class=\"field-label\">Расположение</span><select id=\"shortcut-folder\"><option value=\"\">Избранное</option>")+folders.map(f=>'<option value="'+esc(f.id)+'"'+(f.id===parent?' selected':'')+'>'+esc(f.name)+'</option>').join('')+'</select></label>':'')+'</div>'+(item?t("<label class=\"field\"><span class=\"field-label\">Позиция</span><select id=\"shortcut-position\">")+shortcutList(state.shortcuts,parent).map((s,i)=>'<option value="'+i+'"'+(i===position?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></label>':'')+'<p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions">'+(item?t("<button type=\"button\" class=\"btn btn-delete\" id=\"delete-shortcut\">Удалить</button>"):'')+t("<button type=\"button\" class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">")+(item?t("Сохранить"):t("Добавить"))+'</button></div></form>','shortcut');
    const form=$('shortcut-form');
    $('modal-root').querySelectorAll('[data-type]').forEach(b=>b.addEventListener('click',()=>{type=b.dataset.type;$('modal-root').querySelectorAll('[data-type]').forEach(t=>t.classList.toggle('active',t===b));$('site-fields').hidden=type==='folder';$('shortcut-url').required=type==='site';}));
    let previewTimer;
    function preview() { clearTimeout(previewTimer);if($('shortcut-form')!==form)return;const p=$('icon-preview');if(draftMode==='custom')setHtml(p,draftIcon?iconMarkup({customIcon:draftIcon,iconMode:'custom'},true):(item?.icon?svg(item.icon):svg('image')));else{try{setHtml(p,iconMarkup({url:normalizedUrl($('shortcut-url').value.trim()),iconMode:'favicon'},true));}catch{setHtml(p,svg('globe'));}}fallbackImages(p); }
    function chooseIcon(mode) { draftMode=mode;$('favicon-choice').classList.toggle('active',mode==='favicon');$('custom-choice').classList.toggle('active',mode==='custom');$('upload-button').hidden=mode!=='custom';preview(); }
    $('favicon-choice').addEventListener('click',()=>chooseIcon('favicon'));$('custom-choice').addEventListener('click',()=>chooseIcon('custom'));
    $('shortcut-url').addEventListener('input',()=>{clearTimeout(previewTimer);previewTimer=setTimeout(preview,250);});
    $('shortcut-url').addEventListener('change',preview);$('upload-button').addEventListener('click',()=>$('icon-file').click());
    $('icon-file').addEventListener('change',()=>{const file=$('icon-file').files[0];if(!file)return;if(file.size>1024*1024||!/^image\/(png|jpeg|webp|gif|svg\+xml)$/.test(file.type)){showError(t("Выберите изображение подходящего формата размером до 1 МБ."));return;}const reader=new FileReader();reader.onload=()=>{if($('shortcut-form')!==form)return;draftIcon=String(reader.result);chooseIcon('custom');$('form-error').hidden=true;};reader.onerror=()=>{if($('shortcut-form')===form)showError(t("Не удалось прочитать изображение."));};reader.readAsDataURL(file);});
    form.addEventListener('submit',e=>{
      e.preventDefault();const name=$('shortcut-title').value.trim();if(!name){showError(t("Укажите название."));return;}let url;
      if(type==='site'){try{url=normalizedUrl($('shortcut-url').value.trim());}catch(error){showError(error.message);return;}if(draftMode==='custom'&&!draftIcon&&!item?.icon){showError(t("Загрузите иконку или выберите «С сайта»."));return;}}
      const folderSelect=$('shortcut-folder'),targetParent=type==='site'?(folderSelect?(folderSelect.value||null):parent):null;
      const desiredPosition=item?Number($('shortcut-position').value):null;
      if(!commit(next=>{
        let current;
        if(editId){const from=shortcutList(next.shortcuts,originalParent),index=from.findIndex(s=>s.id===editId);if(index<0||JSON.stringify(from[index])!==original)throw Error(t("Ярлык изменился в другой вкладке. Откройте его заново."));current=from.splice(index,1)[0];}
        const result=type==='folder'?{id:editId||uid(),type:'folder',name,children:current?.children||[]}:{id:editId||uid(),name,url,iconMode:draftMode,...(draftMode==='custom'&&draftIcon?{customIcon:draftIcon}:{}),...(draftMode==='custom'&&item?.icon&&normalizedUrl(item.url)===url&&!draftIcon?{icon:item.icon}:{})};
        const to=shortcutList(next.shortcuts,targetParent);
        const index=editId&&targetParent===originalParent?Math.min(desiredPosition,to.length):to.length;to.splice(index,0,result);
      },true,'form-error'))return;
      closeModal();renderShortcuts();notify(item?t("Изменения сохранены"):type==='folder'?t("Папка добавлена"):t("Сайт добавлен"));
    });
    $('delete-shortcut')?.addEventListener('click',()=>{if(item.type==='folder'&&item.children.length){openDeleteFolder(item);return;}deleteShortcut(editId,originalParent);});
    preview();
  }
  function deleteShortcut(id,parent) {
    let previous,after;
    if(!commit(next=>{previous=structuredClone(next.shortcuts);const list=shortcutList(next.shortcuts,parent),index=list.findIndex(s=>s.id===id);if(index<0)throw Error(t("Ярлык больше не существует."));const item=list.splice(index,1)[0];if(item.type==='folder')next.shortcuts.push(...item.children);after=JSON.stringify(next.shortcuts);},true,'form-error'))return;
    closeModal();renderShortcuts();notify(t("Удалено. Изменения сохранены"),()=>{if(!commit(next=>{if(JSON.stringify(next.shortcuts)!==after)throw Error(t("После удаления избранное уже изменилось."));next.shortcuts=previous;},true))return;renderShortcuts();notify(t("Удаление отменено"));});
  }
  function openDeleteFolder(item) { openModal(heading(t("Удалить папку?"))+t("<p class=\"dialog-subtitle\">Сайты из «")+esc(item.name)+t("» переместятся в избранное.</p><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" data-close>Отмена</button><button class=\"btn btn-primary\" id=\"confirm-delete\">Удалить папку</button></div>"),'delete');$('confirm-delete').addEventListener('click',()=>deleteShortcut(item.id,null)); }
  function renderFolderContents() {
    const folder=state.shortcuts.find(s=>s.id===currentFolder&&s.type==='folder');if(!folder){closeModal();return;}
    $('dialog-title').textContent=folder.name;const target=$('folder-grid');
    if(target){setHtml(target,folder.children.map(s=>shortcutMarkup(s,folder.id)).join('')+addMarkup(folder.id)+(folder.children.length||editing()?'':t("<p class=\"folder-empty\">В папке пока нет сайтов</p>")));fallbackImages(target);}
    const tools=$('folder-move-toolbar');
    if(tools){tools.hidden=!moving;setHtml(tools,moving?t("<p class=\"editing-hint\">Нажмите новое место или перетащите ярлык.</p><div class=\"dialog-actions\"><button type=\"button\" class=\"btn btn-secondary\" id=\"move-to-root\">В избранное</button><button type=\"button\" class=\"btn btn-secondary\" id=\"cancel-folder-move\">Отмена переноса</button></div>"):'');
      $('cancel-folder-move')?.addEventListener('click',cancelMove);
      $('move-to-root')?.addEventListener('click',()=>{const source=moving;if(!source||!commit(next=>moveShortcut(next.shortcuts,source.id,source.parent,null,next.shortcuts.length),true))return;moving=null;closeModal();updateEditing();renderShortcuts();notify(t("Ярлык перемещён"));});}
  }
  function openFolder(id) {const folder=state.shortcuts.find(s=>s.id===id);if(!folder)return;openModal(heading(folder.name)+'<div id="folder-move-toolbar" hidden></div><div class="shortcut-grid" id="folder-grid"></div><div class="dialog-actions">'+(editing()&&!moving?t("<button class=\"btn btn-secondary\" id=\"edit-folder\">Изменить папку</button>"):'')+t("<button class=\"btn btn-primary\" data-close>Готово</button></div>"),'folder','folder-dialog');currentFolder=id;renderFolderContents();$('edit-folder')?.addEventListener('click',()=>openShortcutDialog(folder));}

  function clearDrag() {document.querySelectorAll('.drag-source,.drop-target').forEach(el=>el.classList.remove('drag-source','drop-target'));drag=null;}
  function reorder(source,target) {
    if(!editing()||!moving||!source||source.id!==moving.id||!target||source.id===target.id)return;
    if(!commit(next=>{
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
    openModal(heading(t("Свой поисковик"))+t("<form id=\"engine-form\"><label class=\"field\"><span class=\"field-label\">Название</span><input id=\"engine-title\" placeholder=\"Например, Brave Search\" maxlength=\"40\" required autofocus></label><label class=\"field\"><span class=\"field-label\">Адрес поиска</span><input id=\"engine-url\" placeholder=\"https://example.com/?q={query}\" required spellcheck=\"false\"><span class=\"field-help\">Вставьте {query} вместо поискового запроса.</span></label><p id=\"form-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" type=\"button\" data-close>Отмена</button><button class=\"btn btn-primary\" type=\"submit\">Добавить</button></div></form>"),'engine');
    $('engine-form').addEventListener('submit',async e=>{e.preventDefault();const name=$('engine-title').value.trim(),template=$('engine-url').value.trim();if(!name||!template.includes('{query}')){showError(t("Укажите название и {query} в адресе поиска."));return;}let url;try{url=normalizedUrl(template.replaceAll('{query}','ARGON_QUERY')).replaceAll('ARGON_QUERY','{query}');}catch{showError(t("Укажите корректный адрес поиска."));return;}const engine={id:uid(),name,url};if(!commit(next=>{next.customEngines.push(engine);next.engine=engine.id;},false,'form-error'))return;renderEngine();closeModal();if(nativeSearch()){try{await browserSearch.select(engine);}catch{notify(t("Поисковик добавлен, но сменить поисковик браузера не удалось."));return;}}notify(t("Поисковик добавлен"));});
  }
  function exportBackup() {
    try{const dump=createDump(state),url=URL.createObjectURL(new Blob([JSON.stringify(dump,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='argon-dump-'+dump.createdAt.slice(0,16).replace('T','_').replace(':','-')+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify(t("Дамп подготовлен для скачивания"));}catch(error){backupError(error.message||t("Не удалось создать дамп."));}
  }
  function backupError(message) { const target=$('backup-error');if(target){target.textContent=message;target.hidden=false;}else notify(message); }
  async function readBackup(event) {
    const input=event.currentTarget,file=input.files[0];input.value='';if(!file)return;const button=$('backup-import');button.disabled=true;
    try{if(file.size>MAX_DUMP_BYTES)throw Error(t("Выберите дамп JSON размером до 10 МБ."));const dump=parseDump(await file.text());if(input.isConnected)openRestoreDialog(dump,file.name);}catch(error){if(input.isConnected)backupError(error.message||t("Не удалось прочитать файл."));}finally{if(button.isConnected)button.disabled=false;}
  }
  function openRestoreDialog(dump,filename) {
    const stats=dumpStats(dump.state),date=new Intl.DateTimeFormat(language() === 'ru' ? 'ru-RU' : 'en-US',{dateStyle:'medium',timeStyle:'short'}).format(new Date(dump.createdAt));
    openModal(heading(t("Восстановить дамп?"))+t("<div class=\"backup-summary\"><div><span>Файл</span><strong>")+esc(filename)+t("</strong></div><div><span>Создан</span><strong>")+esc(date)+t("</strong></div><div><span>Сайты</span><strong>")+stats.sites+t("</strong></div><div><span>Папки</span><strong>")+stats.folders+t("</strong></div><div><span>Свои поисковики</span><strong>")+stats.engines+t("</strong></div></div><p class=\"restore-note\">Текущие ярлыки, поисковики и настройки будут заменены данными из файла.</p><p id=\"backup-error\" class=\"error\" role=\"alert\" hidden></p><div class=\"dialog-actions\"><button class=\"btn btn-secondary\" type=\"button\" id=\"cancel-restore\" autofocus>Отмена</button><button class=\"btn btn-primary\" type=\"button\" id=\"confirm-restore\">Восстановить</button></div>"),'restore');
    $('cancel-restore').addEventListener('click',openSettings);
    $('confirm-restore').addEventListener('click',()=>{try{state=store.replace(dump.state);}catch{backupError(t("Не удалось сохранить дамп. Прежние данные остаются на месте."));return;}closeModal();refresh();notify(t("Дамп восстановлен"));});
  }
  function bookmarkSettingsMarkup() {
    return '<div class="backup-section"><span class="setting-label">'+t("Закладки из другого браузера")+'</span><p class="backup-description">'+t("Добавьте закладки из JSON, bookmarks.html или файла Bookmarks без расширения.")+'</p><button class="btn btn-secondary" id="bookmarks-import" type="button">'+svg('upload')+t("Импортировать закладки")+'</button><input type="file" id="bookmarks-file" hidden><p id="bookmarks-error" class="error" role="alert" hidden></p></div>';
  }
  function bookmarkError(message) {
    const target=$('bookmarks-error');
    if(target){target.textContent=message;target.hidden=false;}else notify(message);
  }
  async function readBookmarks(event) {
    const input=event.currentTarget,file=input.files[0];input.value='';if(!file)return;
    const button=$('bookmarks-import');button.disabled=true;
    try {
      if(file.size>MAX_BOOKMARK_BYTES)throw Error(t("Выберите файл закладок размером до 10 МБ."));
      const parsed=parseBookmarks(await file.text());
      if(input.isConnected)openBookmarkImport(parsed,file.name);
    } catch(error) {if(input.isConnected)bookmarkError(error.message||t("Не удалось прочитать файл."));}
    finally {if(button.isConnected)button.disabled=false;}
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
    $('cancel-bookmarks').addEventListener('click',openSettings);
    $('confirm-bookmarks').addEventListener('click',()=>{
      try {
        const result=store.importBookmarks(parsed,uid);state=result.state;
        closeModal();setEditing(false);refresh();notify(t("Добавлено закладок: {count}",{count:result.imported}));
      } catch(error) {bookmarkError(error.message||t("Не удалось сохранить закладки. Прежние данные остаются на месте."));}
    });
  }
  function openSettings() {
    if(moving)cancelMove();
    openModal(heading(t("Настроить страницу"))+'<button type="button" id="edit-shortcuts" class="setting-row edit-shortcuts"><span><strong>'+ (editing()?t("Завершить редактирование"):t("Редактировать ярлыки"))+t("</strong><small>Добавление, изменение, перемещение и удаление ярлыков.</small></span>")+svg('pencil')+t("</button><span class=\"setting-label\">Оформление</span><div class=\"theme-options\">")+[['light','sun',t("Светлая")],['dark','moon',t("Тёмная")],['amoled','amoled',t("Тёмная AMOLED")],['system','monitor',t("Как в браузере")]].map(([theme,icon,name])=>'<button class="theme-card" type="button" data-theme-choice="'+theme+'" aria-pressed="'+(state.theme===theme)+'">'+svg(icon)+name+'</button>').join('')+t("</div><label class=\"setting-row\"><span>Размер значков</span><select id=\"density-select\" aria-label=\"Размер значков\" aria-describedby=\"density-help\"><option value=\"comfortable\">Крупные</option><option value=\"compact\">Мелкие</option></select></label><div id=\"size-preview\" class=\"shortcut-grid size-preview\" aria-label=\"Образец размеров значков\"></div><p id=\"density-help\" class=\"field-help\">Мелкие значки расположены плотнее: в строке помещается больше ярлыков.</p><div class=\"setting-row\"><span>Названия ярлыков</span><button id=\"labels-toggle\" class=\"toggle\" role=\"switch\" aria-label=\"Показывать названия ярлыков\" aria-checked=\"")+state.showLabels+t("\"></button></div><div class=\"setting-row\"><span>Открывать в новой вкладке</span><button id=\"newtab-toggle\" class=\"toggle\" role=\"switch\" aria-label=\"Открывать сайты в новой вкладке\" aria-checked=\"")+state.newTab+t("\"></button></div>")+bookmarkSettingsMarkup()+t("<div class=\"backup-section\"><span class=\"setting-label\">Резервная копия</span><p class=\"backup-description\">Ярлыки, папки, свои иконки, поисковики и оформление — в одном файле JSON.</p><div class=\"backup-actions\"><button class=\"btn btn-secondary\" id=\"backup-export\" type=\"button\">")+svg('download')+t("Снять дамп</button><button class=\"btn btn-secondary\" id=\"backup-import\" type=\"button\">")+svg('upload')+t("Восстановить</button></div><input type=\"file\" id=\"backup-file\" accept=\".json,application/json\" hidden><p id=\"backup-error\" class=\"error\" role=\"alert\" hidden></p></div><div class=\"dialog-actions\"><button class=\"btn btn-primary\" data-close type=\"button\">Готово</button></div>"),'settings');
    $('edit-shortcuts').addEventListener('click',()=>{const enabled=!editing();closeModal();setEditing(enabled);if(enabled)$('edit-done').focus();});
    $('density-select').value=state.density==='compact'?'compact':'comfortable';
    setHtml($('size-preview'),[{name:'Argon'},...builtins.filter(e=>['noai','google','bing'].includes(e.id))].map(e=>'<span class="shortcut"><span class="shortcut-icon">'+(e.url?engineMarkup(e):svg('brand'))+'</span><span class="shortcut-label">'+esc(e.name==='DuckDuckGo NoAI'?'DuckDuckGo':e.name)+'</span></span>').join(''));
    fallbackImages($('size-preview'));
    $('density-select').addEventListener('change',e=>{if(commit(s=>{s.density=e.target.value;})){applyTheme();renderShortcuts();}else e.target.value=state.density==='compact'?'compact':'comfortable';});
    $('labels-toggle').addEventListener('click',()=>{if(commit(s=>{s.showLabels=!s.showLabels;})){$('labels-toggle').setAttribute('aria-checked',String(state.showLabels));renderShortcuts();}});
    $('newtab-toggle').addEventListener('click',()=>{if(commit(s=>{s.newTab=!s.newTab;})){$('newtab-toggle').setAttribute('aria-checked',String(state.newTab));renderShortcuts();}});
    $('backup-export').addEventListener('click',exportBackup);$('backup-import').addEventListener('click',()=>$('backup-file').click());$('backup-file').addEventListener('change',readBackup);
    $('bookmarks-import').addEventListener('click',()=>$('bookmarks-file').click());
    $('bookmarks-file').addEventListener('change',readBookmarks);
  }
  $('settings-button').addEventListener('click',openSettings);
  window.addEventListener('storage',event=>{if(event.key!==storageKey&&event.key!==null)return;try{state=store.state=store.latest();if(modalKind)closeModal();setEditing(false);refresh();}catch{notify(t("Не удалось прочитать настройки из другой вкладки."));}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(moving){cancelMove();return;}if(modalKind){closeModal();return;}if(!$('engine-menu').hidden){closeEngine();$('engine-trigger').focus();return;}if(editing()){setEditing(false);return;}$('search-input').blur();}
    const typing=e.target.matches('input,textarea,select,[contenteditable=true]');
    if(((e.key==='/'&&!typing)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'))&&!modalKind){e.preventDefault();closeEngine();void focusSearch();}
    if(e.key==='Tab'&&modalKind){const controls=[...$('modal-root').querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]),select')].filter(el=>el.offsetParent!==null);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  });
  refresh();
  void connectSearch();
  if(store.loadError)notify(t("Не удалось прочитать настройки. Исходные данные сохранены; можно восстановить дамп."));
})();
