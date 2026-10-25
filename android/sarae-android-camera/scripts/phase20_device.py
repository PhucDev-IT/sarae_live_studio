"""One bounded LSPatch/owned-recorder observation run. TikTok stays unchanged."""
import datetime,hashlib,json,subprocess,time,sys,re,shutil
from pathlib import Path
P=Path('test-results/phase20');T=Path('.tools/phase20')
prior_seconds=0
if (P/'device-state.json').exists():
    previous=json.loads((P/'device-state.json').read_text())
    if sys.argv[1:]==['--diagnostic-retry']:
        assert not previous.get('diagnosticRetry') and not previous.get('suiteDone') and previous.get('targetPreserved')
        failed=[json.loads(line) for line in (T/previous['run']/'events.jsonl').read_text().splitlines()]
        assert any(e['event']=='binding_valid' and e['stats'][6]==-43 for e in failed),'Only the preserved first pin failure can be diagnosed once'
    else:
        assert sys.argv[1:]==['--resume-idle-check'] and 'targetBaseBefore' not in previous and 'ownPid' not in previous,'Only preflight may resume'
    prior_seconds=previous['deviceSeconds']
    shutil.copy2(P/'device-state.json',P/'initial-preflight-state.json')
artifacts=json.loads((P/'artifacts.json').read_text())
ADB='D:/window/Android/Sdk/platform-tools/adb.exe';OWN='co.sarae.camera.nativeaudiotest';TARGET='com.ss.android.ugc.trill'
CACHE='cache/lspatch/co.sarae.camera.poc/183496786.apk'
OLD='66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120'
start=time.monotonic();launched=False;base=None
run='run-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S')
state={'run':run,'utcStart':datetime.datetime.now(datetime.timezone.utc).isoformat(),'targetModified':False,'liveStarted':False}
state['priorDeviceSeconds']=prior_seconds
state['diagnosticRetry']=sys.argv[1:]==['--diagnostic-retry']
def adb(command,name=None,timeout=5,optional=False):
    left=290-prior_seconds-(time.monotonic()-start);assert left>1
    assert not any(x in command for x in ['uninstall','clear','force-stop'])
    if 'install' in command:assert command==['install','-r',artifacts['patchedHarness']]
    before=time.monotonic();row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':command,'timeoutSeconds':min(timeout,left)}
    try:
        r=subprocess.run([ADB,*command],capture_output=True,timeout=min(timeout,left),creationflags=subprocess.CREATE_NO_WINDOW)
        row['exitCode']=r.returncode
        if name:Path(name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(f'{command}: {r.stdout.decode(errors="replace")} {r.stderr.decode(errors="replace")}')
        return r.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        row['timedOut']=True;raise
    finally:
        row['seconds']=time.monotonic()-before
        with (P/'adb.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
try:
    activity=adb(['shell','dumpsys','activity','activities'],T/'activity-before.txt',6)
    assert any('com.sec.android.app.launcher' in line for line in activity.splitlines() if 'topResumedActivity=' in line),'User activity ongoing; stop without interrupting it'
    assert adb(['shell','getprop','ro.product.model'],P/'model.txt').strip()=='SM-G996U'
    assert adb(['shell','getprop','ro.build.version.sdk'],P/'api.txt').strip()=='34'
    assert adb(['shell','getprop','ro.product.cpu.abi'],P/'abi.txt').strip()=='arm64-v8a'
    target_pid=adb(['shell','pidof',TARGET],P/'target-pid-before.txt',optional=True).strip();state['targetPidBefore']=target_pid
    camera=adb(['shell','dumpsys','media.camera'],T/'idle-camera.txt',6)
    assert camera.split('Active Camera Clients:',1)[1].split('Allowed user IDs:',1)[0].strip()=='[]','Camera active; stop before install'
    if target_pid:
        af=adb(['shell','dumpsys','media.audio_flinger'],T/'idle-before-recorder-audioflinger.txt',6)
        assert not re.search(r'(?mi)^\s*Record thread|type[^\n]*\(RECORD\)',af),'An active input thread exists; do not start a competing recorder'
        audio=adb(['shell','dumpsys','audio'],T/'idle-before-recorder-audio.txt',5)
        recording=audio.split('RecordActivityMonitor dump time:',1)[1].split('Events log: recording activity',1)[0]
        assert len(recording.splitlines())<=3,'Recording configuration present or unknown; do not start a competing recorder'
        state['backgroundTargetHasNoRecordingConfiguration']=True
    assert adb(['shell','run-as',TARGET,'sha256sum',CACHE],P/'target-module-before.txt').split()[0]==OLD
    adb(['exec-out','run-as',TARGET,'cat',CACHE],T/'tiktok-phase18-rollback.apk',8)
    assert hashlib.sha256((T/'tiktok-phase18-rollback.apk').read_bytes()).hexdigest()==OLD
    paths=adb(['shell','pm','path',TARGET],P/'target-paths.txt').splitlines()
    base=next(p.removeprefix('package:') for p in paths if p.endswith('/base.apk'))
    state['targetBaseBefore']=adb(['shell','sha256sum',base],P/'target-base-before.txt').split()[0]
    own_path=adb(['shell','pm','path',OWN],P/'own-path-before.txt').strip().removeprefix('package:')
    if own_path:
        adb(['pull',own_path,str(T/'rollback-own-harness.apk')],P/'backup-own.txt',8)
        state['ownRollbackSha256']=hashlib.sha256((T/'rollback-own-harness.apk').read_bytes()).hexdigest()
    adb(['install','-r',artifacts['patchedHarness']],P/'install-own.txt',30)
    adb(['shell','pm','grant',OWN,'android.permission.RECORD_AUDIO'],P/'grant-own.txt')
    files=f'/sdcard/Android/data/{OWN}/files'
    adb(['shell','mkdir','-p',files])
    adb(['push','.tools/phase15/source.mp4',files+'/source.mp4'],P/'push-source.txt',5)
    adb(['push',str(P/'owned-profile.json'),files+'/phase20-owned.json'],P/'push-owned-profile.txt',5)
    adb(['push',str(P/'target-profile.json'),files+'/phase20-target.json'],P/'push-target-profile.txt',5)
    state['launchElapsedSeconds']=time.monotonic()-start
    adb(['shell','am','start','-W','-n',OWN+'/.RecorderActivity','--ei','seconds','10','--ez','phase20','true','--es','runId',run],P/'launch.txt',8)
    launched=True
    deadline=time.monotonic()+50
    while True:
        assert time.monotonic()<deadline,'Observation run exceeded 50 s; do not repeat'
        status=adb(['shell','ls',files+'/phase20/'+run+'/DONE',files+'/phase20/'+run+'/ERROR'],optional=True,timeout=3)
        if '/ERROR' in status:raise RuntimeError('Harness reported ERROR; inspect evidence before further action')
        if '/DONE' in status:state['suiteDone']=True;break
        time.sleep(2)
finally:
    try:
        if launched:
            pid=adb(['shell','pidof',OWN],P/'own-pid.txt',optional=True).strip();state['ownPid']=pid
            if pid:
                adb(['exec-out','run-as',OWN,'cat',f'/proc/{pid}/maps'],T/'own-maps.txt',optional=True)
                adb(['logcat','-d','-t','850','-v','threadtime','--pid='+pid],T/'own-logcat.txt',6,optional=True)
            adb(['shell','input','keyevent','KEYCODE_HOME'],P/'home.txt');time.sleep(.4)
            adb(['pull',files+'/phase20/'+run,str(T/run)],P/'pull-run.txt',8,optional=True)
            adb(['pull',files+'/phase17-module.jsonl',str(P/'module-events.jsonl')],P/'pull-module-events.txt',5,optional=True)
            if (P/'module-events.jsonl').exists():
                loaded=[json.loads(s) for s in (P/'module-events.jsonl').read_text().splitlines() if json.loads(s)['event']=='native_loaded']
                if loaded:
                    library=loaded[-1]['library']
                    assert f'/{OWN}/code_cache/sarae-phase17/' in library and library.endswith('/libsarae_native_observer.so')
                    state['loadedObserverSha256']=adb(['shell','run-as',OWN,'sha256sum',library],P/'loaded-so-hash.txt').split()[0]
            adb(['logcat','-b','crash','-d','-t','100','-v','threadtime'],T/'crash-buffer.txt',5,optional=True)
        state['targetModuleAfter']=adb(['shell','run-as',TARGET,'sha256sum',CACHE],P/'target-module-after.txt').split()[0]
        if base:
            state['targetBaseAfter']=adb(['shell','sha256sum',base],P/'target-base-after.txt').split()[0]
            state['targetPreserved']=state['targetModuleAfter']==OLD and state['targetBaseAfter']==state['targetBaseBefore']
        state['targetPidAfter']=adb(['shell','pidof',TARGET],P/'target-pid-after.txt',optional=True).strip()
    finally:
        state['thisRunSeconds']=time.monotonic()-start
        state['deviceSeconds']=prior_seconds+state['thisRunSeconds']
        state['utcEnd']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        (P/'device-state.json').write_text(json.dumps(state,indent=2));print(json.dumps(state,indent=2))
