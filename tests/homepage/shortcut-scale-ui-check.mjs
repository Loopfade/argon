import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

export async function checkShortcutScale(browser,base,errors,out) {
  const context=await browser.newContext({viewport:{width:320,height:850},locale:'ru-RU',acceptDownloads:true});
  try {
    await context.route('https://**/*',route=>route.abort());
    const page=await context.newPage();
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);
    const legacy={schemaVersion:2,theme:'amoled',engine:'bing',customEngines:[],density:'compact',showLabels:true,newTab:true,
      shortcuts:[{id:'folder',name:'Работа',type:'folder',children:[{id:'docs',name:'Документы',url:'https://docs.example/',iconMode:'custom',icon:'book'}]},
        {id:'news',name:'Новости',url:'https://news.example/',iconMode:'custom',icon:'book'}]};
    await page.evaluate(state=>localStorage.setItem('argon-start-v1',JSON.stringify(state)),legacy);
    await page.reload();
    const wait=()=>page.waitForFunction(()=>!document.body.inert);
    const state=async()=>{await wait();return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};
    const settings=()=>page.locator('#settings-button').click();
    const close=()=>page.getByRole('button',{name:'Закрыть',exact:true}).click();
    await settings();
    assert.equal(await page.locator('#density-select').inputValue(),'compact');
    assert(await page.locator('#manual-shortcut-scale').isHidden());
    await page.locator('#density-select').selectOption('manual');await wait();
    assert.equal(await page.locator('#shortcut-scale').inputValue(),'100');
    await page.locator('.dialog').evaluate(async element=>{await Promise.all(element.getAnimations().map(a=>a.finished));});
    for(const width of [320,1100]) {
      await page.setViewportSize({width,height:850});
      const columns=[];
      for(const percent of [50,100,175,200]) {
        await page.locator('#shortcut-scale').fill(String(percent));
        await page.locator('#shortcut-scale').press('Enter');await wait();
        const geometry=await page.evaluate(()=>{
          const icon=document.querySelector('#shortcut-grid .shortcut-icon'),preview=document.querySelector('#size-preview .shortcut-icon');
          return {icon:icon.getBoundingClientRect().width,preview:preview.getBoundingClientRect().width,
            columns:getComputedStyle(document.querySelector('#shortcut-grid')).gridTemplateColumns.split(' ').length,
            overflow:document.documentElement.scrollWidth>innerWidth,
            dialogOverflow:document.querySelector('.dialog').scrollWidth>document.querySelector('.dialog').clientWidth,
            label:getComputedStyle(document.querySelector('#shortcut-grid .shortcut-label')).fontSize};
        });
        assert(Math.abs(geometry.icon-.6*percent)<.1);
        assert.equal(geometry.icon,geometry.preview,'preview must match the actual icons');
        assert(!geometry.overflow&&!geometry.dialogOverflow,'manual scale must fit the page and settings at '+width);
        assert(parseFloat(geometry.label)>=10,'text must remain readable at 50%');
        columns.push(geometry.columns);
        assert.equal((await state()).density,'compact','manual mode must retain the legacy fallback');
      }
      assert(columns[0]>columns.at(-1));
    }
    for(const invalid of ['49','201','100.5','']) {
      await page.locator('#shortcut-scale').fill(invalid);await page.locator('#shortcut-scale').press('Enter');
      assert(await page.locator('#shortcut-scale-error').isVisible());
      assert.equal((await state()).shortcutScale,200,'invalid input cannot replace the last valid percentage');
    }
    await page.locator('#shortcut-scale').fill('175');await page.locator('#shortcut-scale').press('Tab');await wait();
    assert.equal((await state()).shortcutScale,175,'leaving the field must save a valid percentage');
    await page.locator('#density-select').selectOption('comfortable');await wait();
    assert(await page.locator('#manual-shortcut-scale').isHidden());
    await page.locator('#density-select').selectOption('manual');await wait();
    assert.equal(await page.locator('#shortcut-scale').inputValue(),'175','manual percentage must survive preset switches');
    await close();
    await page.getByRole('button',{name:'Папка Работа',exact:true}).click();
    await page.locator('.dialog').evaluate(async element=>{await Promise.all(element.getAnimations().map(a=>a.finished));});
    assert(Math.abs(await page.locator('#folder-grid .shortcut-icon').first().evaluate(node=>node.getBoundingClientRect().width)-105)<.1);
    await close();await page.reload();await settings();
    assert.equal(await page.locator('#density-select').inputValue(),'manual');
    assert.equal(await page.locator('#shortcut-scale').inputValue(),'175');
    const expected=await state();
    await page.locator('#data-settings').click();
    const download=page.waitForEvent('download');await page.locator('#backup-export').click();
    const bytes=await readFile(await(await download).path()),dump=JSON.parse(bytes);
    assert.deepEqual(dump.state,expected);
    // Change the current value, then restore the file through the actual UI.
    await close();await settings();await page.locator('#shortcut-scale').fill('75');
    await page.locator('#shortcut-scale').press('Enter');await wait();
    await page.locator('#data-settings').click();
    await page.locator('#backup-file').setInputFiles({name:'manual.json',mimeType:'application/json',buffer:bytes});
    await page.locator('#confirm-restore').click();await wait();
    assert.deepEqual(await state(),expected);
    await page.reload();await settings();
    assert.equal(await page.locator('#shortcut-scale').inputValue(),'175');
    if(out)await page.screenshot({path:resolve(out,'home-manual-scale.png'),fullPage:true});
    // A backup produced before this feature must restore its original preset.
    await page.locator('#data-settings').click();
    await page.locator('#backup-file').setInputFiles({name:'old.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({app:'argon-start',version:1,createdAt:'2026-11-01T12:00:00Z',state:legacy}))});
    await page.locator('#confirm-restore').click();await wait();await settings();
    assert.equal(await page.locator('#density-select').inputValue(),'compact');
    assert.deepEqual(await state(),legacy);
    console.log('PASS: manual 50–200% scale, narrow layouts, folders, validation, reload, preset memory, backup and old-backup restoration');
  } finally {await context.close();}
}
