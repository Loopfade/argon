/** Exercise the production page with its Trusted Types policy and real DOM events. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdir} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname} from 'node:path';

const {chromium}=await import(pathToFileURL(process.env.ARGON_PLAYWRIGHT_MODULE).href);
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../../chromium_overlay/chrome/browser/resources/argon_home');
const out=process.env.ARGON_UI_ARTIFACTS;
if(out)await mkdir(out,{recursive:true});
const files={'/':'index.html','/app.css':'app.css','/app.js':'app.js','/state.js':'state.mjs','/icons.js':'icons.mjs','/i18n.js':'i18n.js','/theme.js':'theme.js'};
const server=createServer(async(req,res)=>{
  const name=files[req.url?.split('?')[0]];
  if(!name){res.writeHead(404);res.end();return;}
  // Match WebUIDataSourceImpl: unrecognized extensions (including .mjs) are HTML.
  const path=req.url?.split('?')[0]||'/';
  const type=path.endsWith('.css')?'text/css':path.endsWith('.js')?'text/javascript':'text/html';
  res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https: http:; connect-src 'none'; frame-src 'none'; trusted-types argon-home; require-trusted-types-for 'script'"});
  let data=await readFile(resolve(root,name),'utf8');
  if(name==='index.html') {
    const language=/^ru(?:[-_,;]|$)/i.test(req.headers['accept-language']||'')?'ru':'en';
    data=data.replace('<html lang="ru"','<html lang="'+language+'"');
  }
  res.end(data);
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base='http://127.0.0.1:'+server.address().port+'/';
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1100,height:850},acceptDownloads:true,locale:'ru-RU'});
const iconRequests=[],allImageRequests=[],imageFailures=[],browserErrors=[];
context.on('request',r=>{if(r.resourceType()==='image')allImageRequests.push(r.url());});
context.on('page',p=>{
  p.on('requestfailed',r=>{if(r.resourceType()==='image')imageFailures.push({url:r.url(),error:r.failure()?.errorText});});
  p.on('console',m=>{if(m.type()==='error')browserErrors.push(m.text());});
});
const iconPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=','base64');
async function mockPage(){
  const p=await context.newPage(),cdp=await context.newCDPSession(p);
  // Playwright 1.56 silently aborts every /favicon.ico URL before context.route,
  // including <img> requests. Fetch provides real image responses for the exact
  // production URLs without changing the application to suit that test quirk.
  cdp.on('Fetch.requestPaused',async({requestId,request,resourceType})=>{
    try {
      const url=new URL(request.url);
      const fulfill=(contentType,body)=>cdp.send('Fetch.fulfillRequest',{requestId,responseCode:200,responseHeaders:[{name:'content-type',value:contentType}],body:Buffer.from(body).toString('base64')});
      if(resourceType==='Document'){await fulfill('text/html','<p>Navigation test</p>');return;}
      iconRequests.push(url.href);
      if(url.href==='https://fixture-icon.example/check.png'||url.href==='https://primary-icon.example/favicon.ico'||url.href==='https://icons.duckduckgo.com/ip3/fallback-icon.example.ico')await fulfill('image/png',iconPng);
      else await cdp.send('Fetch.failRequest',{requestId,errorReason:'Failed'});
    } catch(error) {if(!p.isClosed())browserErrors.push('Image mock: '+error.message);}
  });
  await cdp.send('Fetch.enable',{patterns:[{urlPattern:'https://*',requestStage:'Request'}]});
  await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
  return p;
}
const page=await mockPage(),errors=[];
page.on('pageerror',error=>errors.push(error.message));
const saved=()=>page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));
const formSubmit=()=>page.locator('#shortcut-form button[type=submit]').click();
async function enableEditing(p=page){await p.locator('#settings-button').click();await p.locator('#edit-shortcuts').click();assert(await p.locator('#edit-done').isVisible());}
async function editFirst(p){await p.locator('[data-item]').first().click();await p.locator('#shortcut-edit').click();}
async function actions(name,p=page){await p.getByRole('button',{name:'Действия с ярлыком '+name,exact:true}).click();}
async function add(name,url,type='site',deferFrame=false){
  if(deferFrame)await page.evaluate(()=>{
    window.argonOriginalFrame=window.requestAnimationFrame;
    window.argonDeferredFrames=[];
    window.requestAnimationFrame=callback=>window.argonDeferredFrames.push(callback);
  });
  await page.getByRole('button',{name:'Добавить сайт или папку',exact:true}).click();
  if(type==='folder')await page.locator('[data-type=folder]').click();
  await page.getByLabel('Название',{exact:true}).fill(name);
  if(deferFrame){
    await page.locator('#shortcut-url').focus();
    await page.evaluate(()=>{
      window.requestAnimationFrame=window.argonOriginalFrame;
      const frames=window.argonDeferredFrames;
      delete window.argonOriginalFrame;delete window.argonDeferredFrames;
      for(const callback of frames)callback(performance.now());
    });
    assert.equal(await page.evaluate(()=>document.activeElement?.id),'shortcut-url','opening a dialog must not steal focus back after the user selects the address field');
  }
  if(type==='site')await page.locator('#shortcut-url').fill(url);
  await formSubmit();
  await page.locator('#shortcut-form').waitFor({state:'hidden'});
}
try {
  for(const locale of ['en-US','de-DE']) {
    const english=await browser.newContext({locale,acceptDownloads:true});
    const p=await english.newPage();
    p.on('pageerror',error=>errors.push(error.message));
    await p.goto(base);
    await p.getByRole('button',{name:'Customize',exact:true}).waitFor();
    assert.equal(await p.locator('html').getAttribute('lang'),'en');
    assert.equal(await p.locator('#search-input').getAttribute('placeholder'),'Search or enter address');
    await p.locator('#settings-button').click();
    await p.getByRole('button',{name:'Edit shortcuts',exact:false}).click();
    await p.getByRole('button',{name:'Add site or folder',exact:true}).click();
    await p.getByLabel('Name',{exact:true}).fill('Избранное');
    await p.getByLabel('Site address',{exact:true}).fill('example.com');
    await p.locator('#shortcut-form button[type=submit]').click();
    await p.locator('[data-item]').first().waitFor();
    assert.equal(await p.locator('[data-item] .shortcut-label').first().textContent(),'Избранное');
    await p.locator('#edit-done').click();
    await p.locator('#settings-button').click();
    assert(await p.getByRole('button',{name:'Export backup',exact:false}).isVisible());
    const download=p.waitForEvent('download');
    await p.locator('#backup-export').click();
    assert((await download).suggestedFilename().startsWith('argon-dump-'));
    if(out)await p.screenshot({path:resolve(out,'homepage-'+locale+'.png'),fullPage:true});
    await english.close();
  }
  // Real image responses, service fallback, total failure, and switching back
  // from an uploaded image. These used to be hidden by aborting every image.
  const iconPage=await mockPage();
  iconPage.on('pageerror',error=>errors.push(error.message));
  await iconPage.goto(base);
  await iconPage.evaluate(()=>new Promise((resolve,reject)=>{
    const image=new Image();
    const timer=setTimeout(()=>reject(Error('Image fixture timed out')),5000);
    image.onload=()=>{clearTimeout(timer);resolve();};
    image.onerror=()=>{clearTimeout(timer);reject(Error('Image fixture did not decode'));};
    image.src='https://fixture-icon.example/check.png';
  }));
  await enableEditing(iconPage);
  await iconPage.locator('[data-add]').click();
  await iconPage.locator('#shortcut-title').fill('Иконка');
  await iconPage.locator('#shortcut-url').fill('primary-icon.example/private/path');
  await iconPage.waitForFunction(()=>document.querySelector('#icon-preview img')?.naturalWidth===1);
  assert.equal(await iconPage.evaluate(()=>document.activeElement.id),'shortcut-url','icon preview should update while the address is still focused');
  assert.equal(await iconPage.locator('#icon-preview img').getAttribute('src'),'https://primary-icon.example/favicon.ico');
  await iconPage.locator('#custom-choice').click();
  await iconPage.locator('#icon-file').setInputFiles({name:'icon.png',mimeType:'image/png',buffer:iconPng});
  await iconPage.locator('#icon-preview img[src^="data:"]').waitFor();
  await iconPage.locator('#favicon-choice').click();
  await iconPage.waitForFunction(()=>document.querySelector('#icon-preview img')?.naturalWidth===1);
  await iconPage.locator('#shortcut-form button[type=submit]').click();
  let iconState=await iconPage.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));
  assert.equal(iconState.shortcuts[0].iconMode,'favicon');
  assert.equal(iconState.shortcuts[0].customIcon,undefined);
  await editFirst(iconPage);
  await iconPage.locator('#shortcut-url').fill('fallback-icon.example');
  await iconPage.waitForFunction(()=>document.querySelector('#icon-preview img')?.src==='https://icons.duckduckgo.com/ip3/fallback-icon.example.ico'&&document.querySelector('#icon-preview img')?.naturalWidth===1);
  assert(iconRequests.indexOf('https://fallback-icon.example/favicon.ico')<iconRequests.indexOf('https://icons.duckduckgo.com/ip3/fallback-icon.example.ico'));
  await iconPage.locator('#shortcut-form button[type=submit]').click();
  await iconPage.reload();
  await iconPage.waitForFunction(()=>document.querySelector('.shortcut-icon img')?.naturalWidth===1);
  assert.equal(await iconPage.locator('.shortcut-icon img').getAttribute('src'),'https://icons.duckduckgo.com/ip3/fallback-icon.example.ico');
  await enableEditing(iconPage);
  await editFirst(iconPage);
  await iconPage.locator('#shortcut-url').fill('missing-icon.example');
  await iconPage.waitForFunction(()=>document.querySelector('#icon-preview use')?.getAttribute('href')==='#i-globe');
  assert(iconRequests.includes('https://icons.duckduckgo.com/ip3/missing-icon.example.ico'));
  // Closing while a preview is debounced must not touch the next dialog.
  await iconPage.locator('#shortcut-url').fill('primary-icon.example');
  await iconPage.getByRole('button',{name:'Отмена',exact:true}).click();
  await iconPage.locator('#settings-button').click();
  await iconPage.waitForTimeout(300);
  assert(await iconPage.locator('#density-select').isVisible());
  await iconPage.evaluate(()=>localStorage.clear());
  await iconPage.close();
  await page.goto(base);
  assert(await page.locator('#edit-done').isHidden());
  assert.equal(await page.locator('[data-add]').count(),0);
  assert.equal(await page.locator('[data-item]').count(),0);
  if(out)await page.screenshot({path:resolve(out,'home-empty.png'),fullPage:true});
  await enableEditing();
  await add('Мой сайт','example.com');
  await add('Документы','docs.example.com','site',true);
  await add('Работа',null,'folder');
  let data=await saved();assert.equal(data.shortcuts.length,3);
  assert.equal(await page.locator('[data-edit],[data-grip],#edit-lock').count(),0);
  await actions('Документы');await page.locator('#shortcut-move').click();
  await page.keyboard.press('Escape');assert(await page.locator('#move-toolbar').isHidden());assert(await page.locator('#edit-done').isVisible());
  const site=data.shortcuts[0],folder=data.shortcuts[2];
  await page.evaluate(()=>{window.argonDragTrace=[];for(const type of ['dragstart','dragover','drop','dragend'])document.addEventListener(type,e=>{if(window.argonDragTrace.length<30)window.argonDragTrace.push({type,id:e.target.closest('[data-holder]')?.dataset.holder});},true);});
  await actions('Мой сайт');await page.locator('#shortcut-move').click();
  await page.locator('[data-holder="'+site.id+'"] [data-item]').dragTo(page.locator('[data-holder="'+folder.id+'"] .shortcut-icon'));
  data=await saved();assert.equal(data.shortcuts.length,2,'drag trace: '+JSON.stringify(await page.evaluate(()=>window.argonDragTrace))+'; errors: '+JSON.stringify(errors));assert.equal(data.shortcuts[1].children[0].id,site.id);
  await actions('Работа');await page.locator('#open-folder').click();
  await actions('Мой сайт');await page.locator('#shortcut-edit').click();
  await page.locator('#shortcut-folder').selectOption('');
  await page.getByLabel('Название',{exact:true}).fill('Личный сайт');
  await formSubmit();
  data=await saved();assert.equal(data.shortcuts.length,3);assert.equal(data.shortcuts[1].children.length,0);
  assert.equal(data.shortcuts[2].name,'Личный сайт');
  await actions('Документы');await page.locator('#shortcut-edit').click();
  await page.getByRole('button',{name:'Удалить',exact:true}).click();
  assert.equal((await saved()).shortcuts.length,2);
  await page.locator('#toast').getByRole('button',{name:'Отменить'}).click();
  assert.equal((await saved()).shortcuts.length,3);
  await actions('Личный сайт');await page.locator('#shortcut-move').click();
  await page.getByRole('button',{name:'Переместить: Документы',exact:true}).click();
  assert.equal((await saved()).shortcuts[0].name,'Личный сайт');
  await page.locator('#edit-done').click();
  assert.equal(await page.locator('[data-edit],[data-grip],[data-add]').count(),0);
  await page.reload();assert.equal((await saved()).shortcuts.length,3);
  assert(await page.locator('#edit-done').isHidden());
  await page.getByRole('link',{name:'Личный сайт',exact:true}).click({button:'right'});
  assert.equal(await page.getByRole('dialog').count(),0);
  await page.locator('[data-theme-choice=dark]').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
  await page.locator('[data-theme-choice=amoled]').click();
  assert.equal(await page.locator('html').getAttribute('data-theme'),'amoled');
  await page.waitForFunction(()=>getComputedStyle(document.body).backgroundColor==='rgb(0, 0, 0)');
  assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(0, 0, 0)');
  assert.equal((await saved()).theme,'amoled');
  await page.reload();
  assert.equal(await page.locator('[data-theme-choice=amoled]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('html').getAttribute('data-theme'),'amoled');
  assert.equal(await page.locator('[data-engine=yandex]').count(),0);
  const googleIcon=await page.locator('[data-engine=google] img').getAttribute('src');
  assert.equal(await page.locator('#selected-engine-icon img').getAttribute('src'),googleIcon);
  await page.locator('#engine-trigger').click();await page.locator('[data-engine=noai]').click();
  await page.locator('#settings-button').click();await page.locator('#density-select').selectOption('compact');
  // The opening animation scales the entire dialog. Measure settled geometry,
  // otherwise getBoundingClientRect() reports a transient, smaller sample.
  await page.locator('.dialog').evaluate(async dialog=>{await Promise.all(dialog.getAnimations().map(animation=>animation.finished));});
  for(const width of [1100,320]){
    await page.setViewportSize({width,height:850});
    const sizes={};
    for(const size of ['comfortable','compact']){
      await page.locator('#density-select').selectOption(size);
      sizes[size]=await page.evaluate(()=>{
        const real=document.querySelector('#shortcut-grid .shortcut-icon'),sample=document.querySelector('#size-preview .shortcut-icon');
        return {icon:real.getBoundingClientRect().width,preview:sample.getBoundingClientRect().width,columns:getComputedStyle(document.querySelector('#shortcut-grid')).gridTemplateColumns.split(' ').length};
      });
      assert.equal(sizes[size].icon,sizes[size].preview,'settings preview must use the real icon size');
    }
    assert(sizes.compact.icon<sizes.comfortable.icon);
    assert(sizes.compact.columns>sizes.comfortable.columns,'small icons must fit more columns at '+width);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(out)await page.screenshot({path:resolve(out,'home-size-preview-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1100,height:850});
  const downloadPromise=page.waitForEvent('download');await page.getByRole('button',{name:'Снять дамп'}).click();
  const dump=JSON.parse(await readFile(await(await downloadPromise).path(),'utf8'));
  assert.equal(dump.state.engine,'noai');assert.equal(dump.state.density,'compact');assert.equal(dump.state.shortcuts.length,3);
  await page.locator('#backup-file').setInputFiles({name:'argon.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(dump))});
  await page.locator('#confirm-restore').click();
  assert(await page.locator('#edit-done').isHidden());
  assert.equal((await saved()).shortcuts.length,3);
  if(out)await page.screenshot({path:resolve(out,'home-dark.png'),fullPage:true});
  await enableEditing();
  await page.getByRole('button',{name:'Добавить сайт или папку',exact:true}).click();
  await page.locator('#shortcut-title').fill('Не сохранять');await page.locator('#shortcut-url').fill('failed.example');
  await page.evaluate(()=>{window.argonOriginalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('Storage full','QuotaExceededError');};});
  await formSubmit();assert(await page.locator('#form-error').isVisible());assert.equal((await saved()).shortcuts.length,3);
  await page.evaluate(()=>{Storage.prototype.setItem=window.argonOriginalSetItem;delete window.argonOriginalSetItem;});
  await page.getByRole('button',{name:'Закрыть',exact:true}).click();
  for(const width of [320,375,768]){
    await page.setViewportSize({width,height:820});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'horizontal page overflow at '+width);
    if(out)await page.screenshot({path:resolve(out,'home-edit-'+width+'.png'),fullPage:true});
  }
  await page.locator('#search-input').fill('example.com/path');
  const address=page.waitForRequest(r=>r.isNavigationRequest()&&r.url()==='https://example.com/path');
  await page.locator('#search-form button[type=submit]').click();await address;
  await page.goto(base);await page.locator('#search-input').fill('тест поиска');
  const search=page.waitForRequest(r=>r.isNavigationRequest()&&r.url().startsWith('https://noai.duckduckgo.com/?q='));
  await page.locator('#search-form button[type=submit]').click();await search;
  assert.deepEqual(errors,[]);
  console.log('PASS: production homepage under Trusted Types; icons, fallback, live size preview, settings-only editing, tap/drag moves, folders, rename, undo, persistence, themes, dump, quota, narrow screens and search');
} catch(error) {
  const active=context.pages().find(p=>p.url().startsWith(base))||page;
  console.error(JSON.stringify({pageErrors:errors,iconRequests,allImageRequests,imageFailures,browserErrors:browserErrors.slice(-20),preview:await active.evaluate(async()=>({markup:document.querySelector('#icon-preview')?.outerHTML,address:document.querySelector('#shortcut-url')?.value,protocol:location.protocol,sources:(await import('./icons.js')).faviconSources('https://primary-icon.example/private/path',location.protocol)})).catch(()=>null)}));
  if(out)await active.screenshot({path:resolve(out,'failure.png'),fullPage:true}).catch(()=>{});
  throw error;
} finally {await browser.close();await new Promise(r=>server.close(r));}
