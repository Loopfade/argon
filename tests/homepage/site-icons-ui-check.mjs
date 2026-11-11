/** Production chooser and image decoding with a browser-transport fixture. */
import assert from 'node:assert/strict';
import {resolve} from 'node:path';

export async function checkSiteIcons(browser, base, errors, out) {
  const context=await browser.newContext({viewport:{width:360,height:1000},locale:'ru-RU'});
  try {
    await context.route('https://**/*',route=>route.abort());
    await context.route('**/site-icons.js',route=>route.fulfill({contentType:'text/javascript',body:
      "import {discoverSiteIcons as discover} from './site-icons-production.js';\n"+
      "export function discoverSiteIcons(url,options){return discover(url,{...options,readResource:(...args)=>window.argonIconFixture.read(...args)});}"}));
    await context.addInitScript(()=>{
      const response=(body,url,mime='text/html')=>({body:btoa(body),url,mime,charset:'utf-8'});
      const png=(size,color)=>{
        const canvas=document.createElement('canvas');canvas.width=canvas.height=size;
        const ctx=canvas.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,size,size);
        return canvas.toDataURL('image/png').split(',')[1];
      };
      const images={
        'https://icons.example/assets/small.png?a=1&b=2':{mime:'image/png',body:png(32,'#f00')},
        'https://icons.example/assets/large.png':{mime:'image/png',body:png(128,'#0a0')},
        'https://icons.example/pwa/big.png':{mime:'image/png',body:png(512,'#00f')},
        'https://icons.example/assets/vector.svg':response('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="30" fill="#a4f"/></svg>','','image/svg+xml'),
        'https://icons.example/assets/broken.png':{mime:'image/png',body:btoa('broken image')},
      };
      const html='<base href="https://icons.example/assets/">'+
        '<link rel="icon" href="broken.png">'+
        '<link rel="icon" href="small.png?a=1&amp;b=2" sizes="1024x1024">'+
        '<link rel="icon" href="vector.svg">'+
        '<link rel="apple-touch-icon" href="large.png" sizes="16x16">'+
        '<link rel="manifest" href="../pwa/app.webmanifest">'+
        '<script>window.untrustedIconScriptExecuted=true;</script>'+ '<img src="https://tripwire.example/should-never-load.png">';
      const fixture=window.argonIconFixture={calls:[],hold:false,release:null,
        async read(kind,url){
          fixture.calls.push([kind,url]);
          if(kind==='page'){
            if(fixture.hold)await new Promise(resolve=>{fixture.release=resolve;});
            return response(html,url);
          }
          if(kind==='manifest')return response('{"icons":[{"src":"big.png"}]}',url,'application/manifest+json');
          return images[url]||{};
        },
      };
    });
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    const requests=[];page.on('request',request=>requests.push(request.url()));
    await page.goto(base);
    const edit=async()=>{
      await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();
      await page.locator('[data-add]').click();
      await page.locator('#shortcut-title').fill('Выбранная иконка');
      await page.locator('#shortcut-url').fill('https://chooser.example/page');
    };
    const find=async()=>{
      await page.locator('#find-site-icons').click();
      await page.waitForFunction(()=>document.querySelectorAll('[data-site-icon]').length===4);
    };
    await edit();await find();
    assert.deepEqual(await page.locator('.site-icon-option>span:not(.site-icon-check)').allTextContents(),['SVG','512 × 512','128 × 128','32 × 32']);
    assert.equal(await page.locator('[data-site-icon][aria-pressed=true]').count(),0,'Discovery must not select an icon for the user');
    assert.equal(await page.evaluate(()=>window.untrustedIconScriptExecuted),undefined);
    assert(!requests.some(url=>url.includes('tripwire.example')),'Downloaded HTML must not load subresources');
    assert(await page.evaluate(()=>window.argonIconFixture.calls.some(([,url])=>url.endsWith('small.png?a=1&b=2'))),'Decode entities in real browser');
    assert(!(await page.evaluate(()=>window.argonIconFixture.calls)).some(([,url])=>url.includes('duckduckgo.com')),'Successful discovery must not call service');
    await page.getByRole('button',{name:'Выбрать иконку SVG',exact:true}).click();
    assert.match(await page.locator('#icon-preview img').getAttribute('src'),/^data:image\/svg\+xml;base64,/);
    await page.locator('#favicon-choice').click();
    assert.equal(await page.locator('[data-site-icon][aria-pressed=true]').count(),0);
    await page.getByRole('button',{name:'Выбрать иконку 512 × 512',exact:true}).click();
    assert.match(await page.locator('#icon-preview img').getAttribute('src'),/^data:image\/png;base64,/);
    for(const width of [320,768]){
      await page.setViewportSize({width,height:1000});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Chooser must fit at '+width);
      if(out)await page.screenshot({path:resolve(out,'home-icon-chooser-'+width+'.png'),fullPage:true});
    }
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')||'null')?.shortcuts.length||0),0,'Selection stays in unsaved draft');
    await page.locator('#shortcut-form button[type=submit]').click();
    await page.locator('#shortcut-form').waitFor({state:'hidden'});
    const chosen=await page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')).shortcuts[0]);
    assert.equal(chosen.iconMode,'custom');assert.match(chosen.customIcon,/^data:image\/png;base64,/);
    await page.reload();
    await page.waitForFunction(()=>document.querySelector('.shortcut-icon img')?.naturalWidth===256);
    const restored=await page.evaluate(async()=>{
      const {createDump,parseDump}=await import('./state.js');
      const state=JSON.parse(localStorage.getItem('argon-start-v1'));
      return parseDump(JSON.stringify(createDump(state))).state.shortcuts[0].customIcon;
    });
    assert.equal(restored,chosen.customIcon,'Chosen icon must survive reload and backup');
    await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();
    await page.locator('[data-add]').click();await page.locator('#shortcut-url').fill('https://old.example/');
    await page.evaluate(()=>{window.argonIconFixture.hold=true;});
    await page.locator('#find-site-icons').click();
    await page.waitForFunction(()=>typeof window.argonIconFixture.release==='function');
    await page.locator('#shortcut-url').fill('https://new.example/');
    await page.evaluate(()=>{window.argonIconFixture.hold=false;window.argonIconFixture.release();});
    assert(await page.locator('#site-icon-results').isHidden(),'Address changes dismiss stale results');
    await page.locator('#shortcut-title').fill('Не сохранять');await find();
    await page.getByRole('button',{name:'Выбрать иконку SVG',exact:true}).click();
    await page.getByRole('button',{name:'Отмена',exact:true}).click();
    assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')).shortcuts.length),1,'Cancel must not apply selected icon');
  } finally {await context.close();}
}
