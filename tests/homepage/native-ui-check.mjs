/** Production UI and connector with a transport fixture; no Android toolbar is simulated. */
import assert from 'node:assert/strict';
import {resolve} from 'node:path';

export async function checkNativeSearch(browser, base, errors, out) {
  const context=await browser.newContext({viewport:{width:360,height:820},locale:'ru-RU'});
  try {
    await context.route('https://**/*',route=>route.abort());
    await context.route('**/browser-search.js',route=>route.fulfill({
      contentType:'text/javascript',
      body:[
        "import {connectBrowserSearch as connect} from './browser-search-production.js';",
        "export function connectBrowserSearch(onState,onAlpha) {",
        "  return connect(onState,onAlpha,globalThis.argonNativeFixture.bindings);",
        "}",
      ].join('\n'),
    }));
    const original={schemaVersion:2,theme:'amoled',engine:'duck',customEngines:[],
      showLabels:true,newTab:true,density:'compact',
      background:{source:'custom',image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII='},
      shortcuts:[{id:'kept',name:'Мой сайт',url:'https://kept.example/',icon:'book'}]};
    await context.addInitScript(value=>{
      localStorage.setItem('argon-start-v1',JSON.stringify(value));
      const listeners=new Map();
      let release;
      const startup=new Promise(resolve=>{release=resolve;});
      const f=window.argonNativeFixture={
        calls:[],listenerCalls:[],readyFrames:[],blocked:false,newBridge:false,release:()=>release(),
        state:{revision:1,available:true,tablet:false,canChange:true,enabled:true,
          engine:{id:'google',name:'Google',url:'https://www.google.com/search?q=example'}},
        emit(name,value){listeners.get(name)?.(value);},
        update(patch){
          f.state={...f.state,...patch,revision:f.state.revision+1};
          f.emit('argon-search-state-changed',structuredClone(f.state));
        },
      };
      f.bindings={
        addWebUiListener(name,callback){f.listenerCalls.push(name);listeners.set(name,callback);},
        send(name,args){
          f.calls.push([name,...args]);
          if(name==='argonHomeReady')f.readyFrames.push({
            provider:document.querySelector('#selected-engine-name').textContent,
            theme:document.documentElement.dataset.theme,
            visibility:getComputedStyle(document.body).visibility,
            background:!document.querySelector('#home-background').hidden,
            themeIcon:document.querySelector('#theme-trigger-icon').getAttribute('href'),
          });
          if(name==='setArgonSearchBoxBounds'&&f.newBridge){
            f.newBridge=false;
            f.emit('argon-search-box-state',{alpha:1,focused:false});
          }
        },
        async sendWithPromise(name,...args){
          f.calls.push([name,...args]);
          if(name==='getArgonSearchState'){await startup;return structuredClone(f.state);}
          if(name==='focusArgonOmnibox'){
            if(f.blocked)return false;
            f.emit('argon-search-box-state',{alpha:0,focused:true});
            return true;
          }
          if(name==='setArgonSearchEngine'){
            if(f.blocked||!f.state.canChange)throw Error('Managed or blocked provider');
            const engine=args[0];
            const id=['google','bing','duck','noai'].includes(engine.id)?engine.id:'browser:current';
            f.update({engine:{id,name:engine.name,url:engine.url.replaceAll('{query}','example')}});
            return structuredClone(f.state);
          }
          throw Error('Unexpected native message: '+name);
        },
      };
    },original);
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    const native=fn=>page.evaluate(fn);
    const state=()=>native(()=>JSON.parse(localStorage.getItem('argon-start-v1')));
    const restore=()=>native(()=>window.argonNativeFixture.emit(
      'argon-search-box-state',{alpha:1,focused:false}));
    const focusCount=number=>page.waitForFunction(count=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='focusArgonOmnibox').length>=count,number);
    await page.goto(base);
    assert.equal(await page.locator('body').evaluate(node=>getComputedStyle(node).visibility),'hidden',
      'Saved DuckDuckGo provider must not be painted before the browser replies with Google');
    // A draft can also arrive through restoration/autofill before the bridge.
    await native(()=>{document.querySelector('#search-input').value='черновик запроса';});
    await native(()=>window.argonNativeFixture.release());
    await focusCount(1);
    await page.waitForFunction(()=>window.argonNativeFixture.readyFrames.length===1);
    assert.deepEqual(await native(()=>window.argonNativeFixture.readyFrames[0]),
      {provider:'Google',theme:'amoled',visibility:'visible',background:true,themeIcon:'#i-amoled'},
      'First native frame acknowledgement must follow provider, theme, image decoding and theme button configuration');
    assert.equal(await page.locator('#search-input').evaluate(node=>node.tagName),'BUTTON');
    assert.equal(await native(()=>window.argonNativeFixture.calls
      .find(call=>call[0]==='focusArgonOmnibox')[1]),'черновик запроса');
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Google');
    assert.deepEqual(await state(),original,'Native provider updates must not rewrite homepage data');
    assert.deepEqual((await native(()=>window.argonNativeFixture.listenerCalls)).sort(),
      ['argon-search-box-state','argon-search-state-changed'],'Startup and pageshow must share one connector');

    await restore();
    await page.locator('#search-input').click();
    await focusCount(2);
    assert(await page.locator('#search-form').evaluate(node=>node.inert));
    await native(()=>window.argonNativeFixture.emit('argon-search-box-state',{alpha:1,focused:true}));
    assert.equal(await page.locator('#search-form').evaluate(node=>getComputedStyle(node).opacity),'0',
      'Animation completion cannot reveal a second search box while native input is focused');
    const calls=await native(()=>window.argonNativeFixture.calls);
    const lastFocus=calls.findLastIndex(call=>call[0]==='focusArgonOmnibox');
    assert.equal(calls[lastFocus-1][0],'setArgonSearchBoxBounds','Native focus needs fresh measured bounds');
    assert.equal(page.url(),base,'Clicking the fakebox must not navigate the renderer');
    await restore();
    await page.locator('#search-form button[type=submit]').click();
    await focusCount(3);
    await restore();
    await page.keyboard.press('Control+k');
    await focusCount(4);
    await restore();

    // Chromium routes taps through the transparent fakebox after scrolling it into the toolbar.
    await native(()=>window.argonNativeFixture.emit('argon-search-box-state',{alpha:0,focused:false}));
    assert.equal(await page.locator('#search-form').evaluate(node=>node.inert),false);
    await page.locator('#search-input').click();
    await focusCount(5);
    await restore();

    const boundsBefore=await native(()=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='setArgonSearchBoxBounds').length);
    const readyBefore=await native(()=>window.argonNativeFixture.readyFrames.length);
    await native(()=>{
      const f=window.argonNativeFixture;
      f.emit('argon-search-box-state',{alpha:0,focused:true});
      f.newBridge=true;
      dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
    });
    await page.waitForFunction(()=>!document.querySelector('#search-form').inert);
    await page.waitForFunction(count=>window.argonNativeFixture.readyFrames.length>count,readyBefore);
    assert((await native(()=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='setArgonSearchBoxBounds').length))>boundsBefore,
      'Restored document must resend unchanged geometry to a new adapter');

    await page.locator('#settings-button').click();
    await page.locator('#edit-shortcuts').click();
    await page.locator('[data-item="kept"]').click();
    await page.locator('#shortcut-edit').click();
    await page.locator('#shortcut-title').fill('Несохранённое название');
    await native(()=>window.argonNativeFixture.update({
      engine:{id:'bing',name:'Bing',url:'https://www.bing.com/search?q=example'}}));
    assert.equal(await page.locator('#shortcut-title').inputValue(),'Несохранённое название',
      'External browser provider changes must not close or rewrite a shortcut draft');
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Bing');
    assert.deepEqual(await state(),original);
    await page.keyboard.press('Escape');
    await page.locator('#edit-done').click();

    await page.locator('#engine-trigger').click();
    await page.locator('[data-engine="noai"]').click();
    await page.waitForFunction(()=>window.argonNativeFixture.state.engine.id==='noai'
      &&document.querySelector('#search-form').inert);
    assert(await native(()=>window.argonNativeFixture.calls.some(
      call=>call[0]==='setArgonSearchEngine'&&call[1].id==='noai')));
    await restore();

    await native(()=>{window.argonNativeFixture.blocked=true;});
    await page.locator('#search-input').click();
    await page.getByText('Не удалось открыть адресную строку. Повторите нажатие.',{exact:true}).waitFor();
    assert.equal(await page.locator('#search-form').evaluate(node=>node.inert),false);
    await native(()=>{window.argonNativeFixture.blocked=false;});
    await native(()=>window.argonNativeFixture.update({canChange:false}));
    await page.locator('#engine-trigger').click();
    assert(await page.locator('#engine-menu button').evaluateAll(nodes=>nodes.every(node=>node.disabled)));
    assert(await page.getByText('Поисковик задан политикой или расширением.',{exact:true}).isVisible());
    await page.keyboard.press('Escape');
    await native(()=>window.argonNativeFixture.update({canChange:true}));

    await page.locator('#engine-trigger').click();
    await page.locator('#add-engine').click();
    await page.locator('#engine-title').fill('Мой поиск');
    await page.locator('#engine-url').fill('https://www.google.com/search?q={query}&udm=14');
    const beforeQuota=await state();
    const selectionCount=await native(()=>window.argonNativeFixture.calls.filter(call=>call[0]==='setArgonSearchEngine').length);
    await native(()=>{
      const setItem=Storage.prototype.setItem;
      Storage.prototype.setItem=function(key,value){
        if(window.failHomepageWrite&&key==='argon-start-v1')throw new DOMException('Test quota','QuotaExceededError');
        return setItem.call(this,key,value);
      };
      window.failHomepageWrite=true;
    });
    await page.locator('#engine-form button[type=submit]').click();await page.locator('#form-error').waitFor();
    assert.deepEqual(await state(),beforeQuota);
    assert.equal(await native(()=>window.argonNativeFixture.calls.filter(call=>call[0]==='setArgonSearchEngine').length),selectionCount,
      'Failed homepage persistence must not change the browser provider');
    await native(()=>{window.failHomepageWrite=false;});
    await page.locator('#engine-form button[type=submit]').click();
    await page.waitForFunction(()=>window.argonNativeFixture.state.engine.name==='Мой поиск');
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Мой поиск');
    const saved=await state();
    assert.equal(saved.customEngines.length,1);
    assert.equal(saved.theme,original.theme);assert.equal(saved.density,original.density);
    assert.deepEqual(saved.shortcuts,original.shortcuts);
    assert.deepEqual(saved.background,original.background);
    // Chromium classifies alternative Google URLs as Google, not browser:current.
    await native(()=>{const f=window.argonNativeFixture;f.update({engine:{...f.state.engine,id:'google'}});});
    await page.locator('#engine-trigger').click();await page.locator('#manage-engines').click();
    await page.locator('[data-edit-engine]').click();await page.locator('#engine-title').fill('Новое имя');
    await native(()=>{window.argonNativeFixture.blocked=true;});
    await page.locator('#engine-form button[type=submit]').click();await page.locator('#form-error').waitFor();
    assert.deepEqual(await state(),saved,'a native rejection must retain the homepage engine and draft');
    assert.equal(await page.locator('#engine-title').inputValue(),'Новое имя');
    await native(()=>{window.argonNativeFixture.blocked=false;});
    await page.locator('#engine-form button[type=submit]').click();await page.locator('[data-edit-engine]').waitFor();
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Новое имя');
    assert.equal((await state()).customEngines[0].id,saved.customEngines[0].id);
    await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    await native(()=>window.argonNativeFixture.update({canChange:false}));
    await page.locator('#engine-trigger').click();assert(await page.locator('#manage-engines').isDisabled());
    await page.keyboard.press('Escape');await native(()=>window.argonNativeFixture.update({canChange:true}));
    await page.locator('#engine-trigger').click();await page.locator('#manage-engines').click();
    const beforeDelete=await state();
    const deleteSelectionCount=await native(()=>window.argonNativeFixture.calls.filter(call=>call[0]==='setArgonSearchEngine').length);
    await native(()=>{window.failHomepageWrite=true;});
    await page.locator('[data-delete-engine]').click();await page.locator('#engines-error').waitFor();
    assert.deepEqual(await state(),beforeDelete);
    assert.equal(await native(()=>window.argonNativeFixture.calls.filter(call=>call[0]==='setArgonSearchEngine').length),deleteSelectionCount);
    await native(()=>{window.failHomepageWrite=false;});
    await page.locator('[data-delete-engine]').click();await page.waitForFunction(()=>window.argonNativeFixture.state.engine.id==='google');
    await page.waitForFunction(()=>!document.body.inert);
    assert.equal((await state()).customEngines.length,0);assert.equal((await state()).engine,'google');
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Google');
    await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    // Tablet is identified by native Chromium, not by CSS viewport width.
    await page.setViewportSize({width:1024,height:768});
    await native(()=>window.argonNativeFixture.update({tablet:true}));
    await page.locator('html.native-tablet-search').waitFor();
    assert.equal(await page.locator('.search-wrap').isVisible(),false,
      'Tablet must show only the Chromium omnibox');
    assert(await page.locator('#shortcut-grid').isVisible(),
      'Favorites remain on the tablet home page');
    assert(await page.locator('#settings-button').isVisible(),
      'Homepage settings remain visible');
    await page.locator('#settings-button').click();await page.locator('#search-settings').click();
    assert(await page.locator('#search-settings-options').isVisible(),
      'Search providers must remain reachable when the tablet fakebox is hidden');
    await native(()=>window.argonNativeFixture.update({canChange:false}));
    assert(await page.locator('#search-settings-options button').evaluateAll(nodes=>nodes.every(node=>node.disabled)));
    await native(()=>window.argonNativeFixture.update({canChange:true}));
    if(out)await page.screenshot({path:resolve(out,'home-search-settings-tablet.png'),fullPage:true,animations:'disabled'});
    await page.locator('#search-settings-options [data-engine=bing]').click();
    await page.waitForFunction(()=>window.argonNativeFixture.state.engine.id==='bing');await restore();
    await page.locator('#settings-button').click();await page.locator('#search-settings').click();
    await page.locator('#settings-add-engine').click();
    await page.locator('#engine-title').fill('Поиск планшета');await page.locator('#engine-url').fill('https://tablet.example/?q={query}');
    await page.locator('#engine-form button[type=submit]').click();await page.locator('#search-settings-options').waitFor();
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Поиск планшета');
    await page.locator('#settings-manage-engines').click();await page.locator('[data-delete-engine]').click();
    await page.waitForFunction(()=>window.argonNativeFixture.state.engine.id==='google');
    await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    assert(await page.locator('#search-settings-options').isVisible());
    await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    await page.getByRole('button',{name:'Закрыть',exact:true}).click();
    const tabletBounds=await native(()=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='setArgonSearchBoxBounds').length);
    const tabletFocus=await native(()=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='focusArgonOmnibox').length);
    await page.keyboard.press('Control+k');
    await focusCount(tabletFocus+1);
    await page.setViewportSize({width:1200,height:760});
    await page.waitForTimeout(100);
    assert.equal(await native(()=>window.argonNativeFixture.calls
      .filter(call=>call[0]==='setArgonSearchBoxBounds').length),tabletBounds,
      'Hidden tablet fakebox must not send geometry on focus or resize');
    await native(()=>window.argonNativeFixture.update({tablet:false}));
    await page.locator('html:not(.native-tablet-search)').waitFor();
    assert(await page.locator('.search-wrap').isVisible(),
      'Phone home page must retain its central search');
    if(out)await page.screenshot({path:resolve(out,'home-native-search-360.png'),fullPage:true,animations:'disabled'});
  } finally {await context.close();}
}
