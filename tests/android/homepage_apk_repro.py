#!/usr/bin/env python3
"""Observe the shipped ARM APK on Android; this script does not change app methods."""
import json
import copy
import hashlib
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
                    if any('ToolbarManager.ToolbarManager' in text for text in texts):
                        result['manager'] = owner
                    if any('Android.TopToolbar.CaptureBlocked.StatusIconAnimationDuration' in text for text in texts):
                        result['phone'] = owner
                        result['readiness'] = method.get_name()
    (OUT / 'dex-map.json').write_text(json.dumps(result, indent=2))
    return result


def emulator_apk(original):
    """Keep DEX/native code unchanged; add locale aliases to avoid native-bridge exceptions."""
    build_tools = sorted(Path(os.environ['ANDROID_HOME']).glob('build-tools/*/apksigner'))[-1].parent
    temp = Path(os.environ['RUNNER_TEMP'])
    unaligned, aligned, signed = [temp / name for name in ('argon-aliases.apk','argon-aligned.apk','argon-emulator.apk')]
    evidence = {'original_apk_sha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'aliases': [], 'code_sha256': {}}
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(unaligned, 'w', allowZip64=True) as target:
        for info in source.infolist():
            if info.filename.startswith('META-INF/') and info.filename.rsplit('.',1)[-1] in ('RSA','SF','DSA','EC','MF'):
                continue
            data = source.read(info.filename)
            target.writestr(info, data)
            if info.filename.endswith(('.dex','.so')):
                evidence['code_sha256'][info.filename] = hashlib.sha256(data).hexdigest()
            if info.filename.startswith('assets/locales/') and info.filename.endswith('.pak'):
                name = info.filename.rsplit('/',1)[-1]
                language = name.split('_',1)[0].split('.',1)[0].split('-',1)[0]
                for prefix in ('assets/stored-locales/','assets/fallback-locales/','assets/locales#lang_'+language+'/'):
                    alias = copy.copy(info)
                    alias.filename = alias.orig_filename = prefix + name
                    target.writestr(alias, data)
                    evidence['aliases'].append(alias.filename)
    subprocess.run([str(build_tools/'zipalign'),'-f','-P','16','4',str(unaligned),str(aligned)],check=True)
    key = temp / 'argon-emulator-key.jks'
    subprocess.run(['keytool','-genkeypair','-keystore',str(key),'-storepass','android','-keypass','android','-alias','argon-emulator','-dname','CN=Argon Emulator Test','-keyalg','RSA','-keysize','2048','-validity','2'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    subprocess.run([str(build_tools/'apksigner'),'sign','--ks',str(key),'--ks-pass','pass:android','--key-pass','pass:android','--out',str(signed),str(aligned)],check=True)
    with zipfile.ZipFile(signed) as result:
        for name, digest in evidence['code_sha256'].items():
            assert hashlib.sha256(result.read(name)).hexdigest() == digest, name
    evidence['emulator_apk_sha256'] = hashlib.sha256(signed.read_bytes()).hexdigest()
    (OUT/'emulator-apk.json').write_text(json.dumps(evidence,indent=2))
    return signed


APK = emulator_apk(next(Path('.apk-input').rglob('*.apk')))
mapping = dex_map(APK)
with zipfile.ZipFile(APK) as archive:
    mapping['localeAssets'] = [name for name in archive.namelist() if name.startswith('assets/') and name.endswith('.pak')]
print('Locale assets:', mapping['localeAssets'], flush=True)
print('DEX map:', mapping, flush=True)
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
adb('shell', 'monkey', '-p', PACKAGE, '-c', 'android.intent.category.LAUNCHER', '1')
time.sleep(20)
(OUT / '00-before-probe.png').write_bytes(adb('exec-out', 'screencap', '-p', binary=True))
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
    pid = int(adb('shell','pidof',PACKAGE).strip().split()[0])
    session = device.attach(pid)
    source = r'''
import Java from 'frida-java-bridge';
const config = CONFIG;
const history = [];
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
    if(['location_bar','url_bar','toolbar','control_container'].includes(name)) {
      const position=Java.array('int',[0,0]); view.getLocationOnScreen(position);
      out.push({id:name,alpha:view.getAlpha(),visibility:view.getVisibility(),shown:view.isShown(),
        translationY:view.getTranslationY(),x:position[0],y:position[1],width:view.getWidth(),height:view.getHeight()});
    }
    if(Java.use('android.view.ViewGroup').class.isInstance(view)) {
      const group=Java.cast(view,Java.use('android.view.ViewGroup'));
      for(let i=0;i<group.getChildCount();i++) views(group.getChildAt(i),out);
    }
  }catch(_){}
}
Java.perform(() => {
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
});
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
    (project/'agent.ts').write_text(source)
    bundle=frida.Compiler().build('agent.ts',project_root=str(project))
    agent = session.create_script(bundle)
    agent.on('message', lambda message, data: print('FRIDA', message, flush=True))
    agent.load()
except Exception as error:
    (OUT / 'frida-error.txt').write_text(str(error))
    print('Frida probe unavailable:', error, flush=True)


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
