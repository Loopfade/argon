import {openPageSettings,openShortcutSettings,openAppearanceSettings,openBackgroundSettings,setShortcutSize,closeDialog} from './settings-navigation.mjs';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=','base64');
const fixture={schemaVersion:2,theme:'light',engine:'google',customEngines:[],density:'comfortable',showLabels:true,newTab:false,
  shortcuts:[{id:'site',name:'W'.repeat(40),url:'https://site.example/',iconMode:'custom',icon:'book'},
    {id:'folder',type:'folder',name:'Работа',children:[]}]};
const wait=async page=>{await page.waitForFunction(()=>!document.body.inert);await page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
const saved=async page=>{await wait(page);return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};
const close=page=>closeDialog(page);
const addEngine=async(page,name,url)=>{
  await page.locator('#engine-trigger').click();await page.locator('#add-engine').click();
  await page.locator('#engine-title').fill(name);await page.locator('#engine-url').fill(url);
  await page.locator('#engine-form button[type=submit]').click();await wait(page);
};

export async function checkHomepageConsistency(browser,base,errors,out) {
  const contexts=[];
  async function environment() {
    const context=await browser.newContext({locale:'ru-RU',viewport:{width:320,height:850}});contexts.push(context);
    await context.route('https://**/*',route=>route.abort());
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base);await page.evaluate(value=>localStorage.setItem('argon-start-v1',JSON.stringify(value)),fixture);await page.reload();
    const second=await context.newPage();second.on('pageerror',error=>errors.push(error.message));await second.goto(base);
    return {page,second};
  }
  try {
    {
      const {page,second}=await environment();
      await openShortcutSettings(page);await openShortcutSettings(second);
      await setShortcutSize(second,'manual');await wait(second);
      await second.locator('#shortcut-scale').fill('175');await second.locator('#shortcut-scale').press('Enter');await wait(second);
      await second.locator('#labels-toggle').click();await wait(second);await second.locator('#newtab-toggle').click();await wait(second);
      await page.waitForFunction(()=>document.querySelector('#newtab-toggle').getAttribute('aria-checked')==='true');
      assert.equal(await page.locator('#density-select [aria-pressed=true]').getAttribute('data-shortcut-size'),'manual');
      assert(await page.locator('#manual-shortcut-scale').isVisible());
      assert.equal(await page.locator('#shortcut-scale').inputValue(),'175');
      assert.equal(await page.locator('#labels-toggle').getAttribute('aria-checked'),'false');
      await page.locator('#shortcut-scale').fill('150');
      await openAppearanceSettings(second);await second.locator('.theme-options [data-theme-choice=dark]').click();await wait(second);
      await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
      assert.equal(await page.locator('#shortcut-scale').inputValue(),'150','unrelated changes must retain a dirty scale field');
      await page.locator('#shortcut-scale').press('Enter');await wait(page);assert.equal((await saved(page)).shortcutScale,150);
      await openShortcutSettings(second);await setShortcutSize(second,'compact');await wait(second);
      await page.waitForFunction(()=>document.querySelector('#density-select [aria-pressed=true]')?.dataset.shortcutSize==='compact');
      assert(await page.locator('#manual-shortcut-scale').isHidden());
      // A toggle must describe the current value before the user's next click.
      await page.locator('#labels-toggle').click();await wait(page);assert.equal((await saved(page)).showLabels,true);
    }
    {
      const {page,second}=await environment();
      for(const p of [page,second])await openBackgroundSettings(p);
      await second.locator('#background-file').setInputFiles({name:'background.png',mimeType:'image/png',buffer:png});
      await page.locator('#background-preview-image').waitFor({state:'visible'});
      assert(await page.locator('#background-clear').isEnabled());
      assert.equal(await page.locator('#background-current').textContent(),'Своя картинка');
      await page.locator('#background-clear').click();await wait(page);
      await second.locator('#background-preview-image').waitFor({state:'hidden'});
      assert(await second.locator('#background-clear').isDisabled());
      assert.equal(await second.locator('#background-current').textContent(),'Стандартный фон темы');
    }
    {
      const {page,second}=await environment();
      await addEngine(page,'W'.repeat(40),'https://first.example/?q={query}');
      await page.locator('#engine-trigger').click();await page.locator('#manage-engines').click();
      await addEngine(second,'Второй','https://second.example/?q={query}');
      await page.waitForFunction(()=>document.querySelectorAll('.custom-engine-row').length===2);
      await second.locator('#engine-trigger').click();await second.locator('#manage-engines').click();
      await second.locator('[data-delete-engine]').last().click();await wait(second);
      await page.waitForFunction(()=>document.querySelectorAll('.custom-engine-row').length===1);
      await close(page);await close(second);
      await addEngine(page,'Поиск в поддомене','https://{query}.example/');
      assert.equal((await saved(page)).customEngines.at(-1).url,'https://{query}.example/');
      await openPageSettings(page);await page.locator('#search-settings').click();
      assert(await page.locator('.dialog').evaluate(node=>node.scrollWidth<=node.clientWidth),
        'long custom engine names must wrap inside the narrow search chooser');
      if(out)await page.screenshot({path:resolve(out,'home-search-settings-320.png'),fullPage:true,animations:'disabled'});
    }
    {
      const {page}=await environment();
      await page.locator('#edit-bookmarks').click();
      await page.locator('[data-item=site]').click();await page.locator('#shortcut-edit').click();
      await page.locator('#shortcut-url').fill('https://changed.example/');
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert.equal((await saved(page)).shortcuts[0].icon,'book','changing the URL must retain a selected custom glyph');
      await page.locator('[data-item=site]').click();await page.locator('#shortcut-edit').click();
      await page.evaluate(()=>{
        const decode=HTMLImageElement.prototype.decode;
        HTMLImageElement.prototype.decode=function(){
          return new Promise((resolve,reject)=>{window.releaseIcon=()=>decode.call(this).then(resolve,reject);});
        };
      });
      await page.locator('#icon-file').setInputFiles({name:'slow.png',mimeType:'image/png',buffer:png});
      await page.waitForFunction(()=>typeof window.releaseIcon==='function');
      assert.equal(await page.locator('#icon-file').inputValue(),'','the same file must be selectable again');
      await page.locator('#favicon-choice').click();await page.evaluate(()=>window.releaseIcon());
      assert(await page.locator('#favicon-choice').evaluate(node=>node.classList.contains('active')),
        'a completed upload must not undo the more recent favicon choice');
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert.equal((await saved(page)).shortcuts[0].iconMode,'favicon');
      await page.reload();await page.locator('#edit-bookmarks').click();
      await page.locator('[data-add]').click();await page.locator('#shortcut-title').fill('Новая папка');
      await page.locator('#icon-file').setInputFiles({name:'broken.png',mimeType:'image/png',buffer:Buffer.from('broken')});
      await page.locator('#form-error').waitFor();await page.locator('[data-type=folder]').click();
      await page.locator('#shortcut-form button[type=submit]').click();await wait(page);
      assert.equal((await saved(page)).shortcuts.at(-1).type,'folder','an irrelevant site image error cannot block creating a folder');
    }
    {
      const {page}=await environment();await page.locator('#edit-bookmarks').click();
      await page.locator('[data-item=site]').click();await page.locator('#shortcut-move').click();
      for(const width of [320,360,768]) {
        await page.setViewportSize({width,height:850});
        const geometry=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
          buttons:[...document.querySelectorAll('#move-toolbar button')].map(node=>({left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right}))}));
        assert(!geometry.overflow,'move controls and long names must fit at '+width);
        assert(geometry.buttons.every(rect=>rect.left>=0&&rect.right<=width));
      }
      await page.setViewportSize({width:320,height:850});
      if(out)await page.screenshot({path:resolve(out,'home-move-narrow.png'),fullPage:true,animations:'disabled'});
      await page.locator('#move-cancel').click();await page.locator('#edit-done').click();
      await openPageSettings(page);await page.locator('#search-settings').click();
      await page.locator('#search-settings-options [data-engine=bing]').click();await wait(page);
      assert.equal((await saved(page)).engine,'bing');
      await openPageSettings(page);await page.locator('#search-settings').click();
      await page.locator('#settings-manage-engines').count().then(count=>assert.equal(count,0));
      await page.locator('#settings-add-engine').click();await page.getByRole('button',{name:'Отмена',exact:true}).click();
      assert(await page.locator('#search-settings-options').isVisible(),'cancelling a new engine returns to the settings chooser');
      await page.locator('#search-back').click();assert(await page.locator('#shortcut-settings').isVisible());
    }
    console.log('PASS: live settings/background/engine lists across tabs, icon upload cancellation, folder validation, search templates, narrow move controls and settings search chooser');
  } finally {for(const context of contexts)await context.close();}
}
