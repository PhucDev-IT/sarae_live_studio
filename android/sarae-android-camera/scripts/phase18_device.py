"""Native-load-only target module deployment and bounded non-LIVE preview.

No install/uninstall/clear, recorder creation, native interception or PCM change.
State is persistent; cache mutations require HOME, camera idle, recording idle.
"""
import argparse,datetime,hashlib,json,re,subprocess,time
from pathlib import Path
P=Path('test-results/phase18');T=Path('.tools/phase18');P.mkdir(exist_ok=True);T.mkdir(exist_ok=True)
STATE=P/'device-state.json';PKG='com.ss.android.ugc.trill';FILES=f'/sdcard/Android/data/{PKG}/files'
CACHE='cache/lspatch/co.sarae.camera.poc/183496786.apk';OLD='53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351'
ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['inventory','deploy','snapshot','create','finish','rollback']);parser.add_argument('--label',default='snapshot')
parser.add_argument('--tap-x',type=int);parser.add_argument('--tap-y',type=int)
a=parser.parse_args();assert re.fullmatch('[a-z0-9-]{1,32}',a.label)
s=json.loads(STATE.read_text()) if STATE.exists() else {'spent':0,'active':None,'finished':False,'liveStarted':False}
assert not s['finished'],'Phase 18 verification already finished'
start=time.monotonic()
def elapsed():return s['spent']+time.monotonic()-(s['active'] if s['active'] is not None else start)
def save():STATE.write_text(json.dumps(s,indent=2))
def adb(command,name=None,timeout=5,optional=False,binary=False):
    assert not any(x in command for x in ['install','uninstall','clear','install-create','install-commit'])
    remaining=(295 if a.stage in ['finish','rollback'] else 245)-elapsed();assert remaining>1,'Device budget reserved for cleanup'
    before=time.monotonic();entry={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':command,'timeoutSeconds':min(timeout,remaining)}
    try:
        r=subprocess.run([ADB,*command],capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
        entry['exitCode']=r.returncode
        if name:Path(name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(f'{command}: {r.stderr.decode(errors="replace")}')
        return r.stdout if binary else r.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:entry['timedOut']=True;raise
    finally:
        entry['seconds']=time.monotonic()-before
        with (P/'adb.jsonl').open('a') as f:f.write(json.dumps(entry)+'\n')
def guard_idle(label):
    activity=adb(['shell','dumpsys','activity','activities'],T/(label+'-activity.txt'),6)
    assert any('com.sec.android.app.launcher' in l for l in activity.splitlines() if 'topResumedActivity=' in l),'User activity foreground: do not update/stop'
    camera=adb(['shell','dumpsys','media.camera'],T/(label+'-camera.txt'),6)
    assert camera.split('Active Camera Clients:',1)[1].split('Allowed user IDs:',1)[0].strip()=='[]','Camera in use: do not update/stop'
    af=adb(['shell','dumpsys','media.audio_flinger'],T/(label+'-audioflinger.txt'),6)
    assert not re.search(r'(?mi)^\s*Record thread|type[^\n]*\(RECORD\)',af),'Input thread in use: do not update/stop'
    audio=adb(['shell','dumpsys','audio'],T/(label+'-audio.txt'),5)
    current=audio.split('RecordActivityMonitor dump time:',1)[1].split('Events log: recording activity',1)[0]
    assert len(current.splitlines())<=3,'Recording configurations present/unknown: stop'
def collect(label,full=False):
    pid=adb(['shell','pidof',PKG],P/(label+'-pid.txt')).strip();assert re.fullmatch(r'\d+',pid);s['pid']=pid
    native=''
    for attempt in range(6):
        native=adb(['shell','cat',FILES+'/phase18/native-'+pid+'.jsonl'],P/(label+'-native.jsonl'),optional=True)
        if 'native_loaded' in native or 'native_observer_error' in native:break
        time.sleep(.5)
    rows=[json.loads(line) for line in native.splitlines()]
    if any(e['event']=='native_observer_error' for e in rows):raise RuntimeError('Target native load/metadata error; stop and inspect')
    loads=[e for e in rows if e['event']=='native_loaded']
    assert loads,'Native load not confirmed; do not continue target preview test'
    if loads:
        expected=json.loads((P/'candidate.json').read_text())['observerSoSha256']
        assert loads[-1]['sha256']==expected
        library=loads[-1]['library'];assert f'/{PKG}/code_cache/sarae-native-observer/' in library
        assert adb(['shell','run-as',PKG,'sha256sum',library],P/(label+'-loaded-so-hash.txt')).split()[0]==expected
        s['nativeLoadPass']=True
    for kind,folder in [('bridge','phase6'),('audio','phase11')]:
        adb(['pull',FILES+f'/{folder}/{kind}-{pid}.jsonl',str(P/(label+'-'+kind+'.jsonl'))],timeout=5,optional=True)
    if full:
        adb(['exec-out','run-as',PKG,'cat',f'/proc/{pid}/maps'],T/(label+'-maps.txt'),optional=True)
        adb(['shell','dumpsys','media.camera'],T/(label+'-camera.txt'),6)
        adb(['shell','dumpsys','media.audio_flinger'],T/(label+'-audioflinger.txt'),6)
        adb(['logcat','-d','-t','1100','-v','threadtime','--pid='+pid],T/(label+'-logcat.txt'),5)
    adb(['shell','screencap','-p','/sdcard/sarae-phase18.png'],timeout=4)
    adb(['pull','/sdcard/sarae-phase18.png',str(T/(label+'.png'))],timeout=4)
try:
    if a.stage=='inventory':
        assert not s.get('inventoryPass')
        guard_idle('inventory-idle')
        assert adb(['shell','getprop','ro.product.model'],P/'model.txt').strip()=='SM-G996U'
        assert adb(['shell','getprop','ro.build.version.sdk'],P/'api.txt').strip()=='34'
        paths=adb(['shell','pm','path',PKG],P/'package-paths-before.txt').splitlines();assert len(paths)==61
        s['base']=next(l.removeprefix('package:') for l in paths if l.endswith('/base.apk'))
        s['arm64Split']=next(l.removeprefix('package:') for l in paths if l.endswith('/split_config.arm64_v8a.apk'))
        assert adb(['shell','sha256sum',s['base']],P/'base-before.txt').split()[0]=='4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b'
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE],P/'module-before.txt').split()[0]==OLD
        data=adb(['exec-out','run-as',PKG,'cat',CACHE],T/'rollback-module.apk',binary=True)
        assert hashlib.sha256(data).hexdigest()==OLD
        adb(['shell','run-as',PKG,'ls','-l',CACHE],P/'module-mode-before.txt')
        adb(['pull',s['arm64Split'],str(T/'installed-arm64.apk')],P/'pull-installed-arm64.txt',10)
        adb(['shell','sha256sum',FILES+'/A.mp4',FILES+'/B.mp4'],P/'media-before.txt',6)
        adb(['shell','cat',FILES+'/sarae-render.json'],P/'render-before.json')
        s['inventoryPass']=True
    elif a.stage=='deploy':
        assert s.get('inventoryPass') and not s.get('deployed')
        assert json.loads((P/'candidate.json').read_text())['staticPass']
        assert json.loads((P/'target-identity.json').read_text())['freshInstalledSplitVerified']
        assert hashlib.sha256((T/'rollback-module.apk').read_bytes()).hexdigest()==OLD
        s['active']=time.monotonic();save();guard_idle('before-deploy')
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE]).split()[0]==OLD
        s['candidateSha256']=hashlib.sha256((T/'module.apk').read_bytes()).hexdigest()
        # Allowed only after HOME + idle camera/recording checks above.
        adb(['shell','am','force-stop',PKG],P/'stop-idle-before-update.txt')
        adb(['shell','run-as',PKG,'cp',CACHE,CACHE+'.phase18-rollback'],P/'backup-remote.txt')
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE+'.phase18-rollback'],P/'backup-remote-hash.txt').split()[0]==OLD
        adb(['push',str(T/'module.apk'),'/data/local/tmp/sarae-phase18-module.apk'],P/'push-module.txt',6)
        adb(['shell','run-as',PKG,'cp','/data/local/tmp/sarae-phase18-module.apk',CACHE+'.phase18'],P/'write-module.txt')
        assert adb(['shell','run-as',PKG,'sha256sum',CACHE+'.phase18'],P/'candidate-device-hash.txt').split()[0]==s['candidateSha256']
        adb(['shell','run-as',PKG,'mv',CACHE+'.phase18',CACHE],P/'activate-module.txt')
        s['deployed']=True;save()
        adb(['shell','am','start','-W','-n',PKG+'/com.ss.android.ugc.aweme.splash.SplashActivity'],P/'launch.txt',8)
        time.sleep(1);collect('loaded',True)
    elif a.stage=='snapshot':
        assert s.get('deployed');collect(a.label,True)
    elif a.stage=='create':
        assert s.get('nativeLoadPass')
        # Inspect loaded.png before supplying the observed create-button coordinate.
        assert a.tap_x is not None and a.tap_y is not None
        assert 0<a.tap_x<1080 and 0<a.tap_y<2400
        activity=adb(['shell','dumpsys','activity','activities'],T/'before-create-activity.txt',6)
        assert any(PKG in line for line in activity.splitlines() if 'topResumedActivity=' in line)
        s['createTap']=[a.tap_x,a.tap_y];save()
        adb(['shell','input','tap',str(a.tap_x),str(a.tap_y)])
        time.sleep(3);collect('preview',True)
    elif a.stage in ['finish','rollback']:
        if s.get('deployed'):
            adb(['shell','input','keyevent','KEYCODE_HOME']);time.sleep(.5)
            if a.stage=='finish':collect('final',True)
        if a.stage=='rollback':
            guard_idle('before-rollback')
            adb(['shell','am','force-stop',PKG],P/'stop-idle-before-rollback.txt')
            adb(['push',str(T/'rollback-module.apk'),'/data/local/tmp/sarae-phase18-rollback.apk'],timeout=6)
            adb(['shell','run-as',PKG,'cp','/data/local/tmp/sarae-phase18-rollback.apk',CACHE+'.restore'])
            assert adb(['shell','run-as',PKG,'sha256sum',CACHE+'.restore']).split()[0]==OLD
            adb(['shell','run-as',PKG,'mv',CACHE+'.restore',CACHE]);s['rolledBack']=True
        adb(['shell','sha256sum',s['base']],P/'base-after.txt')
        adb(['shell','run-as',PKG,'sha256sum',CACHE],P/'module-after.txt')
        adb(['shell','sha256sum',FILES+'/A.mp4',FILES+'/B.mp4'],P/'media-after.txt',6)
        adb(['shell','cat',FILES+'/sarae-render.json'],P/'render-after.json')
        adb(['logcat','-b','crash','-d','-t','100','-v','threadtime'],T/'crash-buffer.txt',5,optional=True)
        s['spent']=elapsed();s['active']=None;s['finished']=True
finally:
    if a.stage=='inventory':s['spent']+=time.monotonic()-start
    save();print(json.dumps({'stage':a.stage,'elapsedDeviceSeconds':s['spent'] if s['finished'] or a.stage=='inventory' else elapsed(),'state':s},indent=2))
