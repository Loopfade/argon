import assert from 'node:assert/strict';
import {resolve} from 'node:path';

const fixture={schemaVersion:2,theme:'light',engine:'google',customEngines:[],density:'comfortable',showLabels:true,newTab:false,
  shortcuts:[{id:'folder',type:'folder',name:'Работа',children:[]}]};
const fragment=(url,title)=>'#add-site?url='+encodeURIComponent(url)+'&title='+encodeURIComponent(title);
const wait=async page=>{await page.waitForFunction(()=>!document.body.inert);await page.evaluate(()=>navigator.locks.request('argon-start-v1',()=>{}));};
const read=async page=>{await wait(page);return page.evaluate(()=>JSON.parse(localStorage.getItem('argon-start-v1')));};

export async function checkAddShortcut(browser,base,errors,out) {
  const context=await browser.newContext({locale:'ru-RU',viewport:{width:320,height:850}});
  await context.route('https://**/*',route=>route.abort());
  const page=await context.newPage();
  const home=await context.newPage();
  for(const p of [page,home])p.on('pageerror',error=>errors.push(error.message));
  const open=async(url,title='Страница')=>{
    // The native command creates a new document, even if another home tab exists.
    await page.goto('about:blank');await page.goto(base+fragment(url,title));
    await page.locator('#shortcut-form').waitFor();
  };
  const submit=async()=>{await page.locator('#shortcut-form button[type=submit]').click();await wait(page);};
  try {
    await home.goto(base);
    await home.evaluate(value=>localStorage.setItem('argon-start-v1',JSON.stringify(value)),fixture);
    await home.reload();
    const address='https://example.com/article?q=a%26b&next=%23anchor#part?title=other';
    const title='Статья & "кавычки" <img src=x>';
    await open(address,title);
    assert.equal(await page.locator('#dialog-title').textContent(),'Добавить на главную Argon');
    assert.equal(await page.locator('#shortcut-title').inputValue(),title);
    assert.equal(await page.locator('#shortcut-url').inputValue(),address);
    assert.equal(await page.locator('#shortcut-form [data-type]').count(),0);
    assert.equal(page.url(),base,'draft parameters must be consumed before any save');
    assert(await page.locator('#edit-done').isHidden(),'adding a browser draft must not unlock grid editing');
    assert.deepEqual(await read(page),fixture,'opening a draft must not write to storage');
    assert.equal(await page.locator('#shortcut-title img').count(),0);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if(out)await page.screenshot({path:resolve(out,'home-add-from-site-320.png'),fullPage:true});
    await page.getByRole('button',{name:'Отмена',exact:true}).click();
    assert.deepEqual(await read(page),fixture);
    await page.reload();assert.equal(await page.locator('#shortcut-form').count(),0,'cancelled requests must not return on reload');

    await open(address);
    await page.locator('#shortcut-folder').selectOption('folder');
    // An unrelated change from another home tab must survive the explicit save.
    await home.locator('#settings-button').click();await home.locator('#labels-toggle').click();await wait(home);
    await page.waitForFunction(()=>document.querySelector('#shortcut-title')?.value==='Страница');
    await submit();await page.locator('#shortcut-form').waitFor({state:'hidden'});
    assert(await page.locator('#edit-done').isHidden());
    assert.equal((await read(page)).showLabels,false);
    assert.equal((await read(page)).shortcuts[0].children[0].url,address);
    await home.getByRole('button',{name:'Закрыть',exact:true}).click();
    await home.locator('[data-item=folder]').click();
    await home.locator('a[data-item]').waitFor();
    assert.equal(await home.locator('a[data-item]').getAttribute('href'),address,'another home tab must receive the saved shortcut');
    await page.reload();assert.equal(await page.locator('#shortcut-form').count(),0,'saved requests must not reopen on reload');

    await open(address,'Повтор');await submit();
    assert.match(await page.locator('#form-error').textContent(),/Этот адрес уже добавлен/);
    assert.equal((await read(page)).shortcuts[0].children.length,1,'duplicates must be found inside folders');
    assert.equal(await page.locator('#shortcut-title').inputValue(),'Повтор');

    await open('https://example.com/another','Новая страница');
    await page.evaluate(()=>{window.argonSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('Full','QuotaExceededError');};});
    await submit();assert(await page.locator('#form-error').isVisible());
    assert.equal(await page.locator('#shortcut-title').inputValue(),'Новая страница');
    assert.equal((await read(page)).shortcuts.length,1);
    await page.evaluate(()=>{Storage.prototype.setItem=window.argonSetItem;delete window.argonSetItem;});
    await submit();await page.locator('#shortcut-form').waitFor({state:'hidden'});
    assert.equal((await read(page)).shortcuts.length,2);

    // Invalid requests never open an editor or alter the stored tree.
    const before=await read(page);
    await page.goto('about:blank');await page.goto(base+fragment('javascript:alert(1)','Invalid'));
    await page.locator('#toast').waitFor({state:'visible'});
    assert.equal(await page.locator('#shortcut-form').count(),0);
    assert.equal(page.url(),base);assert.deepEqual(await read(page),before);
  } finally {await context.close();}
  const english=await browser.newContext({locale:'en-US',viewport:{width:768,height:850}});
  try {
    await english.route('https://**/*',route=>route.abort());
    const page=await english.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+fragment('https://example.com/','English page'));
    await page.locator('#shortcut-form').waitFor();
    assert.equal(await page.locator('#dialog-title').textContent(),'Add to Argon home');
    assert(await page.locator('#edit-done').isHidden());
    if(out)await page.screenshot({path:resolve(out,'home-add-from-site-tablet.png'),fullPage:true});
  } finally {await english.close();}
  console.log('PASS: add from site, explicit save, cancellation/reload, folders, duplicates, concurrent settings, quota, safe title/URL and localization');
}
