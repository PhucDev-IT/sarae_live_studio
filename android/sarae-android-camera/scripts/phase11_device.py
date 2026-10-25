"""Bounded one-recording experiment. Never presses recording/save/post/LIVE controls."""
import argparse,hashlib,json,re,subprocess,time
from pathlib import Path
P=Path('test-results/phase11');S=P/'state.json';ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
PACKAGE='com.ss.android.ugc.trill';FILES=f'/sdcard/Android/data/{PACKAGE}/files'
p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','snapshot','sequence','finish']);a=p.parse_args()
pre=json.loads((P/'preflight.json').read_text());start=time.monotonic()
if a.stage=='prepare':
    s=json.loads(S.read_text()) if S.exists() else {'spent':pre['seconds'],'active':None,'switches':0,'finished':False}
    assert s['active'] is None and 'module_hash' not in s,'No repeated preparation/test'
else:s=json.loads(S.read_text())
assert not s['finished'],'Test finished; do not spend another device budget'
def elapsed():return s['spent'] if s.get('finished') else s['spent']+(time.monotonic()-(s['active'] if s['active'] is not None else start))
def save():S.write_text(json.dumps(s,indent=2))
def adb(args,name=None,timeout=5,input_data=None,optional=False):
    remaining=(295 if a.stage=='finish' else 260)-elapsed()
    if remaining<1:raise TimeoutError('Device budget: stop; cleanup reserve protected')
    begin=time.monotonic();row={'args':args,'timeout':min(timeout,remaining)}
    try:
        r=subprocess.run([ADB,*args],input=input_data,capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
        row['exit']=r.returncode
        if name:(P/name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(r.stderr.decode(errors='replace'))
        return r.stdout.decode(errors='replace')
    finally:
        row['seconds']=time.monotonic()-begin
        with (P/'adb.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
def collect(label,video=True):
    pid=adb(['shell','pidof',PACKAGE],label+'-pid.txt').strip();assert re.fullmatch(r'\d+',pid)
    adb(['pull',f'{FILES}/phase11/audio-{pid}.jsonl',str(P/(label+'-audio.jsonl'))],timeout=4,optional=True)
    if video:adb(['pull',f'{FILES}/phase6/bridge-{pid}.jsonl',str(P/(label+'-bridge.jsonl'))],timeout=5,optional=True)
    return pid
def shot(label):
    adb(['shell','screencap','-p','/sdcard/sarae-phase11.png'],timeout=4)
    adb(['pull','/sdcard/sarae-phase11.png',str(P/(label+'.png'))],timeout=4)
def broadcast(action,extras,name):
    return adb(['shell','am','broadcast','-a','co.sarae.camera.'+action,'-p',PACKAGE,*extras],name,timeout=4)
try:
    if a.stage=='prepare':
        module=Path('.tools/phase11/module.apk');data=module.read_bytes();cache=pre['cache_path']
        assert re.fullmatch(r'cache/lspatch/co\.sarae\.camera\.poc/\d+\.apk',cache)
        assert adb(['shell','run-as',PACKAGE,'sha256sum',cache],'cache-before.txt').split()[0]=='4e59099e01efe6837773d78ff244554ebacb3edc70c2b9f66534e3c2bcf0bb22'
        paths=adb(['shell','pm','path',PACKAGE],'paths-before.txt').splitlines();assert len(paths)==61
        base=next(x.removeprefix('package:') for x in paths if x.endswith('/base.apk'))
        assert adb(['shell','sha256sum',base],'base-before.txt').split()[0]=='4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b'
        s['base']=base
        adb(['shell','am','force-stop',PACKAGE],'stop-before-module-update.txt')
        adb(['shell','run-as',PACKAGE,'cp',cache,cache+'.phase10'],'backup-cache.txt')
        adb(['push',str(module),'/data/local/tmp/sarae-phase11-module.apk'],'push-module.txt',6)
        adb(['shell','run-as',PACKAGE,'cp','/data/local/tmp/sarae-phase11-module.apk',cache+'.phase11'],'write-cache.txt')
        assert adb(['shell','run-as',PACKAGE,'sha256sum',cache+'.phase11'],'candidate-cache-hash.txt').split()[0]==hashlib.sha256(data).hexdigest()
        adb(['shell','run-as',PACKAGE,'mv',cache+'.phase11',cache],'activate-cache.txt')
        s['module_hash']=hashlib.sha256(data).hexdigest()
        adb(['shell','am','start','-W','-n',PACKAGE+'/com.ss.android.ugc.aweme.splash.SplashActivity'],'launch.txt',10)
        time.sleep(2)
        s['pid']=collect('ready',False)
        rows=[json.loads(x) for x in (P/'ready-audio.jsonl').read_text().splitlines()]
        assert any(x.get('pipelineVersion')=='phase11' for x in rows),'New module not loaded; no test'
        broadcast('AUDIO_MODE',['--es','mode','VIDEO'],'video-mode.txt')
        shot('ready')
        s['spent']+=time.monotonic()-start;s['active']=time.monotonic()
    elif a.stage=='snapshot':
        collect('snapshot');shot('snapshot')
    elif a.stage=='sequence':
        assert s['switches']==0 and elapsed()<160,'Need manual-stop/save/cleanup reserve'
        assert collect('record-start',False)==s['pid']
        events=[json.loads(x) for x in (P/'record-start-audio.jsonl').read_text().splitlines()]
        assert any(e['kind']=='audio_read_confirmed' for e in events),'Capture reads missing; stop'
        # Human confirmed recording is active before this stage. Hold A long enough to cross a loop.
        capture_start=max(e['ns'] for e in events if e['kind']=='audio_capture_start')
        assert not any(e['kind']=='audio_capture_stop' and e['ns']>capture_start for e in events),'Recording already stopped'
        # A human reply/tool dispatch may arrive well after recording began.
        # Use observed capture age rather than adding another full 12 seconds.
        capture_age=max(0,(max(e['ns'] for e in events)-capture_start)/1e9)
        first_at=max(0,12-capture_age)
        sequence_start=time.monotonic()
        for at,source in [(first_at,'B'),(first_at+6,'A')]:
            wait=sequence_start+at-time.monotonic()
            if wait>0:time.sleep(wait)
            broadcast('TEST_SWITCH',['--es','source',source,'--es','mode','now'],f'switch-{source}.txt')
            s['switches']+=1;save()
            print('switch',source,'device_seconds',elapsed(),flush=True)
        time.sleep(6)
        collect('after-switches');shot('after-switches');s['sequence_complete']=True
    elif a.stage=='finish':
        # User has stopped/saved the recording. Restore mic, collect files first.
        broadcast('AUDIO_MODE',['--es','mode','MICROPHONE'],'restore-mic.txt')
        videos=adb(['shell','content','query','--uri','content://media/external/video/media','--projection','_id:_data:date_added:duration','--where','"_id>1000074324"','--sort','"date_added DESC"'],'new-videos.txt',4)
        paths=re.findall(r'_data=([^,\r\n]+)',videos)
        if paths:adb(['pull',paths[0],str(Path('.tools/phase11')/Path(paths[0]).name)],'recorded-pull.txt',8);s['recorded_file']=str(Path('.tools/phase11')/Path(paths[0]).name)
        collect('final');shot('final')
        adb(['shell','sha256sum',s['base']],'base-after.txt',5)
        adb(['logcat','-b','crash','-d','-t','100','-v','threadtime'],'crash-buffer.txt',4,optional=True)
        adb(['logcat','-d','-t','400','-v','threadtime','--pid='+s['pid'],'AndroidRuntime:V','libc:V','DEBUG:V','LSPatch:V','LSPosed:V','*:S'],'fatal-loader-logcat.txt',4,optional=True)
        s['spent']=elapsed();s['active']=None;s['finished']=True
finally:
    save();print(json.dumps({'seconds':elapsed(),'state':s},indent=2),flush=True)
