import {openBackgroundSettings,openTransferSettings,closeDialog} from './settings-navigation.mjs';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

export async function checkBackgrounds(browser,base,errors,out) {
  const context=await browser.newContext({viewport:{width:360,height:900},locale:'ru-RU',acceptDownloads:true});
  try {
    await context.route('https://**/*',route=>route.abort());
    await context.route('**/backgrounds.js',route=>route.fulfill({contentType:'text/javascript',body:[
      "import {loadBingWallpapers as load,downloadBingWallpaper as download,prepareBackground,backgroundBlob} from './backgrounds-production.js';",
      "export {prepareBackground,backgroundBlob};",
      "export const loadBingWallpapers=options=>load({...options,readResource:(...args)=>window.argonBackgroundFixture.read(...args)});",
      "export const downloadBingWallpaper=(image,options)=>download(image,{...options,readResource:(...args)=>window.argonBackgroundFixture.read(...args)});",
    ].join('\n')}));
    const original={schemaVersion:2,theme:'amoled',engine:'bing',customEngines:[],density:'compact',showLabels:true,newTab:true,
      shortcutScaleMode:'manual',shortcutScale:135,
      shortcuts:[{id:'work',name:'Работа',type:'folder',children:[{id:'docs',name:'Документы',url:'https://docs.example/',iconMode:'custom',icon:'book'}]}]};
    await context.addInitScript(seed=>{
      if(localStorage.getItem('argon-start-v1')===null)localStorage.setItem('argon-start-v1',JSON.stringify(seed));
      const canvas=document.createElement('canvas');canvas.width=320;canvas.height=180;
      const ctx=canvas.getContext('2d');ctx.fillStyle='#246d46';ctx.fillRect(0,0,320,180);
      const jpeg=canvas.toDataURL('image/jpeg').split(',')[1];
      const images=[{url:'/th?id=OHR.Forest_ROW123_1920x1080.jpg&pid=hp',title:'Лес',copyright:'Лес (© Автор фотографии)',copyrightlink:'https://www.bing.com/search?q=forest',startdate:'20261113',wp:true},
        {url:'/th?id=OHR.Restricted_ROW124_1920x1080.jpg&pid=hp',title:'Только просмотр',copyright:'© Другой автор',copyrightlink:'https://www.bing.com/search?q=landscape',startdate:'20261112',wp:false}];
      const fixture=window.argonBackgroundFixture={preview:jpeg,calls:[],fail:false,hold:'',release:null,finished:0,oversized:false,
        async read(kind,url){
          fixture.calls.push([kind,url]);
          if(fixture.hold===(kind==='manifest'?'feed':'image'))await new Promise(resolve=>{fixture.release=()=>{fixture.hold='';resolve();};});
          fixture.finished++;
          if(fixture.fail)return {};
          if(kind==='manifest')return {url,mime:'application/json',body:btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify({images}))))};
          if(fixture.oversized&&url.includes('1920x1080'))return {};
          return {url,mime:'image/jpeg',body:jpeg};
        }};
    },original);
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    const wait=async()=>{await page.waitForFunction(()=>!document.body.inert);await page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
    const state=async()=>{await wait();return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};
    const background=()=>openBackgroundSettings(page);
    const close=()=>closeDialog(page);
    const settle=()=>page.waitForFunction(()=>document.querySelector('#background-controls')?.disabled===false);
    const applied=()=>page.waitForFunction(()=>document.body.classList.contains('has-background')&&!document.querySelector('#home-background').hidden);
    await page.goto(base);
    const thumbnail=Buffer.from(await page.evaluate(()=>window.argonBackgroundFixture.preview),'base64');
    await context.route('https://www.bing.com/th?**',route=>route.fulfill({contentType:'image/jpeg',body:thumbnail}));
    await background();
    assert(await page.locator('#background-clear').isDisabled());
    assert.equal(await page.evaluate(()=>window.argonBackgroundFixture.calls.length),0,'opening settings must not contact Bing');
    // The actual browser decodes and prepares each supported local image type.
    for(const [type,extension,color] of [['image/png','png','#814c20'],['image/webp','webp','#3479aa'],['image/jpeg','jpg','#3b6a24']]) {
      const data=await page.evaluate(({type,color})=>{const canvas=document.createElement('canvas');canvas.width=480;canvas.height=320;const ctx=canvas.getContext('2d');ctx.fillStyle=color;ctx.fillRect(0,0,480,320);return canvas.toDataURL(type).split(',')[1];},{type,color});
      await page.locator('#background-file').setInputFiles({name:'own.'+extension,mimeType:type,buffer:Buffer.from(data,'base64')});
      await settle();await applied();const saved=await state();
      assert.equal(saved.background.source,'custom');assert.match(saved.background.image,/^data:image\/jpeg;base64,/);
      assert.deepEqual(saved.shortcuts,original.shortcuts);assert.equal(saved.shortcutScale,135);
    }
    const previous=await state();
    await page.locator('#background-file').setInputFiles({name:'bad.png',mimeType:'image/png',buffer:Buffer.from('not an image')});
    await settle();assert(await page.locator('#background-error').isVisible());assert.deepEqual(await state(),previous);
    await page.locator('#background-file').setInputFiles({name:'too-big.jpg',mimeType:'image/jpeg',buffer:Buffer.alloc(10*1024*1024+1)});
    await settle();assert(await page.locator('#background-error').isVisible());assert.deepEqual(await state(),previous);
    await page.evaluate(()=>window.argonBackgroundFixture.fail=true);await page.locator('#background-bing').click();await settle();
    assert(await page.locator('#background-error').isVisible());assert.deepEqual(await state(),previous);
    await page.evaluate(()=>window.argonBackgroundFixture.fail=false);await page.locator('#background-bing').click();await settle();
    assert.equal(await page.locator('.bing-card').count(),2);
    await page.waitForFunction(()=>[...document.querySelectorAll('.bing-card img')].every(image=>image.naturalWidth>0));
    assert(await page.locator('[data-bing-set="1"]').isDisabled());assert(await page.locator('[data-bing-download="1"]').isDisabled());
    await page.evaluate(()=>window.argonBackgroundFixture.oversized=true);
    await page.locator('[data-bing-set="0"]').click();await settle();await applied();
    const chosen=await state();assert.equal(chosen.background.source,'bing');assert.equal(chosen.background.credit,'Лес (© Автор фотографии)');
    assert.equal(await page.locator('#background-credit').textContent(),chosen.background.credit);
    const calls=await page.evaluate(()=>window.argonBackgroundFixture.calls);
    assert(calls.some(([kind,url])=>kind==='image'&&url.includes('1280x720')),'oversized Bing images must fall back to the smaller rendition');
    for(const width of [320,1024]) {
      await page.setViewportSize({width,height:900});
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      assert(await page.locator('.dialog').evaluate(node=>node.scrollWidth<=node.clientWidth));
      if(out)await page.screenshot({path:resolve(out,'home-background-bing-'+width+'.png'),fullPage:true,animations:'disabled'});
    }
    const imageDownload=page.waitForEvent('download');await page.locator('[data-bing-download="0"]').click();await settle();
    const download=await imageDownload,bytes=await readFile(await download.path());
    assert.equal(download.suggestedFilename(),'bing-20261113.jpg');assert.equal(bytes[0],0xff);assert.equal(bytes[1],0xd8);
    assert.deepEqual(await state(),chosen,'downloading a wallpaper must not change homepage settings');
    await page.locator('#background-back').click();await openTransferSettings(page);
    const backupDownload=page.waitForEvent('download');await page.locator('#backup-export').click();
    const backup=await readFile(await(await backupDownload).path());assert.deepEqual(JSON.parse(backup).state,chosen);
    await close();await background();await page.locator('#background-clear').click();await wait();
    assert.equal((await state()).background,undefined);assert(await page.locator('#home-background').isHidden());
    await page.locator('#background-back').click();await openTransferSettings(page);
    await page.locator('#backup-file').setInputFiles({name:'with-background.json',mimeType:'application/json',buffer:backup});
    await page.locator('#confirm-restore').click();await wait();await applied();assert.deepEqual(await state(),chosen);
    await page.reload();await applied();assert.deepEqual(await state(),chosen);
    assert.equal(await page.evaluate(()=>window.argonBackgroundFixture.calls.length),0,'a restored image must work offline without refetching Bing');
    // Leaving a chooser must ignore both delayed feed and delayed image results.
    await background();await page.evaluate(()=>window.argonBackgroundFixture.hold='feed');
    await page.locator('#background-bing').click();await page.waitForFunction(()=>typeof window.argonBackgroundFixture.release==='function');
    await page.locator('#background-back').click();await page.evaluate(()=>window.argonBackgroundFixture.release());
    await page.waitForFunction(()=>window.argonBackgroundFixture.finished===1);
    assert(await page.locator('.settings-theme-options').isVisible());assert.deepEqual(await state(),chosen);
    await page.locator('#background-settings').click();await page.locator('#background-bing').click();await settle();
    await page.evaluate(()=>{window.argonBackgroundFixture.hold='image';window.argonBackgroundFixture.release=null;});
    await page.locator('[data-bing-set="0"]').click();await page.waitForFunction(()=>typeof window.argonBackgroundFixture.release==='function');
    await page.locator('#background-back').click();await page.evaluate(()=>window.argonBackgroundFixture.release());
    await page.waitForFunction(()=>window.argonBackgroundFixture.finished===3);assert.deepEqual(await state(),chosen);
    await openTransferSettings(page);
    const legacy={...original};delete legacy.shortcutScale;delete legacy.shortcutScaleMode;
    await page.locator('#backup-file').setInputFiles({name:'legacy.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({app:'argon-start',version:1,createdAt:'2026-11-01T00:00:00Z',state:legacy}))});
    await page.locator('#confirm-restore').click();await wait();assert.deepEqual(await state(),legacy);
    assert(await page.locator('#home-background').isHidden());assert(await page.locator('#background-credit').isHidden());
    console.log('PASS: custom PNG/JPEG/WebP backgrounds, Bing gallery/attribution/download/fallback, cancellation, offline reload, full backup restoration and old backups');
  } finally {await context.close();}
}
