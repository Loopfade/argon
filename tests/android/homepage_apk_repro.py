#!/usr/bin/env python3
"""Observe the shipped ARM APK on Android; this script does not change app methods."""
import json
import hashlib
import sys
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

OUT = Path('android-repro')
OUT.mkdir(exist_ok=True)
PACKAGE = 'app.titaniumru.browser.test'


def adb(*args, binary=False, check=True):
    return subprocess.run(['adb', *args], check=check, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=not binary, timeout=90).stdout


def capture(name):
    (OUT / (name + '.png')).write_bytes(adb('exec-out', 'screencap', '-p', binary=True))
    try:
        (OUT / (name + '.json')).write_text(json.dumps(agent.exports_sync.capture(name), indent=2))
    except Exception as error:
        (OUT / (name + '-probe-error.txt')).write_text(str(error))
    adb('shell', 'uiautomator', 'dump', '/sdcard/argon-window.xml', check=False)
    xml = adb('shell', 'cat', '/sdcard/argon-window.xml', check=False)
    (OUT / (name + '.xml')).write_text(xml)
    print('Captured', name, flush=True)
    return xml


def tap_matching(xml, predicate):
    try:
        for node in ET.fromstring(xml).iter('node'):
            if predicate(node.attrib):
                coords = list(map(int, re.findall(r'\d+', node.attrib.get('bounds', ''))))
                if len(coords) == 4:
                    adb('shell', 'input', 'tap', str((coords[0]+coords[2])//2), str((coords[1]+coords[3])//2))
                    return True
    except ET.ParseError:
        pass
    return False


def dex_map(apk):
    from androguard.core.dex import DEX
    from loguru import logger
    logger.remove()
    result = {}
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            if not re.fullmatch(r'classes\d*\.dex', name):
                continue
            dex = DEX(archive.read(name))
            for cls in dex.get_classes():
                for method in cls.get_methods():
                    if method.get_code() is None:
                        continue
                    texts = [ins.get_output() for ins in method.get_instructions()
                             if ins.get_name().startswith('const-string')]
                    owner = cls.get_name()[1:-1].replace('/', '.')
                    if any('Successfully loaded native library' in text for text in texts):
                        calls=[ins.get_output() for ins in method.get_instructions() if ins.get_name().startswith('invoke-')]
                        native=[]
                        for call in calls:
                            match=re.search(r'LJ/N;->([^\s(]+)',call)
                            if match and match.group(1) not in native:
                                native.append(match.group(1))
                        result['loader']=owner
                        result['loaderInit']=method.get_name()
                        result['loaderNative']=native
                    if any('ToolbarManager.ToolbarManager' in text for text in texts):
                        result['manager'] = owner
                    if any('Android.TopToolbar.CaptureBlocked.StatusIconAnimationDuration' in text for text in texts):
                        result['phone'] = owner
                        result['readiness'] = method.get_name()
    (OUT / 'dex-map.json').write_text(json.dumps(result, indent=2))
    return result


def compile_agent(mapping):
    import frida
    source = r'''
import Java from 'frida-java-bridge';
const config = CONFIG;
const history = [];
// Chromium 156 installs an ARM FindClass callback in the main thread's JNI table.
// x86 framework libraries cannot call that ARM pointer through the native bridge.
// Restore the original table immediately after libraryLoaded returns. This only
// affects the emulator's split-classloader compatibility, not toolbar behavior.
Java.perform(() => {
  const loader=Java.use(config.loader);
  const init=loader[config.loaderInit].overload();
  init.implementation=function() {
    const entries=[], natives=Java.use('J.N');
    for(const name of config.loaderNative) {
      for(const overload of natives[name].overloads) {
        entries.push(overload);
        overload.implementation=function(...args) {
          const env=Java.vm.getEnv().handle;
          const original=env.readPointer();
          const value=overload.call(this,...args);
          const replaced=env.readPointer();
          if(!replaced.equals(original)) {
            env.writePointer(original);
            send({emulatorJniRestored:true,entry:name,original:original.toString(),replaced:replaced.toString()});
            setImmediate(()=>entries.forEach(entry=>entry.implementation=null));
          }
          return value;
        };
      }
    }
    return init.call(this);
  };
  send({emulatorJniBootstrap:true,loader:config.loader,init:config.loaderInit,entries:config.loaderNative});
});

function fields(obj) {
  const result = {};
  for (let cls = obj.getClass(); cls !== null; cls = cls.getSuperclass()) {
    const fs = cls.getDeclaredFields();
    for (let i=0; i<fs.length; i++) {
      try {
        const f=fs[i], type=f.getType().getName().toString();
        if (Java.use('java.lang.reflect.Modifier').isStatic(f.getModifiers())) continue;
        if (!['boolean','float','int','long'].includes(type)) continue;
        f.setAccessible(true);
        const value=f.get(obj);
        result[cls.getName()+'.'+f.getName()]={type:type,value:value===null?null:value.toString()};
      } catch (_) {}
    }
  }
  return result;
}
function views(view, out) {
  try {
    const id=view.getId();
    let name='';
    if(id!==-1) try{name=view.getResources().getResourceEntryName(id).toString();}catch(_){}
    {
      const className=view.getClass().getName().toString();
      const position=Java.array('int',[0,0]); view.getLocationOnScreen(position);
      out.push({id:name,className:className,alpha:view.getAlpha(),visibility:view.getVisibility(),shown:view.isShown(),
        translationY:view.getTranslationY(),x:position[0],y:position[1],width:view.getWidth(),height:view.getHeight()});
    }
    if(Java.use('android.view.ViewGroup').class.isInstance(view)) {
      const group=Java.cast(view,Java.use('android.view.ViewGroup'));
      for(let i=0;i<group.getChildCount();i++) views(group.getChildAt(i),out);
    }
  }catch(_){}
}
setTimeout(()=>Java.perform(() => {
  const method=Java.use(config.phone)[config.readiness].overload();
  method.implementation=function() {
    const result=method.call(this);
    try {
      const readiness=fields(result), stamp=JSON.stringify(readiness);
      if(history.length===0 || history[history.length-1].stamp!==stamp) {
        const item={at:Date.now(),stamp:stamp,readiness:readiness,views:[]};
        views(Java.cast(this,Java.use('android.view.View')),item.views);
        item.fields=fields(this);
        history.push(item);
        if(history.length>200)history.shift();
      }
    }catch(_){}
    return result;
  };
}),10000);
rpc.exports.capture = function(label) {
  return new Promise(resolve => Java.perform(()=>Java.scheduleOnMainThread(()=>{
    const out={label:label,instances:[],history:history.slice()};
    Java.choose(config.phone || 'org.chromium.chrome.browser.toolbar.top.ToolbarPhone',{
      onMatch(instance) {
        const item={fields:fields(instance),views:[]};
        try{views(Java.cast(instance,Java.use('android.view.View')),item.views);}catch(e){item.viewError=String(e);}
        if(config.readiness) try{item.readiness=fields(instance[config.readiness]());}catch(e){item.readyError=String(e);}
        out.instances.push(item);
      },onComplete(){resolve(out);}
    });
  })));
};
'''.replace('CONFIG', json.dumps(mapping))
    project=Path('.frida-agent').resolve()
    (project/'agent.ts').write_text('// @ts-nocheck\n'+source)
    compiler=frida.Compiler()
    compiler.on('diagnostics',lambda diag: (OUT/'compiler-diagnostics.json').write_text(json.dumps(diag,indent=2)))
    bundle=compiler.build('agent.ts',project_root=str(project))
    (project/'agent.bundle.js').write_text(bundle)
    return bundle


APK = next(Path('.apk-input').rglob('*.apk'))
(OUT/'original-apk-sha256.txt').write_text(hashlib.sha256(APK.read_bytes()).hexdigest())
mapping = dex_map(APK)
with zipfile.ZipFile(APK) as archive:
    mapping['localeAssets'] = [name for name in archive.namelist() if name.startswith('assets/') and name.endswith('.pak')]
print('Locale assets:', mapping['localeAssets'], flush=True)
print('DEX map:', mapping, flush=True)
if '--compile-only' in sys.argv:
    compile_agent(mapping)
    print('Compiled diagnostic agent',flush=True)
    sys.exit(0)
(OUT / 'device.txt').write_text(adb('shell', 'getprop'))
print('Device ABIs:', adb('shell', 'getprop', 'ro.product.cpu.abilist').strip(), flush=True)
adb('root', check=False)
adb('wait-for-device')
adb('shell', 'wm', 'size', '1080x2400')
adb('shell', 'wm', 'density', '420')
adb('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')
adb('shell', 'wm', 'dismiss-keyguard', check=False)
adb('install', '-r', '-t', str(APK))
adb('shell', "echo '_ --no-first-run --no-default-browser-check --disable-fre' > /data/local/tmp/chrome-command-line")
adb('shell','am','set-debug-app','--persistent',PACKAGE)
adb('logcat', '-c')
agent = None
pid = None
try:
    if os.environ.get('ARGON_WITH_FRIDA') != '1':
        raise RuntimeError('Baseline run without a diagnostic agent')
    import frida
    adb('push', str(Path(os.environ['RUNNER_TEMP']) / 'frida-server'), '/data/local/tmp/argon-frida-server')
    adb('shell', 'chmod', '755', '/data/local/tmp/argon-frida-server')
    adb('shell', 'nohup /data/local/tmp/argon-frida-server >/data/local/tmp/argon-frida.log 2>&1 </dev/null &')
    adb('forward', 'tcp:27042', 'tcp:27042')
    device = frida.get_device_manager().add_remote_device('127.0.0.1:27042')
    bundle=Path('.frida-agent/agent.bundle.js').read_text()
    pid = device.spawn([PACKAGE])
    session = device.attach(pid)
    agent = session.create_script(bundle)
    agent.on('message', lambda message, data: print('FRIDA', message, flush=True))
    agent.load()
    device.resume(pid)
except Exception as error:
    (OUT / 'frida-error.txt').write_text(str(error))
    print('Frida probe unavailable:', error, flush=True)
    if pid is None:
        adb('shell', 'monkey', '-p', PACKAGE, '-c', 'android.intent.category.LAUNCHER', '1')
    else:
        device.resume(pid)

time.sleep(20)


try:
    xml = capture('01-initial-home')
    found = tap_matching(xml, lambda a: a.get('class') == 'android.widget.Button'
                         and ('URL' in (a.get('text','') + a.get('content-desc',''))))
    if not found:
        adb('shell', 'input', 'tap', '540', '520')
    time.sleep(3)
    capture('02-focused')
    adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
    time.sleep(3)
    capture('03-after-focus-cancel')
    adb('shell', 'input', 'keyevent', 'KEYCODE_MENU')
    time.sleep(2)
    xml = capture('04-menu')
    if not tap_matching(xml, lambda a: (a.get('text','') or a.get('content-desc','')).lower() in ('new tab','новая вкладка')):
        raise RuntimeError('Could not locate the New tab command; inspect the saved screen/XML')
    time.sleep(6)
    capture('05-new-home')
finally:
    (OUT / 'logcat.txt').write_text(adb('logcat', '-d', '-v', 'threadtime', check=False))
    (OUT / 'activities.txt').write_text(adb('shell', 'dumpsys', 'activity', 'activities', check=False))
