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
    chunks=[];offset=8
    while offset<len(data):
        kind,header,size=struct.unpack_from('<HHI',data,offset)
        if size<header or size==0:raise RuntimeError('Invalid XML chunk')
        chunks.append([kind,bytearray(data[offset:offset+size])]);offset+=size
    pool=next(chunk for kind,chunk in chunks if kind==1)
    header=struct.unpack_from('<H',pool,2)[0]
    count,styles,flags,start,style_start=struct.unpack_from('<IIIII',pool,8)
    if styles:raise RuntimeError('Unexpected styled manifest string pool')
    offsets=list(struct.unpack_from('<'+'I'*count,pool,header))
    def length8(pos):
        v=pool[pos]
        return (((v&127)<<8)|pool[pos+1],pos+2) if v&128 else (v,pos+1)
    def length16(pos):
        v=struct.unpack_from('<H',pool,pos)[0]
        return (((v&32767)<<16)|struct.unpack_from('<H',pool,pos+2)[0],pos+4) if v&32768 else (v,pos+2)
    def string(i):
        pos=start+offsets[i]
        if flags&256:
            _,pos=length8(pos);size,pos=length8(pos)
            return bytes(pool[pos:pos+size]).decode('utf-8',errors='replace')
        size,pos=length16(pos)
        return bytes(pool[pos:pos+2*size]).decode('utf-16le',errors='replace')
    names=[string(i) for i in range(count)]
    # App-zygote preloading cannot map this ARM library into the x86 renderer.
    # Keep isolated renderer services, using their ordinary per-process loader.
    for kind,raw in chunks:
        if kind!=0x0102:continue
        node_header=struct.unpack_from('<H',raw,2)[0]
        attr_start,attr_size,attrs=struct.unpack_from('<HHH',raw,node_header+8)
        for i in range(attrs):
            attr=node_header+attr_start+i*attr_size
            name=struct.unpack_from('<I',raw,attr+4)[0]
            if names[name]=='useAppZygote' and os.environ.get('PROBE_APP_ZYGOTE')!='1':
                struct.pack_into('<I',raw,attr+8,0xffffffff)
                raw[attr+15]=0x12;struct.pack_into('<I',raw,attr+16,0)
    ns=names.index('http://schemas.android.com/apk/res/android')
    application=names.index('application')
    encoded=b'\x0a\x0adebuggable\0' if flags&256 else b'\x0a\0'+b'd\0e\0b\0u\0g\0g\0a\0b\0l\0e\0\0\0'
    string_data=pool[start:]
    new_pool=pool[:header]+pool[header:header+4*count]+struct.pack('<I',len(string_data))+string_data+encoded
    new_pool+=b'\0'*((-len(new_pool))%4)
    struct.pack_into('<I',new_pool,4,len(new_pool))
    struct.pack_into('<I',new_pool,8,count+1)
    struct.pack_into('<I',new_pool,16,flags&~1)
    struct.pack_into('<I',new_pool,20,start+4)
    resources=next(chunk for kind,chunk in chunks if kind==0x0180)
    resource_header=struct.unpack_from('<H',resources,2)[0]
    ids=list(struct.unpack_from('<'+'I'*((len(resources)-resource_header)//4),resources,resource_header))
    ids.extend([0]*(count+1-len(ids)));ids[count]=0x0101000f
    new_resources=resources[:resource_header]+struct.pack('<'+'I'*len(ids),*ids)
    struct.pack_into('<I',new_resources,4,len(new_resources))
    inserted=False
    for chunk in chunks:
        kind,raw=chunk
        if kind==1:chunk[1]=new_pool
        elif kind==0x0180:chunk[1]=new_resources
        elif kind==0x0102:
            header=struct.unpack_from('<H',raw,2)[0]
            name=struct.unpack_from('<I',raw,header+4)[0]
            if name!=application:continue
            attr_start,attr_size,attrs=struct.unpack_from('<HHH',raw,header+8)
            if attr_size!=20:raise RuntimeError('Unexpected attribute size')
            index=attrs
            for i in range(attrs):
                attr=header+attr_start+i*20
                attribute_name=struct.unpack_from('<I',raw,attr+4)[0]
                resource=ids[attribute_name] if attribute_name<len(ids) else 0
                if resource==0x0101000f:
                    raw[attr+15]=0x12;struct.pack_into('<I',raw,attr+16,0xffffffff);inserted=True;break
                if resource>0x0101000f and index==attrs:index=i
            if not inserted:
                at=header+attr_start+index*20
                attribute=struct.pack('<IIIHBBI',ns,count,0xffffffff,8,0,0x12,0xffffffff)
                raw[at:at]=attribute
                struct.pack_into('<I',raw,4,len(raw));struct.pack_into('<H',raw,header+12,attrs+1)
                for position in (14,16,18):
                    value=struct.unpack_from('<H',raw,header+position)[0]
                    if value and value>=index+1:struct.pack_into('<H',raw,header+position,value+1)
                inserted=True
    if not inserted:raise RuntimeError('Application node not found')
    result=bytearray(data[:8])+b''.join(chunk for _,chunk in chunks)
    struct.pack_into('<I',result,4,len(result))
    return bytes(result)

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
    modified=APK(str(chosen))
    assert str(modified.get_attribute_value('application','debuggable')).lower()=='true'
    if os.environ.get('PROBE_APP_ZYGOTE')!='1':
        assert all(node.get('{http://schemas.android.com/apk/res/android}useAppZygote','false')!='true' for node in modified.get_android_manifest_xml().iter('service'))
    evidence['app_zygote_disabled_for_emulator']=os.environ.get('PROBE_APP_ZYGOTE')!='1'
    with zipfile.ZipFile(chosen) as archive:
        art_arch='x86_64' if any(name.startswith('lib/arm64-v8a/') for name in archive.namelist()) else 'i686'
    (TEMP/'probe-arch').write_text(art_arch)
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
    for attempt in range(12):
        adb('shell','rm','-f','/sdcard/argon-window.xml',check=False)
        adb('shell','uiautomator','dump','/sdcard/argon-window.xml',check=False)
        xml=adb('shell','cat','/sdcard/argon-window.xml',check=False)
        try:
            ET.fromstring(xml)
            break
        except ET.ParseError:
            time.sleep(2)
    else:
        raise RuntimeError('Android accessibility hierarchy did not become ready')
    (OUT/(name+'.xml')).write_text(xml)
    print('UI_XML '+name+' '+xml,flush=True)
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
probe_flags='_ --no-first-run --no-default-browser-check --disable-fre --js-flags=--jitless'
if os.environ.get('PROBE_SINGLE_PROCESS')=='1':
    probe_flags+=' --single-process --no-sandbox'
adb('shell',"echo '"+probe_flags+"' > /data/local/tmp/chrome-command-line")
adb('shell','am','set-debug-app','--persistent',PACKAGE)
adb('logcat','-c')
try:
    architecture=(TEMP/'probe-arch').read_text()
    uid=adb('shell','stat','-c','%u',DATA).strip()
    adb('shell','mkdir','-p',DATA+'/files',DATA+'/code_cache')
    adb('shell','chown',uid+':'+uid,DATA+'/files',DATA+'/code_cache')
    adb('push',str(TEMP/('probe-'+architecture+'.so')),DATA+'/code_cache/probe.so')
    adb('shell','chmod','555',DATA+'/code_cache/probe.so')
    adb('shell','restorecon',DATA+'/code_cache/probe.so',check=False)
    result=adb('shell','am','start','--attach-agent',DATA+'/code_cache/probe.so='+LOG,'-n',PACKAGE+'/com.google.android.apps.chrome.Main')
    print('ATTACH',result,flush=True)
    time.sleep(30)
    startup=capture('00-startup')
    tap_matching(startup,lambda a: (a.get('text','') or a.get('content-desc','')).lower() in ('no thanks','not now','нет, спасибо','не сейчас'))
    # Let the WebUI's asynchronous search connection and accessibility tree settle.
    time.sleep(15)
    for attempt in range(30):
        if '"hidden":true' in adb('shell','cat',LOG,check=False):break
        time.sleep(2)
    xml=capture('01-initial-home')
    if '"hidden":true' not in adb('shell','cat',LOG,check=False):
        raise RuntimeError('The native homepage delegate never became active')
    if not tap_matching(xml,lambda a: a.get('class')=='android.widget.Button' and 'URL' in (a.get('text','')+a.get('content-desc',''))):
        # The first accessibility snapshot may precede Blink's AX tree.
        time.sleep(3)
        xml=capture('01-initial-home-accessible')
        if not tap_matching(xml,lambda a: a.get('class') in ('android.widget.Button','android.widget.EditText') and ('search-input' in a.get('resource-id','') or 'enter address' in (a.get('text','')+a.get('content-desc','')).lower() or 'URL' in (a.get('text','')+a.get('content-desc','')))):
            print('WEBUI_AX_NOT_READY: tapping the fakebox from the captured 1080x2400 frame',flush=True)
            adb('shell','input','tap','540','765')
    time.sleep(3);capture('02-focused')
    adb('shell','input','keyevent','KEYCODE_BACK');time.sleep(3);capture('03-cancelled')
    new_home('04-original-new-home')
    adb('shell','touch',LOG+'-patch')
    new_home('05-experimental-new-home')
    new_home('06-experimental-repeated-home')
finally:
    logcat=adb('logcat','-d','-v','threadtime',check=False)
    (OUT/'logcat.txt').write_text(logcat)
    print('CRASH_SUMMARY '+json.dumps([line for line in logcat.splitlines() if (' F DEBUG' in line or 'Fatal signal' in line or 'Undefined instruction' in line or ' F chromium' in line)][:120]),flush=True)
    print('APP_LOG_SUMMARY '+json.dumps([line for line in logcat.splitlines() if 'cr_' in line or ' E chromium' in line or ' E ndk_translation' in line][-100:]),flush=True)
    (OUT/'activities.txt').write_text(adb('shell','dumpsys','activity','activities',check=False))
    (OUT/'final-probe.jsonl').write_text(adb('shell','cat',LOG,check=False))
