import {MAX_DUMP_BYTES, HomepageStore, createDump, parseDump,
  dumpStats, shortcutList, moveShortcut, navigationTarget} from './state.js';

(() => {
  'use strict';
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
  let modalKind=null, currentFolder=null, returnFocus=null, toastTimer, drag=null, touchDrag=null, suppressClick=false;
  const editing = () => !store.locked;
  const media = matchMedia('(prefers-color-scheme: dark)');
  const builtins = [
    {id:'google',name:'Google',icon:'google',url:'https://www.google.com/search?q={query}'},
    {id:'yandex',name:'Яндекс',icon:'yandex',url:'https://yandex.ru/search/?text={query}'},
    {id:'duck',name:'DuckDuckGo',icon:'duck',url:'https://duckduckgo.com/?q={query}'},
    {id:'noai',name:'DuckDuckGo NoAI',short:'DDG NoAI',icon:'duck',url:'https://noai.duckduckgo.com/?q={query}'}
  ];
  const engines = () => [...builtins,...state.customEngines];
  const selectedEngine = () => engines().find(e=>e.id===state.engine) || builtins[0];
  function notify(message, undo) {
    const toast=$('toast'); toast.replaceChildren(document.createTextNode(message));
    if (undo) { const b=document.createElement('button'); b.type='button'; b.textContent='Отменить'; b.addEventListener('click',undo); toast.append(b); }
    toast.hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>{toast.hidden=true;},undo?7000:3200);
  }
  function commit(change, layout=false, errorId=null) {
    try { state=store.update(change,{shortcuts:layout}); return true; }
    catch (error) { const target=errorId && $(errorId); if(target){target.textContent=error.message||'Не удалось сохранить изменения.';target.hidden=false;}else notify(error.message||'Не удалось сохранить изменения.'); return false; }
  }
  function applyTheme() {
    const theme=state.theme==='system'?(media.matches?'dark':'light'):state.theme;
    document.documentElement.dataset.theme=theme;
    document.documentElement.dataset.density=state.density;
    document.querySelector('meta[name="theme-color"]').content=theme==='dark'?'#141715':'#f7f7f4';
    document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===state.theme)));
  }
  media.addEventListener('change',applyTheme);
  document.addEventListener('click',e=>{const b=e.target.closest('[data-theme-choice]');if(b&&commit(s=>{s.theme=b.dataset.themeChoice;}))applyTheme();});
  function updateLock() {
    $('edit-lock').checked=store.locked;
    $('edit-lock-label').textContent=store.locked?'Закреплено':'Редактирование';
    $('edit-lock-note').textContent=store.locked?'':'Автосохранение';
    setHtml($('edit-lock-icon'),svg(store.locked?'lock':'unlock'));
    document.body.classList.toggle('editing',editing());
  }
  $('edit-lock').addEventListener('change',()=>{
    store.setLocked($('edit-lock').checked); touchDrag=null; clearDrag(); updateLock(); renderShortcuts();
    if(store.locked)notify('Изменения закреплены');
  });
  const validIcon = value => typeof value==='string' && /^(data:image\/(png|jpeg|webp|gif|svg\+xml);base64,|https?:\/\/)/i.test(value);
  function favicon(url) { try { return 'https://icons.duckduckgo.com/ip3/'+new URL(url).hostname+'.ico'; } catch { return ''; } }
  function iconMarkup(item) {
    if(item.customIcon&&validIcon(item.customIcon))return '<img src="'+esc(item.customIcon)+'" alt="" class="favicon" loading="lazy" draggable="false">';
    if(item.icon)return svg(item.icon);
    return '<img src="'+esc(favicon(item.url))+'" alt="" class="favicon" loading="lazy" draggable="false">';
  }
  function fallbackImages(root) { root.querySelectorAll('img.favicon').forEach(img=>img.addEventListener('error',()=>{const wrap=document.createElement('span');setHtml(wrap,svg('globe'));img.replaceWith(wrap.firstElementChild);},{once:true})); }
  function folderMarkup(folder) {
    const children=folder.children||[];
    return children.length?'<span class="folder-stack">'+Array.from({length:4},(_,i)=>children[i]?'<span>'+iconMarkup(children[i])+'</span>':'<span class="blank"></span>').join('')+'</span>':svg('folder');
  }
  function shortcutMarkup(item,parent=null) {
    const folder=item.type==='folder',tag=folder?'button':'a';
    const attrs=folder?'type="button"':'href="'+esc(item.url)+'" draggable="'+!editing()+'"'+(state.newTab?' target="_blank" rel="noopener noreferrer"':'');
    return '<div class="shortcut-holder" data-holder="'+esc(item.id)+'" data-parent="'+esc(parent||'')+'"><'+tag+' '+attrs+' class="shortcut" data-item="'+esc(item.id)+'" aria-label="'+esc((folder?'Папка ':'')+item.name)+'"><span class="shortcut-icon">'+(folder?folderMarkup(item):iconMarkup(item))+'</span><span class="shortcut-label">'+esc(item.name)+'</span></'+tag+'>'+(editing()?'<button type="button" class="shortcut-grip" data-grip draggable="true" aria-label="Переместить '+esc(item.name)+'">'+svg('grip')+'</button><button type="button" class="shortcut-edit" data-edit aria-label="Изменить '+esc(item.name)+'">'+svg('pencil')+'</button>':'')+'</div>';
  }
  function addMarkup(parent=null) { return editing()?'<button type="button" class="shortcut shortcut-add" data-add data-parent="'+esc(parent||'')+'" aria-label="Добавить сайт'+(parent?' в папку':' или папку')+'"><span class="shortcut-icon">'+svg('plus')+'</span><span class="shortcut-label">Добавить</span></button>':''; }
  function renderShortcuts() {
    const content=state.shortcuts.map(s=>shortcutMarkup(s)).join('')+addMarkup();
    setHtml($('shortcut-grid'),content||'<div class="empty-favorites"><span class="empty-favorites-icon">'+svg('folder')+'</span><span>Нет ярлыков</span><small>Откройте замок, чтобы добавить свои</small></div>');
    document.body.classList.toggle('hidden-labels',!state.showLabels); fallbackImages($('shortcut-grid'));
    if(modalKind==='folder')renderFolderContents();
  }
  function findItem(id,parent=null) { return shortcutList(state.shortcuts,parent).find(item=>item.id===id); }
  function refresh() { applyTheme();renderEngine();updateLock();renderShortcuts(); }
  function renderEngine() {
    const engine=selectedEngine();setHtml($('selected-engine-icon'),svg(engine.icon||'globe'));$('selected-engine-name').textContent=engine.short||engine.name;
    $('engine-trigger').title='Поисковая система: '+engine.name;
    setHtml($('engine-menu'),'<div class="popover-title">Поисковая система</div>'+engines().map(e=>'<button type="button" class="engine-option'+(e.id===engine.id?' active':'')+'" data-engine="'+esc(e.id)+'" aria-pressed="'+(e.id===engine.id)+'"><span class="engine-icon">'+svg(e.icon||'globe')+'</span><span>'+esc(e.id==='noai'?'DuckDuckGo':e.name)+'</span>'+(e.id==='noai'?'<span class="noai-badge">БЕЗ ИИ</span>':'')+(e.id===engine.id?svg('check','check'):'')+'</button>').join('')+'<div class="popover-divider"></div><button type="button" class="engine-option engine-add" id="add-engine">'+svg('plus')+'Добавить свою</button>');
  }
  function closeEngine() { $('engine-menu').hidden=true;$('engine-trigger').setAttribute('aria-expanded','false'); }
  $('engine-trigger').addEventListener('click',()=>{const open=$('engine-menu').hidden;$('engine-menu').hidden=!open;$('engine-trigger').setAttribute('aria-expanded',String(open));if(open)$('engine-menu').querySelector('button')?.focus();});
  $('engine-menu').addEventListener('click',e=>{const b=e.target.closest('[data-engine]');if(b&&commit(s=>{s.engine=b.dataset.engine;})){renderEngine();closeEngine();$('search-input').focus();}if(e.target.closest('#add-engine'))openEngineDialog();});
  $('engine-menu').addEventListener('keydown',e=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(e.key))return;e.preventDefault();const list=[...$('engine-menu').querySelectorAll('button')];let index=list.indexOf(document.activeElement);index=e.key==='Home'?0:e.key==='End'?list.length-1:(index+(e.key==='ArrowDown'?1:-1)+list.length)%list.length;list[index]?.focus();});
  document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))closeEngine();});
  $('search-form').addEventListener('submit',e=>{e.preventDefault();try{const target=navigationTarget($('search-input').value,selectedEngine().url);if(target)location.assign(target);else $('search-input').focus();}catch{notify('Проверьте адрес сайта или поисковой системы.');}});

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
    const focus=returnFocus;modalKind=null;currentFolder=null;returnFocus=null;setHtml($('modal-root'),'');
    document.querySelectorAll('body>.header,body>.workspace,body>.footer').forEach(el=>{el.inert=false;});
    if(focus?.isConnected)focus.focus();else $('edit-lock').focus();
  }
  const closeButton = () => '<button class="icon-button" type="button" data-close aria-label="Закрыть">'+svg('close')+'</button>';
  const heading = title => '<div class="dialog-header"><h2 id="dialog-title">'+esc(title)+'</h2>'+closeButton()+'</div>';
  function normalizedUrl(value) { const url=new URL(/^[a-z][a-z\d+.-]*:/i.test(value)?value:'https://'+value);if(!['http:','https:'].includes(url.protocol)||!url.hostname)throw Error('Укажите корректный адрес сайта.');return url.href; }
  function showError(message) { const error=$('form-error');if(error){error.textContent=message;error.hidden=false;} }
  document.addEventListener('click',e=>{
    if(suppressClick){e.preventDefault();return;}
    const add=e.target.closest('[data-add]');if(add){if(editing())openShortcutDialog(null,add.dataset.parent||null);return;}
    const holder=e.target.closest('[data-holder]');if(!holder)return;
    const id=holder.dataset.holder,parent=holder.dataset.parent||null;
    let item;try{item=findItem(id,parent);}catch{return;}if(!item)return;
    if(e.target.closest('[data-edit],[data-grip]')){e.preventDefault();if(editing())openShortcutDialog(item,parent);return;}
    if(!e.target.closest('[data-item]'))return;
    if(item.type==='folder'){e.preventDefault();openFolder(item.id);}else if(editing()){e.preventDefault();openShortcutDialog(item,parent);}
  });
  document.addEventListener('contextmenu',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!editing())return;e.preventDefault();try{const item=findItem(holder.dataset.holder,holder.dataset.parent||null);if(item)openShortcutDialog(item,holder.dataset.parent||null);}catch{notify('Избранное изменилось в другой вкладке.');}});

  function openShortcutDialog(item,parent=null) {
    if(!editing())return;
    const editId=item?.id,originalParent=parent,original=item?JSON.stringify(item):null;
    let type=item?.type==='folder'?'folder':'site',draftIcon=item?.customIcon||'',draftMode=item?.iconMode||(item?.customIcon?'custom':'favicon');
    const folders=state.shortcuts.filter(s=>s.type==='folder');
    const position=item?shortcutList(state.shortcuts,parent).findIndex(s=>s.id===item.id):null;
    openModal(heading(item?'Изменить '+(type==='folder'?'папку':'ярлык'):'Добавить в избранное')+'<form id="shortcut-form">'+(!item&&!parent?'<div class="tabs"><button type="button" class="active" data-type="site">'+svg('globe')+'Сайт</button><button type="button" data-type="folder">'+svg('folder')+'Папка</button></div>':'')+'<label class="field"><span class="field-label">Название</span><input id="shortcut-title" value="'+esc(item?.name||'')+'" maxlength="40" required autofocus placeholder="Название '+(type==='folder'?'папки':'сайта')+'"></label><div id="site-fields"'+(type==='folder'?' hidden':'')+'><label class="field"><span class="field-label">Адрес сайта</span><input id="shortcut-url" value="'+esc(item?.url||'')+'" placeholder="example.com" spellcheck="false"'+(type==='site'?' required':'')+'></label><div class="field"><span class="field-label">Иконка</span><div class="icon-choices"><button type="button" id="favicon-choice" class="icon-choice'+(draftMode==='favicon'?' active':'')+'">'+svg('globe')+'С сайта</button><button type="button" id="custom-choice" class="icon-choice'+(draftMode==='custom'?' active':'')+'">'+svg('image')+'Своя</button><span id="icon-preview" class="icon-preview">'+(item?iconMarkup(item):svg('globe'))+'</span></div><button type="button" id="upload-button" class="upload-zone"'+(draftMode==='custom'?'':' hidden')+'>'+svg('upload')+'<span>Загрузить изображение</span></button><input type="file" id="icon-file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml" hidden><p class="field-help">PNG, JPG, SVG или WebP, до 1 МБ.</p></div>'+(folders.length?'<label class="field"><span class="field-label">Расположение</span><select id="shortcut-folder"><option value="">Избранное</option>'+folders.map(f=>'<option value="'+esc(f.id)+'"'+(f.id===parent?' selected':'')+'>'+esc(f.name)+'</option>').join('')+'</select></label>':'')+'</div>'+(item?'<label class="field"><span class="field-label">Позиция</span><select id="shortcut-position">'+shortcutList(state.shortcuts,parent).map((s,i)=>'<option value="'+i+'"'+(i===position?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></label>':'')+'<p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions">'+(item?'<button type="button" class="btn btn-delete" id="delete-shortcut">Удалить</button>':'')+'<button type="button" class="btn btn-secondary" data-close>Отмена</button><button class="btn btn-primary" type="submit">'+(item?'Сохранить':'Добавить')+'</button></div></form>','shortcut');
    const form=$('shortcut-form');fallbackImages($('modal-root'));
    $('modal-root').querySelectorAll('[data-type]').forEach(b=>b.addEventListener('click',()=>{type=b.dataset.type;$('modal-root').querySelectorAll('[data-type]').forEach(t=>t.classList.toggle('active',t===b));$('site-fields').hidden=type==='folder';$('shortcut-url').required=type==='site';}));
    function preview() { const p=$('icon-preview');if(draftMode==='custom')setHtml(p,draftIcon?'<img src="'+esc(draftIcon)+'" alt="">':(item?.icon?svg(item.icon):svg('image')));else{try{setHtml(p,'<img class="favicon" src="'+esc(favicon(normalizedUrl($('shortcut-url').value.trim())))+'" alt="">');fallbackImages(p);}catch{setHtml(p,svg('globe'));}} }
    function chooseIcon(mode) { draftMode=mode;$('favicon-choice').classList.toggle('active',mode==='favicon');$('custom-choice').classList.toggle('active',mode==='custom');$('upload-button').hidden=mode!=='custom';preview(); }
    $('favicon-choice').addEventListener('click',()=>chooseIcon('favicon'));$('custom-choice').addEventListener('click',()=>chooseIcon('custom'));
    $('shortcut-url').addEventListener('change',preview);$('upload-button').addEventListener('click',()=>$('icon-file').click());
    $('icon-file').addEventListener('change',()=>{const file=$('icon-file').files[0];if(!file)return;if(file.size>1024*1024||!/^image\/(png|jpeg|webp|gif|svg\+xml)$/.test(file.type)){showError('Выберите изображение подходящего формата размером до 1 МБ.');return;}const reader=new FileReader();reader.onload=()=>{if($('shortcut-form')!==form)return;draftIcon=String(reader.result);chooseIcon('custom');$('form-error').hidden=true;};reader.onerror=()=>{if($('shortcut-form')===form)showError('Не удалось прочитать изображение.');};reader.readAsDataURL(file);});
    form.addEventListener('submit',e=>{
      e.preventDefault();const name=$('shortcut-title').value.trim();if(!name){showError('Укажите название.');return;}let url;
      if(type==='site'){try{url=normalizedUrl($('shortcut-url').value.trim());}catch(error){showError(error.message);return;}if(draftMode==='custom'&&!draftIcon&&!item?.icon){showError('Загрузите иконку или выберите «С сайта».');return;}}
      const folderSelect=$('shortcut-folder'),targetParent=type==='site'?(folderSelect?(folderSelect.value||null):parent):null;
      const desiredPosition=item?Number($('shortcut-position').value):null;
      if(!commit(next=>{
        let current;
        if(editId){const from=shortcutList(next.shortcuts,originalParent),index=from.findIndex(s=>s.id===editId);if(index<0||JSON.stringify(from[index])!==original)throw Error('Ярлык изменился в другой вкладке. Откройте его заново.');current=from.splice(index,1)[0];}
        const result=type==='folder'?{id:editId||uid(),type:'folder',name,children:current?.children||[]}:{id:editId||uid(),name,url,iconMode:draftMode,...(draftMode==='custom'&&draftIcon?{customIcon:draftIcon}:{}),...(item?.icon&&normalizedUrl(item.url)===url&&(!draftIcon||draftMode==='favicon')?{icon:item.icon}:{})};
        const to=shortcutList(next.shortcuts,targetParent);
        const index=editId&&targetParent===originalParent?Math.min(desiredPosition,to.length):to.length;to.splice(index,0,result);
      },true,'form-error'))return;
      closeModal();renderShortcuts();notify(item?'Изменения сохранены':type==='folder'?'Папка добавлена':'Сайт добавлен');
    });
    $('delete-shortcut')?.addEventListener('click',()=>{if(item.type==='folder'&&item.children.length){openDeleteFolder(item);return;}deleteShortcut(editId,originalParent);});
  }
  function deleteShortcut(id,parent) {
    let previous,after;
    if(!commit(next=>{previous=structuredClone(next.shortcuts);const list=shortcutList(next.shortcuts,parent),index=list.findIndex(s=>s.id===id);if(index<0)throw Error('Ярлык больше не существует.');const item=list.splice(index,1)[0];if(item.type==='folder')next.shortcuts.push(...item.children);after=JSON.stringify(next.shortcuts);},true,'form-error'))return;
    closeModal();renderShortcuts();notify('Удалено. Изменения сохранены',()=>{if(!commit(next=>{if(JSON.stringify(next.shortcuts)!==after)throw Error('После удаления избранное уже изменилось.');next.shortcuts=previous;},true))return;renderShortcuts();notify('Удаление отменено');});
  }
  function openDeleteFolder(item) { openModal(heading('Удалить папку?')+'<p class="dialog-subtitle">Сайты из «'+esc(item.name)+'» переместятся в избранное.</p><p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" data-close>Отмена</button><button class="btn btn-primary" id="confirm-delete">Удалить папку</button></div>','delete');$('confirm-delete').addEventListener('click',()=>deleteShortcut(item.id,null)); }
  function renderFolderContents() {
    const folder=state.shortcuts.find(s=>s.id===currentFolder&&s.type==='folder');if(!folder){closeModal();return;}
    $('dialog-title').textContent=folder.name;const target=$('folder-grid');
    if(target){setHtml(target,folder.children.map(s=>shortcutMarkup(s,folder.id)).join('')+addMarkup(folder.id)+(folder.children.length||editing()?'':'<p class="folder-empty">В папке пока нет сайтов</p>'));fallbackImages(target);}
  }
  function openFolder(id) {const folder=state.shortcuts.find(s=>s.id===id);if(!folder)return;openModal(heading(folder.name)+'<div class="shortcut-grid" id="folder-grid"></div><div class="dialog-actions">'+(editing()?'<button class="btn btn-secondary" id="edit-folder">Изменить папку</button>':'')+'<button class="btn btn-primary" data-close>Готово</button></div>','folder','folder-dialog');currentFolder=id;renderFolderContents();$('edit-folder')?.addEventListener('click',()=>openShortcutDialog(folder));}

  function clearDrag() {document.querySelectorAll('.drag-source,.drop-target').forEach(el=>el.classList.remove('drag-source','drop-target'));drag=null;}
  function reorder(source,target) {
    if(!editing()||!source||!target||source.id===target.id)return;
    if(!commit(next=>{
      const from=shortcutList(next.shortcuts,source.parent),item=from.find(s=>s.id===source.id),to=shortcutList(next.shortcuts,target.parent),onto=to.find(s=>s.id===target.id);
      if(!item||!onto)throw Error('Избранное изменилось. Повторите перенос.');
      if(onto.type==='folder'&&item.type!=='folder')moveShortcut(next.shortcuts,item.id,source.parent,onto.id,onto.children.length);
      else {if(source.parent!==target.parent)throw Error('Выберите папку в настройках ярлыка.');moveShortcut(next.shortcuts,item.id,source.parent,target.parent,to.indexOf(onto));}
    },true))return;
    renderShortcuts();
  }
  const holderInfo=holder=>({id:holder.dataset.holder,parent:holder.dataset.parent||null});
  document.addEventListener('dragstart',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!editing()||!e.target.closest('[data-grip]')){if(holder&&editing())e.preventDefault();return;}drag=holderInfo(holder);holder.classList.add('drag-source');e.dataTransfer?.setData('text/plain',drag.id);if(e.dataTransfer)e.dataTransfer.effectAllowed='move';});
  document.addEventListener('dragover',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!drag)return;e.preventDefault();document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'));if(holder.dataset.holder!==drag.id)holder.classList.add('drop-target');});
  document.addEventListener('drop',e=>{const holder=e.target.closest('[data-holder]');if(!holder||!drag)return;e.preventDefault();const source=drag,target=holderInfo(holder);clearDrag();reorder(source,target);});
  document.addEventListener('dragend',clearDrag);
  document.addEventListener('pointerdown',e=>{const grip=e.target.closest('[data-grip]');if(!grip||!editing()||e.pointerType==='mouse')return;e.preventDefault();touchDrag={...holderInfo(grip.closest('[data-holder]')),x:e.clientX,y:e.clientY,active:false,target:null};grip.setPointerCapture(e.pointerId);});
  document.addEventListener('pointermove',e=>{if(!touchDrag)return;const p=touchDrag;if(!p.active&&Math.hypot(e.clientX-p.x,e.clientY-p.y)<7)return;p.active=true;const holder=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-holder]');p.target=holder?holderInfo(holder):null;document.querySelectorAll('.drop-target').forEach(el=>el.classList.remove('drop-target'));if(holder&&holder.dataset.holder!==p.id)holder.classList.add('drop-target');});
  document.addEventListener('pointerup',()=>{if(!touchDrag)return;const p=touchDrag;touchDrag=null;clearDrag();suppressClick=true;setTimeout(()=>{suppressClick=false;},0);if(p.active){if(p.target)reorder(p,p.target);}else{try{const item=findItem(p.id,p.parent);if(item)openShortcutDialog(item,p.parent);}catch{}}});
  document.addEventListener('pointercancel',()=>{if(touchDrag){touchDrag=null;clearDrag();}});

  function openEngineDialog() {
    openModal(heading('Свой поисковик')+'<form id="engine-form"><label class="field"><span class="field-label">Название</span><input id="engine-title" placeholder="Например, Brave Search" maxlength="40" required autofocus></label><label class="field"><span class="field-label">Адрес поиска</span><input id="engine-url" placeholder="https://example.com/?q={query}" required spellcheck="false"><span class="field-help">Вставьте {query} вместо поискового запроса.</span></label><p id="form-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" data-close>Отмена</button><button class="btn btn-primary" type="submit">Добавить</button></div></form>','engine');
    $('engine-form').addEventListener('submit',e=>{e.preventDefault();const name=$('engine-title').value.trim(),template=$('engine-url').value.trim();if(!name||!template.includes('{query}')){showError('Укажите название и {query} в адресе поиска.');return;}let url;try{url=normalizedUrl(template.replaceAll('{query}','ARGON_QUERY')).replaceAll('ARGON_QUERY','{query}');}catch{showError('Укажите корректный адрес поиска.');return;}const engine={id:uid(),name,url};if(!commit(next=>{next.customEngines.push(engine);next.engine=engine.id;},false,'form-error'))return;renderEngine();closeModal();notify('Поисковик добавлен');});
  }
  function exportBackup() {
    try{const dump=createDump(state),url=URL.createObjectURL(new Blob([JSON.stringify(dump,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='argon-dump-'+dump.createdAt.slice(0,16).replace('T','_').replace(':','-')+'.json';document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Дамп подготовлен для скачивания');}catch(error){backupError(error.message||'Не удалось создать дамп.');}
  }
  function backupError(message) { const target=$('backup-error');if(target){target.textContent=message;target.hidden=false;}else notify(message); }
  async function readBackup(event) {
    const input=event.currentTarget,file=input.files[0];input.value='';if(!file)return;const button=$('backup-import');button.disabled=true;
    try{if(file.size>MAX_DUMP_BYTES)throw Error('Выберите дамп JSON размером до 10 МБ.');const dump=parseDump(await file.text());if(input.isConnected)openRestoreDialog(dump,file.name);}catch(error){if(input.isConnected)backupError(error.message||'Не удалось прочитать файл.');}finally{if(button.isConnected)button.disabled=false;}
  }
  function openRestoreDialog(dump,filename) {
    const stats=dumpStats(dump.state),date=new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium',timeStyle:'short'}).format(new Date(dump.createdAt));
    openModal(heading('Восстановить дамп?')+'<div class="backup-summary"><div><span>Файл</span><strong>'+esc(filename)+'</strong></div><div><span>Создан</span><strong>'+esc(date)+'</strong></div><div><span>Сайты</span><strong>'+stats.sites+'</strong></div><div><span>Папки</span><strong>'+stats.folders+'</strong></div><div><span>Свои поисковики</span><strong>'+stats.engines+'</strong></div></div><p class="restore-note">Текущие ярлыки, поисковики и настройки будут заменены данными из файла.</p><p id="backup-error" class="error" role="alert" hidden></p><div class="dialog-actions"><button class="btn btn-secondary" type="button" id="cancel-restore" autofocus>Отмена</button><button class="btn btn-primary" type="button" id="confirm-restore">Восстановить</button></div>','restore');
    $('cancel-restore').addEventListener('click',openSettings);
    $('confirm-restore').addEventListener('click',()=>{try{state=store.replace(dump.state);}catch{backupError('Не удалось сохранить дамп. Прежние данные остаются на месте.');return;}closeModal();refresh();notify('Дамп восстановлен');});
  }
  function openSettings() {
    openModal(heading('Настроить страницу')+'<span class="setting-label">Оформление</span><div class="theme-options">'+[['light','sun','Светлая'],['dark','moon','Тёмная'],['system','monitor','Системная']].map(([theme,icon,name])=>'<button class="theme-card" type="button" data-theme-choice="'+theme+'" aria-pressed="'+(state.theme===theme)+'">'+svg(icon)+name+'</button>').join('')+'</div><label class="setting-row"><span>Размер ярлыков</span><select id="density-select" aria-label="Размер ярлыков"><option value="compact">Компактный</option><option value="comfortable">Обычный</option><option value="large">Крупный</option></select></label><div class="setting-row"><span>Названия ярлыков</span><button id="labels-toggle" class="toggle" role="switch" aria-label="Показывать названия ярлыков" aria-checked="'+state.showLabels+'"></button></div><div class="setting-row"><span>Открывать в новой вкладке</span><button id="newtab-toggle" class="toggle" role="switch" aria-label="Открывать сайты в новой вкладке" aria-checked="'+state.newTab+'"></button></div><div class="backup-section"><span class="setting-label">Резервная копия</span><p class="backup-description">Ярлыки, папки, свои иконки, поисковики и оформление — в одном файле JSON.</p><div class="backup-actions"><button class="btn btn-secondary" id="backup-export" type="button">'+svg('download')+'Снять дамп</button><button class="btn btn-secondary" id="backup-import" type="button">'+svg('upload')+'Восстановить</button></div><input type="file" id="backup-file" accept=".json,application/json" hidden><p id="backup-error" class="error" role="alert" hidden></p></div><div class="dialog-actions"><button class="btn btn-primary" data-close type="button">Готово</button></div>','settings');
    $('density-select').value=state.density;
    $('density-select').addEventListener('change',e=>{if(commit(s=>{s.density=e.target.value;})){applyTheme();renderShortcuts();}else e.target.value=state.density;});
    $('labels-toggle').addEventListener('click',()=>{if(commit(s=>{s.showLabels=!s.showLabels;})){$('labels-toggle').setAttribute('aria-checked',String(state.showLabels));renderShortcuts();}});
    $('newtab-toggle').addEventListener('click',()=>{if(commit(s=>{s.newTab=!s.newTab;})){$('newtab-toggle').setAttribute('aria-checked',String(state.newTab));renderShortcuts();}});
    $('backup-export').addEventListener('click',exportBackup);$('backup-import').addEventListener('click',()=>$('backup-file').click());$('backup-file').addEventListener('change',readBackup);
  }
  $('settings-button').addEventListener('click',openSettings);
  window.addEventListener('storage',event=>{if(event.key!==storageKey&&event.key!==null)return;try{state=store.state=store.latest();refresh();}catch{notify('Не удалось прочитать настройки из другой вкладки.');}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(modalKind){closeModal();return;}if(!$('engine-menu').hidden){closeEngine();$('engine-trigger').focus();return;}if(editing()){store.setLocked(true);updateLock();renderShortcuts();return;}$('search-input').blur();}
    const typing=e.target.matches('input,textarea,select,[contenteditable=true]');
    if(((e.key==='/'&&!typing)||((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'))&&!modalKind){e.preventDefault();closeEngine();$('search-input').focus();}
    if(e.key==='Tab'&&modalKind){const controls=[...$('modal-root').querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]),select')].filter(el=>el.offsetParent!==null);const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}
  });
  refresh();
  if(store.loadError)notify('Не удалось прочитать настройки. Исходные данные сохранены; можно восстановить дамп.');
})();
