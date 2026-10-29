import test from 'node:test';
import assert from 'node:assert/strict';
import {parseBookmarks, MAX_BOOKMARK_BYTES} from '../../chromium_overlay/chrome/browser/resources/argon_home/bookmarks.mjs';
import {initialState, mergeBookmarks, HomepageStore, createDump, parseDump} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

const ids = () => {let n=0;return ()=>'import-'+(++n);};
const json = value => parseBookmarks(JSON.stringify(value));
const html = `<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE><H1>Bookmarks</H1><DL><p>
<DT><A HREF="https://example.org/?a=1&amp;b=2">Главная &amp; новости</A>
<DT><H3>Работа</H3><DL><p>
  <DT><A HREF='https://docs.example/'>Документы &#x1F4DA;</A>
  <DT><H3>Архив</H3><DL><DT><A HREF=https://archive.example/>Архив</A></DL>
</DL><p>
<DT><A HREF="javascript:alert(1)">Букмарклет</A>
<DT><A HREF="chrome://settings/">Настройки</A>
</DL><p>`;

test('Netscape exports retain order, decoded titles, addresses and nested folder paths',()=>{
  const parsed=parseBookmarks('\ufeff'+html);
  assert.equal(parsed.format,'HTML');assert.equal(parsed.skipped,2);
  assert.deepEqual(parsed.records.map(r=>[r.name,r.url,r.path]),[
    ['Главная & новости','https://example.org/?a=1&b=2',[]],
    ['Документы 📚','https://docs.example/',['Работа']],
    ['Архив','https://archive.example/',['Работа','Архив']],
  ]);
  const result=mergeBookmarks(initialState(),parsed,ids());
  assert.equal(result.imported,3);assert.equal(result.folders,2);
  assert.deepEqual(result.state.shortcuts.map(s=>s.name),['Главная & новости','Работа','Работа / Архив']);
  assert.deepEqual(parseDump(JSON.stringify(createDump(result.state))).state,result.state);
});

test('Chromium Bookmarks, Firefox JSON and simple bookmark lists are accepted',()=>{
  const chrome=json({version:1,roots:{bookmark_bar:{type:'folder',name:'Панель',children:[
    {type:'url',name:'Сайт',url:'https://example.com',id:'1'},
  ]},other:{type:'folder',name:'Другие',children:[]}}});
  assert.deepEqual(chrome.records,[{name:'Сайт',url:'https://example.com/',path:['Панель']}]);
  const firefox=json({root:'placesRoot',title:'',type:'text/x-moz-place-container',children:[
    {title:'Меню',type:'text/x-moz-place-container',children:[
      {title:'Firefox',type:'text/x-moz-place',uri:'https://mozilla.org/'},
      {type:'text/x-moz-place-separator'},
    ]},
  ]});
  assert.deepEqual(firefox.records,[{name:'Firefox',url:'https://mozilla.org/',path:['Меню']}]);
  assert.equal(firefox.skipped,1);
  for(const value of [[{title:'A',url:'https://a.example'}],{bookmarks:[{name:'A',url:'https://a.example'}]},
    {children:[{title:'A',url:'https://a.example'}]},{name:'A',url:'https://a.example'}]) {
    assert.deepEqual(json(value).records,[{name:'A',url:'https://a.example/',path:[]}]);
  }
});

test('HTML never executes markup, imports embedded images, or accepts non-web links',()=>{
  const parsed=parseBookmarks(`<DL><script>globalThis.bookmarkScriptRan=true;</script>
    <iframe src="https://evil.example/"></iframe><img src="https://evil.example/image">
    <DT><A HREF="https://safe.example/?x=>" onclick="alert(1)">&lt;img onerror=alert(1)&gt;</A>
    <DT><A HREF="java&#10;script:alert(1)">Bad</A><DT><A HREF="file:///secret">Bad</A>
    <DT><A HREF="data:text/html,bad">Bad</A><DT><A HREF="not a url">Bad</A></DL>`);
  assert.equal(globalThis.bookmarkScriptRan,undefined);
  assert.equal(parsed.records.length,1);assert.equal(parsed.skipped,4);
  assert.equal(parsed.records[0].name,'<img onerror=alert(1)>');
  assert.equal(parsed.records[0].url,'https://safe.example/?x=%3E');
});

test('import appends, deduplicates normalized URLs, and preserves settings and user icons',()=>{
  const current={...initialState(),theme:'amoled',engine:'noai',density:'compact',newTab:true,
    shortcuts:[{id:'import-1',name:'Мой сайт',url:'https://example.com',icon:'book'}]};
  const before=structuredClone(current);
  const result=mergeBookmarks(current,json([{title:'Duplicate',url:'https://EXAMPLE.com/'},
    {title:'B',url:'https://b.example/'},{title:'B again',url:'https://b.example/'},
    {title:'Fragment',url:'https://b.example/#section'}]),ids());
  assert.deepEqual(current,before);assert.equal(result.imported,2);assert.equal(result.duplicates,2);
  assert.deepEqual(result.state.shortcuts[0],current.shortcuts[0]);
  assert.equal(new Set(result.state.shortcuts.map(x=>x.id)).size,3);
  assert.deepEqual({...result.state,shortcuts:[]},{...current,shortcuts:[]});
  assert.equal(mergeBookmarks(result.state,json([{url:'https://b.example'}]),ids()).imported,0);
});

test('confirmation re-reads storage; quota errors leave both the saved and active state intact',()=>{
  let raw=JSON.stringify(initialState()),fail=false;
  const storage={getItem:()=>raw,setItem:(key,value)=>{if(fail)throw Error('quota');raw=value;}};
  const store=new HomepageStore(storage,'test'),parsed=json([{url:'https://new.example'}]);
  mergeBookmarks(store.state,parsed,ids()); // preview
  const other={...initialState(),theme:'amoled',shortcuts:[{id:'other',name:'Другая вкладка',url:'https://other.example'}]};
  raw=JSON.stringify(other);
  const result=store.importBookmarks(parsed,ids());
  assert.equal(result.state.theme,'amoled');assert.equal(result.state.shortcuts.length,2);
  assert.equal(store.editing,false);
  const previous=raw,active=structuredClone(store.state);fail=true;
  assert.throws(()=>store.importBookmarks(json([{url:'https://failed.example'}]),ids()),/quota/);
  assert.equal(raw,previous);assert.deepEqual(store.state,active);
  raw='{broken';fail=false;
  assert.throws(()=>store.importBookmarks(parsed,ids()));assert.equal(raw,'{broken');
});

test('malformed, compressed, oversized, over-deep and unsupported inputs fail without partial imports',()=>{
  for(const source of ['{broken','{}','[]','<p>not bookmarks</p>','<DL><A HREF="https://a.example">A</A>',
    '<DL><A HREF="https://a.example">A</A></DL><!-- unfinished',
    'mozLz40\0compressed',JSON.stringify(createDump(initialState()))])assert.throws(()=>parseBookmarks(source));
  assert.throws(()=>parseBookmarks(' '.repeat(MAX_BOOKMARK_BYTES+1)),/10 МБ/);
  assert.throws(()=>parseBookmarks('я'.repeat(MAX_BOOKMARK_BYTES/2+1)),/10 МБ/);
  let deep={url:'https://deep.example'};
  for(let i=0;i<66;i++)deep={name:'D',children:[deep]};
  assert.throws(()=>json([deep]),/уровней/);
  assert.throws(()=>parseBookmarks('<DL>'.repeat(66)+'<A HREF="https://x.example">X</A>'+'</DL>'.repeat(66)),/уровней/);
  const current=initialState();
  current.shortcuts=Array.from({length:2000},(_,i)=>({id:'old-'+i,name:'Site',url:'https://example.com/'+i}));
  assert.throws(()=>mergeBookmarks(current,json([{url:'https://extra.example'}]),ids()),/2000/);
  assert.equal(current.shortcuts.length,2000);
});

test('long names fit the existing schema without broken surrogate pairs; distinct paths remain distinct',()=>{
  const parsed=json([{name:'😀'.repeat(30),url:'https://emoji.example'},
    {name:'A / B',children:[{url:'https://one.example'}]},
    {name:'A',children:[{name:'B',children:[{url:'https://two.example'}]}]}]);
  const result=mergeBookmarks(initialState(),parsed,ids());
  assert.equal(result.shortened,1);assert.equal(result.folders,2);
  assert(result.state.shortcuts[0].name.length<=40);
  assert(!/[\uD800-\uDBFF]…$/.test(result.state.shortcuts[0].name));
  assert.notEqual(result.state.shortcuts[1].id,result.state.shortcuts[2].id);
});
