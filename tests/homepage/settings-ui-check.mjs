import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {openPageSettings,openShortcutSettings,openAppearanceSettings,setShortcutSize,closeDialog} from './settings-navigation.mjs';

/** Exercise the real hub, subpages and asynchronous saves under the WebUI CSP. */
export async function checkSettingsNavigation(browser,base,errors,out) {
  for(const locale of ['ru-RU','en-US']) {
    const context=await browser.newContext({locale,viewport:{width:320,height:850},acceptDownloads:true});
    try {
      await context.route('https://**/*',route=>route.abort());
      const seed={schemaVersion:2,theme:'system',engine:'google',customEngines:[],density:'comfortable',showLabels:true,newTab:false,
        shortcuts:[{id:'saved',name:'Моя закладка',url:'https://saved.example/',iconMode:'custom',icon:'book'}]};
      await context.addInitScript(value=>{if(localStorage.getItem('argon-start-v1')===null)localStorage.setItem('argon-start-v1',JSON.stringify(value));},seed);
      const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
      const wait=async(p=page)=>{await p.waitForFunction(()=>!document.body.inert);await p.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
      const state=async()=>{await wait();return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};
      await page.goto(base);await openPageSettings(page);
      const expected=locale==='ru-RU'?['Поиск','Оформление','Ярлыки','Перенос данных']:['Search','Appearance','Shortcuts','Data transfer'];
      assert.deepEqual(await page.locator('.settings-nav-list>.settings-nav-row strong').allTextContents(),expected);
      assert.equal(await page.locator('#density-select,#edit-shortcuts').count(),0,'the hub contains navigation rather than the old controls');
      assert.deepEqual(await state(),seed,'opening the hub must not write preferences');
      await page.locator('.dialog').evaluate(async dialog=>{await Promise.all(dialog.getAnimations().map(animation=>animation.finished));});
      for(const width of [320,375,768]) {
        await page.setViewportSize({width,height:850});
        const geometry=await page.locator('.settings-dialog').evaluate(dialog=>({overflow:dialog.scrollWidth>dialog.clientWidth,
          buttons:[...dialog.querySelectorAll('button')].map(button=>{const rect=button.getBoundingClientRect();return {width:rect.width,height:rect.height};})}));
        assert(!geometry.overflow,'settings must fit at '+width);
        assert(geometry.buttons.every(button=>button.width>=44&&button.height>=44),'navigation must retain 44px touch targets');
      }
      await page.setViewportSize({width:320,height:850});
      await page.locator('#appearance-settings').click();
      assert.equal(await page.locator('.settings-theme-options [data-theme-choice]').count(),4);
      await page.locator('.settings-theme-options [data-theme-choice=dark]').click();await wait();
      await page.locator('#background-settings').click();
      assert(await page.locator('#background-controls').isVisible());
      await page.locator('#background-back').click();
      assert.equal(await page.evaluate(()=>document.activeElement.id),'background-settings');
      await page.locator('#appearance-back').click();
      assert.equal(await page.evaluate(()=>document.activeElement.id),'appearance-settings');
      assert((await page.locator('#settings-appearance-summary').textContent()).includes(locale==='ru-RU'?'Тёмная':'Dark'));
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.dialog').count(),0);
      assert.equal(await page.evaluate(()=>document.activeElement.id),'settings-button');

      await openPageSettings(page);await page.locator('#search-settings').click();
      await page.locator('#search-settings-options [data-engine=bing]').click();await wait();
      assert(await page.locator('#search-settings-options').isVisible(),'selecting a provider keeps its settings open');
      assert.equal(await page.locator('#search-settings-options [data-engine=bing]').getAttribute('aria-pressed'),'true');
      await page.locator('#search-back').click();
      assert.equal(await page.locator('#settings-engine-name').textContent(),'Bing');
      assert.equal(await page.evaluate(()=>document.activeElement.id),'search-settings');

      await openShortcutSettings(page);await setShortcutSize(page,'manual');await wait();
      await page.locator('#shortcut-scale').fill('175');await page.locator('#shortcut-scale').press('Enter');await wait();
      await page.locator('#labels-toggle').click();await wait();await page.locator('#newtab-toggle').click();await wait();
      assert.equal(await page.locator('#density-select [aria-pressed=true]').count(),1);
      if(out)await page.screenshot({path:resolve(out,'home-settings-shortcuts-'+locale+'.png'),fullPage:true,animations:'disabled'});

      // Hold the same lock as a save, then leave the field and subpage before
      // the save resolves. Its completion must update the hub, not detached UI.
      await page.evaluate(()=>{
        window.settingsLock=navigator.locks.request('argon-start-v1',()=>new Promise(resolve=>{window.releaseSettingsLock=resolve;}));
      });
      await page.waitForFunction(()=>typeof window.releaseSettingsLock==='function');
      await page.locator('#shortcut-scale').fill('185');await page.locator('#shortcuts-back').click();
      assert.equal(await page.locator('.settings-nav-list>.settings-nav-row').count(),4);
      await page.evaluate(()=>window.releaseSettingsLock());await page.evaluate(()=>window.settingsLock);await wait();
      assert((await page.locator('#settings-shortcut-summary').textContent()).includes('185%'));
      assert.equal(await page.evaluate(()=>document.activeElement.id),'shortcut-settings');
      assert.deepEqual((await state()).shortcuts,seed.shortcuts);

      await page.locator('#data-settings').click();
      assert.equal(await page.locator('#backup-export,#backup-import,#bookmarks-import').count(),3);
      const download=page.waitForEvent('download');await page.locator('#backup-export').click();
      const dump=JSON.parse(await readFile(await(await download).path(),'utf8'));
      assert.equal(dump.state.shortcutScale,185);assert.equal(dump.state.showLabels,false);assert.equal(dump.state.newTab,true);
      assert.deepEqual(dump.state.shortcuts,seed.shortcuts);
      await page.locator('#data-back').click();assert.equal(await page.evaluate(()=>document.activeElement.id),'data-settings');
      await closeDialog(page);assert.equal(await page.locator('.dialog').count(),0);
      await page.reload();await openPageSettings(page);
      assert((await page.locator('#settings-shortcut-summary').textContent()).includes('185%'));

      // A second home tab updates both the main-page theme and the hub summary.
      const second=await context.newPage();second.on('pageerror',error=>errors.push(error.message));await second.goto(base);
      await openAppearanceSettings(second);await second.locator('.settings-theme-options [data-theme-choice=amoled]').click();await wait(second);
      await page.waitForFunction(()=>document.querySelector('#settings-appearance-summary').textContent.includes('AMOLED'));
      assert.equal((await state()).shortcutScale,185);
      if(out)await page.screenshot({path:resolve(out,'home-settings-hub-'+locale+'-320.png'),fullPage:true,animations:'disabled'});
    } finally {await context.close();}
  }
  console.log('PASS: settings hub in RU/EN, four sections, back/done/focus, 320/375/768px, provider selection, delayed saves, backup and live summaries');
}
