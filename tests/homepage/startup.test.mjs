import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source=readFileSync(new URL('../../chromium_overlay/chrome/browser/resources/argon_home/startup.js',import.meta.url),'utf8');
function boot() {
  const classes=new Set(), frames=[],timers=new Map();
  const context={
    document:{documentElement:{classList:{add:value=>classes.add(value),remove:value=>classes.delete(value)}}},
    setTimeout(callback,delay){assert.equal(delay,1500);timers.set(1,callback);return 1;},
    clearTimeout:id=>timers.delete(id),
    requestAnimationFrame:callback=>frames.push(callback),
  };
  runInNewContext(source,context);
  return {api:context.argonHomeStartup,classes,frames,timers,
    frame(){const callbacks=frames.splice(0);for(const callback of callbacks)callback();}};
}
test('first visible page waits for configuration; native ready follows rendered frames',()=>{
  const b=boot();let ready=0;
  assert(b.classes.has('argon-home-starting'));
  b.api.finish(()=>ready++);
  assert(!b.classes.has('argon-home-starting'));assert.equal(b.timers.size,0);
  assert.equal(ready,0);b.frame();assert.equal(ready,0);b.frame();assert.equal(ready,1);
});
test('failed application module cannot leave an invisible homepage; late native connection still paints',()=>{
  const b=boot();let ready=0;
  b.timers.get(1)();assert(!b.classes.has('argon-home-starting'));
  b.frame();b.frame();
  b.api.finish(()=>ready++);
  b.frame();assert.equal(ready,0);b.frame();assert.equal(ready,1);
});
