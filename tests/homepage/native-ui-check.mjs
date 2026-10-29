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
      shortcuts:[{id:'kept',name:'Мой сайт',url:'https://kept.example/',icon:'book'}]};
    await context.addInitScript(value=>{
      localStorage.setItem('argon-start-v1',JSON.stringify(value));
      const listeners=new Map();
      let release;
      const startup=new Promise(resolve=>{release=resolve;});
      const f=window.argonNativeFixture={
        calls:[],listenerCalls:[],blocked:false,newBridge:false,release:()=>release(),
        state:{revision:1,available:true,canChange:true,enabled:true,
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
    await page.locator('#search-input').fill('черновик запроса');
    await native(()=>window.argonNativeFixture.release());
    await focusCount(1);
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
    await native(()=>{
      const f=window.argonNativeFixture;
      f.emit('argon-search-box-state',{alpha:0,focused:true});
      f.newBridge=true;
      dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
    });
    await page.waitForFunction(()=>!document.querySelector('#search-form').inert);
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
    await page.locator('#engine-url').fill('https://my-search.example/?q={query}');
    await page.locator('#engine-form button[type=submit]').click();
    await page.waitForFunction(()=>window.argonNativeFixture.state.engine.name==='Мой поиск');
    assert.equal(await page.locator('#selected-engine-name').textContent(),'Мой поиск');
    const saved=await state();
    assert.equal(saved.customEngines.length,1);
    assert.equal(saved.theme,original.theme);assert.equal(saved.density,original.density);
    assert.deepEqual(saved.shortcuts,original.shortcuts);
    if(out)await page.screenshot({path:resolve(out,'home-native-search-360.png'),fullPage:true,animations:'disabled'});
  } finally {await context.close();}
}
