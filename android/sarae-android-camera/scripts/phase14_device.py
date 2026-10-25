"""Target-only preview verification. Never starts LIVE, records, reinstalls, or clears data."""
import argparse,datetime,hashlib,json,re,subprocess,time
from pathlib import Path
P=Path('test-results/phase14');T=Path('.tools/phase14');P.mkdir(exist_ok=True);T.mkdir(exist_ok=True)
STATE=P/'state.json';PKG='com.ss.android.ugc.trill';FILES=f'/sdcard/Android/data/{PKG}/files'
ADB='D:/window/Android/Sdk/platform-tools/adb.exe';CACHE='cache/lspatch/co.sarae.camera.poc/183496786.apk'
OLD='2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0'
parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['inventory','deploy','capture','nav','retired','finish','rollback'])
parser.add_argument('--label',default='snapshot');parser.add_argument('--nav',choices=['create','live-tab','close-preview','home'])
a=parser.parse_args();assert re.fullmatch('[a-z0-9-]{1,40}',a.label)
s=json.loads(STATE.read_text()) if STATE.exists() else {'spent':0,'active':None,'finished':False,'live_sessions':0}
assert not s['finished'],'Verification finished; do not start another test round'
start=time.monotonic()
def elapsed():return s['spent']+time.monotonic()-(s['active'] if s['active'] is not None else start)
def save():STATE.write_text(json.dumps(s,indent=2),encoding='utf-8')
def adb(args,name=None,timeout=5,binary=False,optional=False):
    left=(295 if a.stage in ['finish','rollback'] else 255)-elapsed()
    assert left>1,'Device budget reached; reserve cleanup'
    assert not any(x in args for x in ['install','uninstall','clear','install-create','install-commit'])
    now=time.monotonic();entry={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':args,'timeout':min(timeout,left)}
    try:
        r=subprocess.run([ADB,*args],capture_output=True,timeout=min(timeout,left),creationflags=subprocess.CREATE_NO_WINDOW)
        entry['exit_code']=r.returncode
        if name:Path(name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(r.stderr.decode(errors='replace'))
        return r.stdout if binary else r.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        entry['timed_out']=True;raise
    finally:
        entry['seconds']=time.monotonic()-now
        with (P/'adb.jsonl').open('a') as f:f.write(json.dumps(entry)+'\n')
def guard_idle():
    text=adb(['shell','dumpsys','activity','activities'],T/'idle-activity.txt',6)
    assert any('com.sec.android.app.launcher' in l for l in text.splitlines() if 'topResumedActivity=' in l),'User activity ongoing; do not restart/update'
    camera=adb(['shell','dumpsys','media.camera'],T/'idle-camera.txt',6)
    assert camera.split('Active Camera Clients:')[1].split('Allowed user IDs:')[0].strip()=='[]','Camera active; stop'
def collect(label,full=False):
    pid=adb(['shell','pidof',PKG],P/(label+'-pid.txt'),3).strip();assert re.fullmatch(r'\d+',pid)
    for kind,folder in [('bridge','phase6'),('audio','phase11')]:
        adb(['pull',f'{FILES}/{folder}/{kind}-{pid}.jsonl',str(P/(label+'-'+kind+'.jsonl'))],timeout=5)
    adb(['shell','screencap','-p','/sdcard/sarae-phase14.png'],timeout=4)
    adb(['pull','/sdcard/sarae-phase14.png',str(T/(label+'.png'))],timeout=3)
    if full:
        adb(['shell','dumpsys','media.camera'],T/(label+'-camera.txt'),6)
        adb(['shell','dumpsys','media.audio_flinger'],T/(label+'-audioflinger.txt'),6)
        adb(['logcat','-d','-t','1800','-v','threadtime','--pid='+pid],T/(label+'-logcat.txt'),5)
    s['pid']=pid
    events=[json.loads(l) for l in (P/(label+'-bridge.jsonl')).read_text().splitlines()]
    if any(e['kind']=='attached' and 'revision=phase14 submissionRemap=true' in e['detail'] for e in events):s['load_pass']=True
try:
    if a.stage=='inventory':
        assert not s.get('inventory_pass')
        guard_idle()
        assert adb(['shell','getprop','ro.product.model'],P/'model.txt').strip()=='SM-G996U'
        package=adb(['shell','dumpsys','package',PKG],T/'package-before.txt',6)
        assert 'versionName=47.4.3' in package and 'versionCode=470403' in package
        paths=adb(['shell','pm','path',PKG],P/'paths-before.txt').splitlines();assert len(paths)==61
        s['base']=next(x.removeprefix('package:') for x in paths if x.endswith('/base.apk'))
        assert adb(['shell','sha256sum',s['base']],P/'base-before.txt',6).split()[0]=='4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b'
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE],P/'cache-before.txt').split()[0]==OLD
        data=adb(['exec-out','run-as',PKG,'cat',CACHE],T/'rollback-module.apk',5,binary=True)
        assert hashlib.sha256(data).hexdigest()==OLD
        adb(['shell','run-as',PKG,'ls','-l',CACHE],P/'cache-mode.txt')
        adb(['shell','sha256sum',FILES+'/A.mp4',FILES+'/B.mp4'],P/'media-before.txt',6)
        adb(['shell','cat',FILES+'/sarae-render.json'],P/'render-config.json')
        s['inventory_pass']=True;s['spent']+=time.monotonic()-start
    elif a.stage=='deploy':
        assert s.get('inventory_pass') and not s.get('deployed')
        s['active']=time.monotonic();save();guard_idle()
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE]).split()[0]==OLD
        data=(T/'module.apk').read_bytes();s['candidate_hash']=hashlib.sha256(data).hexdigest()
        adb(['shell','am','force-stop',PKG],P/'stop-idle-before-cache-update.txt')
        adb(['shell','run-as',PKG,'cp',CACHE,CACHE+'.phase14-rollback'],P/'backup-remote.txt')
        adb(['push',str(T/'module.apk'),'/data/local/tmp/sarae-phase14-module.apk'],P/'push-module.txt',6)
        adb(['shell','run-as',PKG,'cp','/data/local/tmp/sarae-phase14-module.apk',CACHE+'.phase14'],P/'write-candidate.txt')
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE+'.phase14'],P/'cache-candidate.txt').split()[0]==s['candidate_hash']
        adb(['shell','run-as',PKG,'mv',CACHE+'.phase14',CACHE],P/'activate-cache.txt')
        s['deployed']=True;save()
        adb(['shell','am','start','-W','-n',PKG+'/com.ss.android.ugc.aweme.splash.SplashActivity'],P/'launch.txt',8)
        time.sleep(1);collect('loaded')
        events=[json.loads(l) for l in (P/'loaded-bridge.jsonl').read_text().splitlines()]
        # Startup JSONL is buffered until frames arrive. Confirm after opening camera if empty.
        s['load_pending']=not s.get('load_pass',False)
    elif a.stage=='nav':
        assert s.get('load_pass') or (s.get('deployed') and a.nav in ['create','home'])
        if a.nav=='home':adb(['shell','input','keyevent','KEYCODE_HOME'])
        else:
            xy={'create':('540','2180'),'live-tab':('914','2175'),'close-preview':('78','132')}[a.nav]
            adb(['shell','input','tap',*xy])
        time.sleep(1)
        collect(a.label)
    elif a.stage=='capture':
        assert s.get('load_pass');collect(a.label,True)
    elif a.stage=='retired':
        assert s.get('load_pass') and not s.get('retired_replayed')
        adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_RETIRED_CLOSE','-p',PKG],P/'retired-replay.txt')
        s['retired_replayed']=True;time.sleep(2);collect('after-retired',True)
    elif a.stage=='finish':
        if s.get('load_pass'):
            adb(['shell','input','keyevent','KEYCODE_HOME']);time.sleep(.8);collect('final',True)
        adb(['shell','sha256sum',s['base']],P/'base-after.txt',6)
        adb(['shell','run-as',PKG,'sha256sum',CACHE],P/'cache-after.txt')
        adb(['logcat','-b','crash','-d','-t','120','-v','threadtime'],T/'crash-buffer.txt',5,optional=True)
        s['spent']=elapsed();s['active']=None;s['finished']=True
    elif a.stage=='rollback':
        guard_idle();assert s.get('deployed')
        adb(['shell','am','force-stop',PKG])
        adb(['push',str(T/'rollback-module.apk'),'/data/local/tmp/sarae-phase14-rollback.apk'],timeout=6)
        adb(['shell','run-as',PKG,'cp','/data/local/tmp/sarae-phase14-rollback.apk',CACHE+'.restore'])
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE+'.restore']).split()[0]==OLD
        adb(['shell','run-as',PKG,'mv',CACHE+'.restore',CACHE])
        s['rolled_back']=True;s['spent']=elapsed();s['active']=None;s['finished']=True
finally:
    save();print(json.dumps({'stage':a.stage,'elapsed_device_seconds':s['spent'] if s['finished'] or (a.stage=='inventory' and s.get('inventory_pass')) else elapsed(),'state':s},indent=2),flush=True)
