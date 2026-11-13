import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';

export async function checkHomepageRegressions(browser,base,errors,out) {
  const fixture={schemaVersion:2,theme:'amoled',engine:'google',customEngines:[],density:'comfortable',showLabels:true,newTab:false,
    shortcuts:[{id:'site',name:'Сайт',url:'https://site.example/path?q=1',iconMode:'custom',customIcon:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII='},
      {id:'work',type:'folder',name:'Работа',children:[{id:'docs',name:'Документы',url:'https://docs.example/',iconMode:'custom',icon:'book'}]},
      {id:'read',type:'folder',name:'Читать',children:[]}]};
  const contexts=[];
  const wait=async page=>{await page.waitForFunction(()=>!document.body.inert);await page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
  const state=async page=>{await wait(page);return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};
  const close=page=>page.getByRole('button',{name:'Закрыть',exact:true}).click();
  const editing=async page=>{await page.locator('#settings-button').click();await page.locator('#edit-shortcuts').click();};
  const actions=(page,id)=>page.locator('[data-item="'+id+'"]').click();
  const folder=async(page,id)=>{await actions(page,id);await page.locator('#open-folder').click();};
  async function environment() {
    const context=await browser.newContext({locale:'ru-RU',viewport:{width:360,height:850},acceptDownloads:true});contexts.push(context);
    await context.route('https://**/*',route=>route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));await page.goto(base);
    await page.evaluate(value=>localStorage.setItem('argon-start-v1',JSON.stringify(value)),fixture);await page.reload();
    return {context,page};
  }
  try {
    {
      const {page,context}=await environment();await editing(page);await actions(page,'site');await page.locator('#shortcut-edit').click();
      await page.locator('#shortcut-title').fill('Черновик сохраняется');
      const second=await context.newPage();await second.goto(base);await second.locator('#theme-trigger').click();
      await second.locator('#theme-menu [data-theme-choice=light]').click();await wait(second);
      await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
      assert.equal(await page.locator('#shortcut-title').inputValue(),'Черновик сохраняется');
      assert(await page.locator('#edit-done').isVisible());
      await page.mouse.click(5,5);assert(await page.locator('#shortcut-form').isVisible(),'outside clicks must retain an editable form');
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert.equal((await state(page)).shortcuts[0].name,'Черновик сохраняется');assert.equal((await state(page)).theme,'light');
    }
    {
      const {page}=await environment();await editing(page);await actions(page,'site');await page.locator('#shortcut-edit').click();
      await page.locator('#icon-file').setInputFiles({name:'broken.png',mimeType:'image/png',buffer:Buffer.from('not an image')});
      await page.locator('#form-error').waitFor();assert(await page.locator('#form-error').isVisible());
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert(await page.locator('#shortcut-form').isVisible());
      assert.deepEqual((await state(page)).shortcuts[0],fixture.shortcuts[0],'a corrupt image cannot replace the saved icon');
      await page.getByRole('button',{name:'Отмена',exact:true}).click();
      await page.locator('[data-add]').click();await page.locator('#shortcut-title').fill('С портом');await page.locator('#shortcut-url').fill('example.com:8443/path');
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert.equal((await state(page)).shortcuts.at(-1).url,'https://example.com:8443/path');
      await page.locator('[data-add]').click();await page.locator('[data-type=folder]').click();
      assert.equal(await page.locator('#shortcut-title').getAttribute('placeholder'),'Название папки');await close(page);
    }
    {
      const {page}=await environment();await editing(page);await folder(page,'work');
      assert.equal(await page.locator('.folder-dialog').getByRole('button',{name:'Готово',exact:true}).count(),0);
      await actions(page,'docs');await page.locator('#shortcut-edit').click();await page.locator('#shortcut-title').fill('Не сохранять');
      await page.getByRole('button',{name:'Отмена',exact:true}).click();
      assert(await page.locator('#folder-grid').isVisible());assert.equal(await page.locator('#dialog-title').textContent(),'Работа');
      await actions(page,'docs');await page.locator('#shortcut-edit').click();await page.locator('#shortcut-title').fill('Документы новые');
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert(await page.locator('#folder-grid').isVisible());
      await actions(page,'docs');await page.locator('#shortcut-move').click();await page.locator('#cancel-folder-move').click();
      assert(await page.locator('#edit-folder').isVisible(),'cancel must restore folder actions');
      await actions(page,'docs');await page.locator('#shortcut-move').click();await page.locator('#folder-move-destination').click();
      await page.locator('#move-target').selectOption('read');await page.locator('#move-destination-form button[type=submit]').click();await wait(page);
      const moved=(await state(page)).shortcuts.find(item=>item.id==='read').children[0];
      assert.deepEqual(moved,{...fixture.shortcuts[1].children[0],name:'Документы новые'});
      assert.equal(await page.locator('#dialog-title').textContent(),'Читать');
      await actions(page,'docs');await page.locator('#shortcut-move').click();await page.locator('#move-to-root').click();await wait(page);
      assert.deepEqual((await state(page)).shortcuts.find(item=>item.id==='docs'),moved);
      await actions(page,'work');await page.locator('#shortcut-move').click();
      assert(!(await page.locator('#move-hint').textContent()).includes('перенести внутрь'));await page.locator('#move-cancel').click();
      await page.locator('#edit-done').click();await page.getByRole('button',{name:'Папка Работа',exact:true}).click();
      assert.equal(await page.locator('.folder-dialog').getByRole('button',{name:'Готово',exact:true}).count(),0);
      if(out)await page.screenshot({path:resolve(out,'home-folder-actions.png'),fullPage:true,animations:'disabled'});
    }
    {
      const {page}=await environment();await page.locator('#settings-button').click();await page.locator('#density-select').selectOption('manual');await wait(page);
      await page.locator('#shortcut-scale').fill('175');await page.getByRole('button',{name:'Готово',exact:true}).click();await wait(page);
      assert.equal(await page.locator('#density-select').count(),0,'Done must close on its first click after typing');
      assert.equal((await state(page)).shortcutScale,175);
      await page.locator('#settings-button').click();await page.locator('#shortcut-scale').fill('150');
      await page.locator('#data-settings').click();assert(await page.locator('#backup-export').isVisible(),'blur save must not consume another action');
      const download=page.waitForEvent('download');await page.locator('#backup-export').click();
      const backup=JSON.parse(await readFile(await(await download).path(),'utf8'));assert.equal(backup.state.shortcutScale,150);
    }
    {
      const {page}=await environment();const broken=JSON.stringify(fixture).slice(0,-3);
      await page.evaluate(value=>localStorage.setItem('argon-start-v1',value),broken);await page.reload();
      await page.locator('#settings-button').click();await page.locator('#data-settings').click();
      assert(await page.locator('#backup-export').isDisabled());assert(await page.locator('#backup-recovery').isVisible());
      const download=page.waitForEvent('download');await page.locator('#backup-recovery').click();
      assert.equal(await readFile(await(await download).path(),'utf8'),broken);
      assert.equal(await page.evaluate(()=>localStorage.getItem('argon-start-v1')),broken);
      await page.locator('#backup-file').setInputFiles({name:'valid.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify({app:'argon-start',version:1,createdAt:'2026-11-13T00:00:00Z',state:fixture}))});
      await page.locator('#confirm-restore').click();await wait(page);assert.deepEqual(await state(page),fixture);
      await page.locator('#settings-button').click();await page.locator('#data-settings').click();assert(await page.locator('#backup-export').isEnabled());
    }
    {
      const {page}=await environment();
      assert.equal(await page.locator('.appearance button:visible').count(),1);
      const trigger=await page.locator('#theme-trigger').boundingBox();assert(trigger.width<=48&&trigger.height>=44);
      assert.equal(await page.locator('#theme-trigger-icon').getAttribute('href'),'#i-amoled');
      for(const [theme,icon] of [['light','sun'],['dark','moon'],['system','monitor'],['amoled','amoled']]) {
        await page.locator('#theme-trigger').click();assert(await page.locator('#theme-menu').isVisible());
        await page.locator('#theme-menu [data-theme-choice='+theme+']').click();await wait(page);
        assert(await page.locator('#theme-menu').isHidden());assert.equal(await page.locator('#theme-trigger-icon').getAttribute('href'),'#i-'+icon);
      }
      await page.locator('#theme-trigger').press('ArrowDown');
      assert.equal(await page.evaluate(()=>document.activeElement.dataset.themeChoice),'amoled');
      await page.keyboard.press('Home');assert.equal(await page.evaluate(()=>document.activeElement.dataset.themeChoice),'light');
      await page.keyboard.press('ArrowDown');assert.equal(await page.evaluate(()=>document.activeElement.dataset.themeChoice),'dark');
      if(out)await page.screenshot({path:resolve(out,'home-theme-menu.png'),fullPage:true,animations:'disabled'});
      await page.keyboard.press('Escape');assert(await page.locator('#theme-menu').isHidden());
      await page.locator('#theme-trigger').click();await page.mouse.click(5,200);assert(await page.locator('#theme-menu').isHidden());
      await page.reload();assert.equal(await page.locator('#theme-trigger-icon').getAttribute('href'),'#i-amoled');
      await page.locator('#engine-trigger').click();await page.locator('#add-engine').click();
      await page.locator('#engine-title').fill('Мой поиск');await page.locator('#engine-url').fill('search.example:8443/?q={query}');
      await page.locator('#engine-form button[type=submit]').click();await wait(page);
      const first=(await state(page)).customEngines[0];
      await page.locator('#engine-trigger').click();await page.locator('#add-engine').click();
      await page.locator('#engine-title').fill('Дубликат');await page.locator('#engine-url').fill(first.url);await page.locator('#engine-form button[type=submit]').click();
      await page.locator('#form-error').waitFor();assert.equal((await state(page)).customEngines.length,1);await close(page);
      await page.locator('#engine-trigger').click();await page.locator('#manage-engines').click();
      await page.locator('[data-edit-engine]').click();await page.locator('#engine-title').fill('Новое имя');await page.locator('#engine-url').fill('https://new-search.example/?q={query}');
      await page.locator('#engine-form button[type=submit]').click();await wait(page);
      assert.equal((await state(page)).customEngines[0].name,'Новое имя');assert.equal((await state(page)).customEngines[0].id,first.id);
      await page.locator('[data-delete-engine]').click();await wait(page);assert.equal((await state(page)).customEngines.length,0);assert.equal((await state(page)).engine,'google');
    }
    console.log('PASS: draft preservation, image validation, host:port, folder navigation and destinations, recovery export, first-click autosave, compact theme selector and custom search management');
  } finally {for(const context of contexts)await context.close();}
}
