import test from 'node:test';
import assert from 'node:assert/strict';
import {parseBingWallpapers,loadBingWallpapers,backgroundBlob} from '../../chromium_overlay/chrome/browser/resources/argon_home/backgrounds.mjs';
import {initialState,createDump,parseDump,HomepageStore} from '../../chromium_overlay/chrome/browser/resources/argon_home/state.mjs';

const image={url:'/th?id=OHR.Landscape_ROW123_1920x1080.jpg&pid=hp',title:'Пейзаж',copyright:'Пейзаж (© Автор)',copyrightlink:'https://www.bing.com/search?q=landscape',startdate:'20261113',wp:true};
const background={source:'custom',image:'data:image/jpeg;base64,aGVsbG8='};

test('Bing metadata preserves attribution and only accepts Bing image endpoints',()=>{
  const pictures=parseBingWallpapers({images:[null,42,image,image,{...image,url:'https://foreign.example/th?id=OHR.X.jpg'},{...image,url:'javascript:alert(1)'},{...image,url:'/search?q=x'}]});
  assert.equal(pictures.length,1);
  assert.equal(pictures[0].credit,image.copyright);
  assert.equal(pictures[0].creditUrl,image.copyrightlink);
  assert.equal(new URL(pictures[0].thumbnail).searchParams.get('id'),'OHR.Landscape_ROW123_640x360.jpg');
  assert.equal(pictures[0].downloadable,true);
  assert.equal(parseBingWallpapers({images:[{...image,wp:false}]})[0].downloadable,false);
  assert.throws(()=>parseBingWallpapers({}));
});

test('Bing feed uses the existing bounded homepage transport on explicit request',async()=>{
  const calls=[];
  const pictures=await loadBingWallpapers({readResource:async(kind,url)=>{
    calls.push([kind,url]);return {url,mime:'application/json',body:Buffer.from(JSON.stringify({images:[image]})).toString('base64')};
  }});
  assert.equal(calls.length,1);assert.equal(calls[0][0],'manifest');
  assert.equal(new URL(calls[0][1]).pathname,'/HPImageArchive.aspx');
  assert.equal(pictures.length,1);
  const controller=new AbortController();controller.abort();
  await assert.rejects(loadBingWallpapers({signal:controller.signal,readResource:async()=>{assert.fail('an aborted chooser must not start a request');}}),/Aborted/);
  await assert.rejects(loadBingWallpapers({readResource:async()=>({url:'https://foreign.example/feed',mime:'application/json',body:btoa('{}')})}));
});

test('background, attribution and manual scale round-trip in a backup',()=>{
  for(const selected of [background,{...background,source:'bing',credit:image.copyright,creditUrl:image.copyrightlink}]) {
    const state={...initialState(),background:selected,shortcutScaleMode:'manual',shortcutScale:175};
    assert.deepEqual(parseDump(JSON.stringify(createDump(state))).state,state);
  }
  assert.deepEqual(parseDump(JSON.stringify(createDump(initialState()))).state,initialState());
});

test('invalid background data cannot overwrite the existing homepage',()=>{
  const saved=JSON.stringify({...initialState(),background}),writes=[];
  const store=new HomepageStore({getItem:()=>saved,setItem:(_,v)=>writes.push(v)},'home');
  for(const value of [null,{...background,image:'https://remote.example/photo.jpg'},{...background,image:'data:text/html;base64,aGVsbG8='},{...background,image:'data:image/jpeg;base64,'+'a'.repeat(2_000_000)},{...background,source:'bing',credit:'Автор',creditUrl:'javascript:alert(1)'}])
    assert.throws(()=>store.replace({...initialState(),background:value}));
  assert.deepEqual(writes,[]);
  assert.equal(backgroundBlob(background.image).type,'image/jpeg');
  assert.throws(()=>backgroundBlob('data:text/html;base64,aGVsbG8='));
});
