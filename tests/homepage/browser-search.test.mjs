import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserSearch, connectBrowserSearch} from '../../chromium_overlay/chrome/browser/resources/argon_home/browser-search.mjs';

const state = (revision=1, id='google', extra={}) => ({revision,available:true,canChange:true,
  enabled:true,engine:{id,name:id,url:'https://'+id+'.example/?q=example'},...extra});
function fixture(request=async()=>state()) {
  const listeners=new Map(),requests=[],sent=[],updates=[],alphas=[];
  const bindings={addWebUiListener:(name,callback)=>listeners.set(name,callback),
    sendWithPromise:async(name,...args)=>{requests.push([name,...args]);return request(name,...args);},
    send:(name,args)=>sent.push([name,args])};
  const search=new BrowserSearch(bindings,value=>updates.push(value),(value,focused)=>alphas.push([value,focused]));
  return {search,bindings,listeners,requests,sent,updates,alphas};
}

test('the current browser provider wins over stale responses and survives an external change',async()=>{
  let reply;
  const f=fixture(()=>new Promise(resolve=>{reply=resolve;}));
  const refresh=f.search.refresh();
  f.listeners.get('argon-search-state-changed')(state(3,'duck'));
  reply(state(2,'google'));await refresh;
  assert.equal(f.search.engine.id,'duck');assert.equal(f.updates.length,1);
  f.listeners.get('argon-search-state-changed')(state(4,'bing'));
  assert.equal(f.search.engine.id,'bing');
});

test('choosing a page preset or custom engine changes the browser through the same API',async()=>{
  const f=fixture((name,engine)=>state(2,engine?.id||'google'));
  f.search.update(state());
  for(const engine of [{id:'noai',name:'DuckDuckGo NoAI',url:'https://noai.duckduckgo.com/?q={query}'},
    {id:'custom',name:'Мой поиск',url:'https://my.example/?q={query}'}]) {
    await f.search.select(engine);
    assert.deepEqual(f.requests.at(-1),['setArgonSearchEngine',engine]);
    assert.equal(f.search.engine.id,engine.id);
  }
});

test('managed or unavailable providers cannot send a mutation',async()=>{
  for(const extra of [{canChange:false},{available:false}]) {
    const f=fixture();f.search.update(state(1,'google',extra));
    await assert.rejects(f.search.select({id:'bing'}));
    assert.deepEqual(f.requests,[]);
  }
});

test('native focus preserves a draft; a failed focus is reported and previews never call it',async()=>{
  const f=fixture(name=>name==='focusArgonOmnibox');
  assert.equal(await f.search.focus(),false);assert.deepEqual(f.requests,[]);
  f.search.update(state());
  assert.equal(await f.search.focus('пример запроса'),true);
  assert.deepEqual(f.requests.at(-1),['focusArgonOmnibox','пример запроса']);
  const failed=fixture(async()=>false);failed.search.update(state());
  assert.equal(await failed.search.focus(),false);
});

test('only measured, finite bounds are sent; repeated layout notifications do not flood native',async()=>{
  const f=fixture(),rect={left:12,top:100,width:300,height:56};
  f.search.bounds(rect,0);assert.deepEqual(f.sent,[]);
  f.search.update(state());f.search.bounds(rect,0);f.search.bounds(rect,0);
  assert.deepEqual(f.sent,[['setArgonSearchBoxBounds',[12,100,300,56,0]]]);
  f.search.bounds({...rect,top:50},50);
  assert.deepEqual(f.sent.at(-1)[1],[12,50,300,56,.5]);
  f.search.bounds({...rect,top:-10},110);assert.equal(f.sent.at(-1)[1][4],1);
  const count=f.sent.length;
  f.search.bounds({...rect,width:0},0);f.search.bounds({...rect,top:NaN},0);
  assert.equal(f.sent.length,count);
  await f.search.refresh();f.search.bounds({...rect,top:-10},110);
  assert.equal(f.sent.length,count+1,'restoring the same layout must reattach the native adapter');
});

test('native alpha is bounded and malformed state cannot replace a valid selection',()=>{
  const f=fixture();f.search.update(state());
  for(const value of [null,{},state(-1,'bing'),state(2,'bing',{engine:{}})])f.search.update(value);
  assert.equal(f.search.engine.id,'google');
  const alpha=f.listeners.get('argon-search-box-state');
  for(const value of [{alpha:0,focused:false},{alpha:1,focused:true},
    {alpha:2,focused:false},{alpha:-1,focused:true},{alpha:NaN,focused:false},0,null])alpha(value);
  assert.deepEqual(f.alphas,[[0,false],[1,true],[1,false],[0,true]]);
});

test('the injected transport exercises the production connector without exposing native APIs to previews',async()=>{
  const f=fixture();
  const connected=await connectBrowserSearch(()=>{},()=>{},f.bindings);
  assert.equal(connected.engine.id,'google');
  const previous=globalThis.location;globalThis.location={protocol:'https:'};
  try{assert.equal(await connectBrowserSearch(()=>{},()=>{}),null);}
  finally{globalThis.location=previous;}
});
