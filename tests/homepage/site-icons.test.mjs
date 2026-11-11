import test from 'node:test';
import assert from 'node:assert/strict';
import {pageIconCandidates, manifestIconCandidates, rankIcons, discoverSiteIcons,
  MAX_ICON_CANDIDATES} from '../../chromium_overlay/chrome/browser/resources/argon_home/site-icons.mjs';

const resource = (body, url, mime='text/html') => ({body:Buffer.from(body).toString('base64'),url,mime,charset:'utf-8'});
const decode = value => value.replaceAll('&amp;','&').replaceAll('&#47;','/');

test('discovers declared links with base URL, entity decoding, exact rel tokens and first attributes',()=>{
  const result=pageIconCandidates(`
    <base href="https://cdn.example/assets/">
    <LINK REL="shortcut ICON" href="logo.png?a=1&amp;b=2" sizes="32x32">
    <link rel=apple-touch-icon href='../touch.png'>
    <link rel=mask-icon href='mask.svg'>
    <link rel=icon href='logo.png?a=1&amp;b=2'>
    <link rel=icon href='first.png' href='second.png'>
    <link rel=manifest href='app.webmanifest'>`, 'https://site.example/path', decode);
  assert.deepEqual(result, {icons:[
    {url:'https://cdn.example/assets/logo.png?a=1&b=2',kind:'site',hint:32},
    {url:'https://cdn.example/touch.png',kind:'touch'},
    {url:'https://cdn.example/assets/first.png',kind:'site'},
  ],manifest:'https://cdn.example/assets/app.webmanifest'});
});

test('does not discover link-shaped text in scripts, comments, styles, templates or quoted attributes',()=>{
  const result=pageIconCandidates(`
    <!-- <link rel=icon href=comment.png> -->
    <script>const s='<link rel=icon href=script.png>';</script>
    <style>.x{content:'<link rel=icon href=style.png>'}</style>
    <title><link rel=icon href=title.png></title>
    <template><template><link rel=icon href=template.png></template></template>
    <div data-x="<link rel=icon href=attribute.png>"></div>
    <link data-x='a > b' rel=icon href=real.png>
  `,'https://example.com/path');
  assert.deepEqual(result.icons,[{url:'https://example.com/real.png',kind:'site'}]);
});

test('rejects credentials and active/local schemes but preserves image data URLs and bounds candidates',()=>{
  const data='data:image/svg+xml,%3Csvg%20xmlns=%22http://www.w3.org/2000/svg%22/%3E';
  const bad=['javascript:alert(1)','file:///x.png','chrome://settings','blob:https://x/a','data:text/html,x','https://user:pass@example.com/a.png'];
  const html=[...bad,data].map(href=>`<link rel=icon href='${href}'>`).join('');
  assert.deepEqual(pageIconCandidates(html,'https://example.com/').icons,[{url:data,kind:'site'}]);
  assert.deepEqual(pageIconCandidates(html,'file:///private'),{icons:[],manifest:''});
  assert.equal(pageIconCandidates(Array.from({length:100},(_,i)=>`<link rel=icon href='/${i}.png'>`).join(''),'https://example.com/').icons.length,64);
});

test('manifest URLs resolve against the manifest location and monochrome-only icons are excluded',()=>{
  assert.deepEqual(manifestIconCandidates(JSON.stringify({icons:[
    {src:'512.png',purpose:'any maskable'},{src:'../192.png',purpose:'maskable'},
    {src:'mono.svg',purpose:'monochrome'},{src:'javascript:alert(1)'},{src:'512.png'},null,
  ]}),'https://cdn.example/pwa/app.webmanifest'),[
    {url:'https://cdn.example/pwa/512.png',kind:'manifest'},
    {url:'https://cdn.example/192.png',kind:'manifest'},
  ]);
  assert.deepEqual(manifestIconCandidates('{broken','https://example.com/manifest.json'),[]);
});

test('ranks suitable shapes, vectors and real resolution without trusting declared sizes',()=>{
  const icons=[
    {url:'strip',width:1024,height:32}, {url:'small',width:32,height:32},
    {url:'large',width:512,height:512}, {url:'vector',width:64,height:64,vector:true},
    {url:'bad',width:0,height:64}, {url:'medium',width:128,height:128},
  ];
  assert.deepEqual(rankIcons(icons).map(icon=>icon.url),['vector','large','medium','small','strip']);
});

test('tries every candidate after a broken first image, measures actual sizes and only uses the service as last resort',async()=>{
  const calls=[];
  const readResource=async(kind,url)=>{
    calls.push([kind,url]);
    if(kind==='page')return resource('<link rel=icon href="broken.png"><link rel=icon href="small.png" sizes="1024x1024"><link rel=apple-touch-icon href="large.png" sizes="16x16"><link rel=manifest href="app.json">','https://redirect.example/final/page');
    if(kind==='manifest')return resource('{"icons":[{"src":"pwa.png"}]}','https://cdn.example/pwa/app.json','application/manifest+json');
    return {url};
  };
  let active=0,peak=0;
  const loadImage=async candidate=>{
    active++;peak=Math.max(peak,active);
    await new Promise(resolve=>setImmediate(resolve));active--;
    const size=candidate.url.endsWith('small.png')?32:candidate.url.endsWith('large.png')?128:candidate.url.endsWith('pwa.png')?512:0;
    return size?{...candidate,width:size,height:size}:null;
  };
  const icons=await discoverSiteIcons('https://original.example/',{readResource,loadImage});
  assert.deepEqual(icons.map(icon=>icon.width),[512,128,32]);
  assert.equal(peak,3);
  assert(calls.some(([,url])=>url==='https://redirect.example/favicon.ico'));
  assert(calls.some(([,url])=>url==='https://cdn.example/pwa/pwa.png'));
  assert(!calls.some(([,url])=>url.includes('icons.duckduckgo.com')));
});

test('keeps root and service fallbacks when HTML is unavailable and sends only the hostname to the service',async()=>{
  const calls=[];
  const icons=await discoverSiteIcons('https://example.com/private/path?token=secret',{
    readResource:async(kind,url)=>{calls.push([kind,url]);throw Error('offline');},
    loadImage:async candidate=>candidate.kind==='service'?{...candidate,width:32,height:32}:null,
  });
  assert.equal(icons[0].url,'https://icons.duckduckgo.com/ip3/example.com.ico');
  assert.deepEqual(calls.map(([,url])=>url),[
    'https://example.com/private/path?token=secret','https://example.com/favicon.ico','https://icons.duckduckgo.com/ip3/example.com.ico',
  ]);
});

test('aborted searches cannot publish late results or start another stage',async()=>{
  let release;const pending=new Promise(resolve=>{release=resolve;});
  const controller=new AbortController(),calls=[];
  const result=discoverSiteIcons('https://example.com/',{signal:controller.signal,
    readResource:async(kind,url)=>{calls.push([kind,url]);return pending;},loadImage:async()=>assert.fail('late image load'),
  });
  controller.abort();release(resource('<link rel=icon href=x.png>','https://example.com/'));
  await assert.rejects(result,{name:'AbortError'});
  assert.equal(calls.length,1);
});

test('bounded downloads retain large manifest and late declared icons after many small entries',async()=>{
  const images=[];
  await discoverSiteIcons('https://example.com/',{
    readResource:async(kind,url)=>{
      if(kind==='page')return resource(Array.from({length:20},(_,i)=>`<link rel=icon href='/${i}.png' sizes='16x16'>`).join('')+
        '<link rel=icon href="/large.png" sizes="256x256"><link rel=manifest href="/app.json">','https://example.com/');
      if(kind==='manifest')return resource('{"icons":[{"src":"pwa.png","sizes":"512x512"}]}',url,'application/manifest+json');
      images.push(url);return {};
    },
    loadImage:async candidate=>({...candidate,width:16,height:16}),
  });
  assert.equal(images.length,MAX_ICON_CANDIDATES);
  assert(images.includes('https://example.com/large.png'));
  assert(images.includes('https://example.com/pwa.png'));
  assert(images.includes('https://example.com/favicon.ico'));
});

test('equal-quality results keep document order regardless of decode completion and identical image data is deduplicated',async()=>{
  const icons=await discoverSiteIcons('https://example.com/',{
    readResource:async kind=>kind==='page'?resource('<link rel=icon href=a.png><link rel=icon href=b.png><link rel=icon href=copy.png>','https://example.com/'): {},
    loadImage:async candidate=>{
      if(candidate.kind==='default')return null;
      if(candidate.url.endsWith('a.png'))await new Promise(resolve=>setImmediate(resolve));
      return {...candidate,data:candidate.url.endsWith('b.png')?'b':'a',width:64,height:64};
    },
  });
  assert.deepEqual(icons.map(icon=>icon.url),['https://example.com/a.png','https://example.com/b.png']);
});
