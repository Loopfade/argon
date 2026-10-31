#!/usr/bin/env python3
"""Observe the existing APK with Android's supported ART tooling interface."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

OUT=Path('android-repro');OUT.mkdir(exist_ok=True)
TEMP=Path(os.environ['RUNNER_TEMP'])
PACKAGE='app.titaniumru.browser.test'
DATA='/data/data/'+PACKAGE
LOG=DATA+'/files/argon-probe.jsonl'

def run(args, **kwargs):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,**kwargs)
    if p.returncode:
        raise RuntimeError(str(args)+': '+p.stderr.decode(errors='replace')+' '+p.stdout.decode(errors='replace'))
    return p.stdout

def adb(*args,binary=False,check=True):
    p=subprocess.run(['adb',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    if check and p.returncode:
        raise RuntimeError(str(args)+': '+p.stderr.decode(errors='replace')+' '+p.stdout.decode(errors='replace'))
    return p.stdout if binary else p.stdout.decode(errors='replace')

def debuggable_manifest(data):
    data=bytearray(data);offset=8;ids=[]
    while offset<len(data):
        kind,header,size=struct.unpack_from('<HHI',data,offset)
        if size<header or size==0:raise RuntimeError('Invalid XML chunk')
        if kind==0x0180:
            ids=list(struct.unpack_from('<'+'I'*((size-header)//4),data,offset+header))
        if kind==0x0102:
            ext=offset+header
            start,entry_size,count=struct.unpack_from('<HHH',data,ext+8)
            for i in range(count):
                attr=ext+start+i*entry_size
                name=struct.unpack_from('<I',data,attr+4)[0]
                if name<len(ids) and ids[name]==0x0101000f:
                    struct.pack_into('<I',data,attr+8,0xffffffff)
                    data[attr+15]=0x12
                    struct.pack_into('<I',data,attr+16,0xffffffff)
                    return bytes(data)
        offset+=size
    raise RuntimeError('Existing debuggable attribute not found')

def prepare():
    from androguard.core.apk import APK
    from loguru import logger
    logger.remove()
    source=next(Path('.apk-input').rglob('*.apk'))
    manifest=APK(str(source))
    debug=str(manifest.get_attribute_value('application','debuggable')).lower()=='true'
    evidence={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_debuggable':debug}
    chosen=source.resolve()
    if not debug:
        tools=sorted(Path(os.environ['ANDROID_HOME']).glob('build-tools/*/apksigner'))[-1].parent
        raw=TEMP/'probe-unaligned.apk';aligned=TEMP/'probe-aligned.apk';chosen=TEMP/'probe-debuggable.apk'
        evidence['code_sha256']={}
        with zipfile.ZipFile(source) as zin,zipfile.ZipFile(raw,'w') as zout:
            for info in zin.infolist():
                if info.filename.startswith('META-INF/') and info.filename.endswith(('.RSA','.SF','.DSA','.EC','.MF')):continue
                contents=zin.read(info.filename)
                if info.filename.endswith(('.dex','.so')):
                    evidence['code_sha256'][info.filename]=hashlib.sha256(contents).hexdigest()
                if info.filename=='AndroidManifest.xml':contents=debuggable_manifest(contents)
                zout.writestr(info,contents)
        run([str(tools/'zipalign'),'-f','-P','16','4',str(raw),str(aligned)])
        key=TEMP/'probe-key.jks'
        run(['keytool','-genkeypair','-keystore',str(key),'-storepass','android','-keypass','android','-alias','probe','-dname','CN=Argon Emulator Probe','-keyalg','RSA','-validity','2'])
        run([str(tools/'apksigner'),'sign','--ks',str(key),'--ks-pass','pass:android','--key-pass','pass:android','--out',str(chosen),str(aligned)])
        with zipfile.ZipFile(chosen) as archive:
            for name,digest in evidence['code_sha256'].items():assert hashlib.sha256(archive.read(name)).hexdigest()==digest
    evidence['test_sha256']=hashlib.sha256(chosen.read_bytes()).hexdigest()
    (OUT/'apk-evidence.json').write_text(json.dumps(evidence,indent=2))
    (TEMP/'probe-apk-path').write_text(str(chosen))
    for architecture in ('x86_64','i686'):
        matches=sorted(Path(os.environ['ANDROID_HOME']).glob('ndk/*/toolchains/llvm/prebuilt/linux-x86_64/bin/'+architecture+'-linux-android29-clang++'))
        if not matches:raise RuntimeError('Installed NDK not found')
        compiler=matches[-1]
        java=Path(os.environ['JAVA_HOME'])
        run([str(compiler),'-shared','-fPIC','-std=c++17','-fno-exceptions','-fno-rtti','-nostdlib++',
             '-I'+str(java/'include'),'-I'+str(java/'include/linux'),
             'tests/android/homepage_probe.cc','-o',str(TEMP/('probe-'+architecture+'.so')),'-Wall','-Wextra','-Werror'])
    print('PREPARED',json.dumps(evidence),flush=True)

if '--prepare' in sys.argv:
    prepare();sys.exit(0)

def capture(name):
    png=adb('exec-out','screencap','-p',binary=True)
    (OUT/(name+'.png')).write_bytes(png)
    raw=adb('shell','cat',LOG,check=False)
    (OUT/(name+'.jsonl')).write_text(raw)
    events=[]
    for line in raw.splitlines():
        try:events.append(json.loads(line))
        except ValueError:pass
    compact=[{k:v for k,v in e.items() if k!='views'}|{'location_views':[v for v in e.get('views',[]) if 'LocationBar' in v.get('class','')]} for e in events]
    print('PROBE '+name+' '+json.dumps(compact),flush=True)
    print('SCREENSHOT_BASE64 '+name+' '+base64.b64encode(png).decode(),flush=True)
    adb('shell','uiautomator','dump','/sdcard/argon-window.xml',check=False)
    xml=adb('shell','cat','/sdcard/argon-window.xml',check=False)
    (OUT/(name+'.xml')).write_text(xml)
    return xml

def tap_matching(xml,predicate):
    try:
        for node in ET.fromstring(xml).iter('node'):
            if predicate(node.attrib):
                coords=list(map(int,re.findall(r'\d+',node.attrib.get('bounds',''))))
                if len(coords)==4:
                    adb('shell','input','tap',str((coords[0]+coords[2])//2),str((coords[1]+coords[3])//2));return True
    except ET.ParseError:pass
    return False

def new_home(label):
    adb('shell','input','keyevent','KEYCODE_MENU');time.sleep(2)
    xml=capture(label+'-menu')
    if not tap_matching(xml,lambda a: (a.get('text','') or a.get('content-desc','')).lower() in ('new tab','новая вкладка')):
        raise RuntimeError('New tab not found')
    time.sleep(5)
    return capture(label)

adb('root',check=False);adb('wait-for-device')
adb('shell','wm','size','1080x2400');adb('shell','wm','density','420')
adb('shell','input','keyevent','KEYCODE_WAKEUP');adb('shell','wm','dismiss-keyguard',check=False)
adb('install','-r','-t',(TEMP/'probe-apk-path').read_text())
adb('shell',"echo '_ --no-first-run --no-default-browser-check --disable-fre' > /data/local/tmp/chrome-command-line")
adb('shell','am','set-debug-app','-w','--persistent',PACKAGE)
adb('logcat','-c')
try:
    adb('shell','monkey','-p',PACKAGE,'-c','android.intent.category.LAUNCHER','1');time.sleep(3)
    pid=int(adb('shell','pidof',PACKAGE).strip().split()[0])
    executable=adb('shell','readlink','/proc/'+str(pid)+'/exe')
    architecture='i686' if '32' in executable else 'x86_64'
    uid=adb('shell','stat','-c','%u',DATA).strip()
    adb('shell','mkdir','-p',DATA+'/files',DATA+'/code_cache')
    adb('shell','chown',uid+':'+uid,DATA+'/files',DATA+'/code_cache')
    adb('push',str(TEMP/('probe-'+architecture+'.so')),DATA+'/code_cache/probe.so')
    adb('shell','chmod','555',DATA+'/code_cache/probe.so')
    adb('shell','restorecon',DATA+'/code_cache/probe.so',check=False)
    result=adb('shell','am','attach-agent',PACKAGE,DATA+'/code_cache/probe.so='+LOG)
    print('ATTACH',result,flush=True)
    adb('forward','tcp:8700','jdwp:'+str(pid))
    debugger=socket.create_connection(('127.0.0.1',8700),timeout=10)
    debugger.sendall(b'JDWP-Handshake')
    handshake=b''
    while len(handshake)<14:handshake+=debugger.recv(14-len(handshake))
    assert handshake==b'JDWP-Handshake'
    debugger.sendall(struct.pack('>IIBBB',11,1,0,1,9))
    time.sleep(20)
    xml=capture('01-initial-home')
    if not tap_matching(xml,lambda a: a.get('class')=='android.widget.Button' and 'URL' in (a.get('text','')+a.get('content-desc',''))):
        adb('shell','input','tap','540','520')
    time.sleep(3);capture('02-focused')
    adb('shell','input','keyevent','KEYCODE_BACK');time.sleep(3);capture('03-cancelled')
    new_home('04-original-new-home')
    adb('shell','touch',LOG+'-patch')
    new_home('05-experimental-new-home')
    new_home('06-experimental-repeated-home')
finally:
    (OUT/'logcat.txt').write_text(adb('logcat','-d','-v','threadtime',check=False))
    (OUT/'activities.txt').write_text(adb('shell','dumpsys','activity','activities',check=False))
    (OUT/'final-probe.jsonl').write_text(adb('shell','cat',LOG,check=False))
