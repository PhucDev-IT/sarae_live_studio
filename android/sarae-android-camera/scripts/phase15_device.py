"""One bounded independent-recorder run. Never launches or changes TikTok."""
import datetime,hashlib,json,subprocess,time,sys,shutil
from pathlib import Path

P=Path('test-results/phase15');T=Path('.tools/phase15')
P.mkdir(exist_ok=True);T.mkdir(exist_ok=True)
STATE=P/'device-state.json'
prior=None
if STATE.exists():
    assert sys.argv[1:] in [['--verify-correction'],['--verify-harness-protocol']],'A run already exists; do not repeat without a concrete correction'
    prior=json.loads(STATE.read_text())
    protocol=sys.argv[1:] == ['--verify-harness-protocol']
    if protocol:
        assert prior.get('correction_run') and prior['capture_done_elapsed_seconds']<5 and not prior.get('protocol_correction'),'Only correct the evidenced stale completion marker'
    else:assert prior.get('suite_done') and not prior.get('correction_run'),'Only one targeted correction verification is allowed'
    assert prior['total_device_seconds']<200
    # Preserve the failed original recordings, logs and analysis, byte for byte.
    label='cancelled-correction' if protocol else 'initial'
    shutil.copytree(T/'device',T/(label+'-device'))
    shutil.copy2(STATE,P/(label+'-device-state.json'))
    if not protocol:
        shutil.copy2(P/'analysis.json',P/'initial-analysis.json')
        shutil.copy2(P/'events.jsonl',P/'initial-events.jsonl')
ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
OWN='co.sarae.camera.nativeaudiotest';TARGET='com.ss.android.ugc.trill'
CACHE='cache/lspatch/co.sarae.camera.poc/183496786.apk'
EXPECTED='53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351'
start=time.monotonic();launched=False
state={'utc_start':datetime.datetime.now(datetime.timezone.utc).isoformat(),'live_started':False,'target_modified':False}
prior_seconds=prior['total_device_seconds'] if prior else 0
state['correction_run']=bool(prior)
state['protocol_correction']=sys.argv[1:]==['--verify-harness-protocol']
state['prior_device_seconds']=prior_seconds
run_id='run-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S')
state['run_id']=run_id

def adb(args,name=None,timeout=5,optional=False):
    remaining=290-prior_seconds-(time.monotonic()-start)
    assert remaining>1,'Device budget exhausted'
    timeout=min(timeout,remaining)
    before=time.monotonic();entry={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':args,'timeout':timeout}
    # Installation is restricted to our independent APK; no target mutators allowed.
    assert not any(x in args for x in ['uninstall','clear','force-stop','install-create','install-commit'])
    if 'install' in args:assert args==['install','-r','native-audio-test/build/outputs/apk/debug/native-audio-test-debug.apk']
    try:
        r=subprocess.run([ADB,*args],capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
        entry['exit_code']=r.returncode
        if name:Path(name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(f'{args}: {r.stderr.decode(errors="replace")} {r.stdout.decode(errors="replace")}')
        return r.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        entry['timed_out']=True;raise
    finally:
        entry['seconds']=time.monotonic()-before
        with (P/'adb.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(entry)+'\n')

try:
    activity=adb(['shell','dumpsys','activity','activities'],T/'activity-before.txt',6)
    assert any('com.sec.android.app.launcher' in line for line in activity.splitlines() if 'topResumedActivity=' in line),'User activity ongoing: do not replace the foreground app'
    assert adb(['shell','getprop','ro.product.model'],P/'model.txt').strip()=='SM-G996U'
    assert adb(['shell','getprop','ro.build.version.sdk'],P/'api.txt').strip()=='34'
    assert adb(['shell','getprop','ro.product.cpu.abi'],P/'abi.txt').strip()=='arm64-v8a'
    assert adb(['shell','run-as',TARGET,'sha256sum',CACHE],P/'module-before.txt').split()[0]==EXPECTED
    paths=adb(['shell','pm','path',TARGET],P/'target-paths-before.txt')
    base=next(line.removeprefix('package:') for line in paths.splitlines() if line.endswith('/base.apk'))
    base_hash=adb(['shell','sha256sum',base],P/'target-base-before.txt').split()[0]
    state['target_pid_before']=adb(['shell','pidof',TARGET],P/'target-pid-before.txt',optional=True).strip()
    adb(['install','-r','native-audio-test/build/outputs/apk/debug/native-audio-test-debug.apk'],P/'install-own.txt',30)
    adb(['shell','pm','grant',OWN,'android.permission.RECORD_AUDIO'],P/'grant-own.txt')
    files=f'/sdcard/Android/data/{OWN}/files'
    adb(['shell','mkdir','-p',files])
    adb(['push',str(T/'source.mp4'),files+'/source.mp4'],P/'push-source.txt',8)
    assert adb(['shell','sha256sum',files+'/source.mp4'],P/'source-device-hash.txt').split()[0]==hashlib.sha256((T/'source.mp4').read_bytes()).hexdigest()
    # Exactly one foreground harness run: MICROPHONE -> VIDEO -> MUTED, 12 s each.
    state['launch_elapsed_seconds']=time.monotonic()-start
    adb(['shell','am','start','-W','-n',OWN+'/.RecorderActivity','--ei','seconds','12','--es','runId',run_id],P/'launch.txt',8)
    launched=True
    deadline=time.monotonic()+75
    while True:
        if time.monotonic()>deadline:raise TimeoutError('Harness suite exceeded 75 s; no retry')
        status=adb(['shell','ls',files+'/phase15/'+run_id+'/DONE',files+'/phase15/'+run_id+'/ERROR'],timeout=3,optional=True)
        if '/ERROR' in status:raise RuntimeError('Harness reported ERROR')
        if '/DONE' in status:state['suite_done']=True;break
        time.sleep(3)
    state['capture_done_elapsed_seconds']=time.monotonic()-start
finally:
    # Returning our harness to HOME cancels its own recorder if a run failed.
    # Never stop, restart, reinstall or clear the target process.
    try:
        if launched:
            pid=adb(['shell','pidof',OWN],P/'own-pid.txt',optional=True).strip()
            adb(['shell','input','keyevent','KEYCODE_HOME'],P/'home.txt')
            # onStop is a cancellation gate; collect after worker teardown.
            time.sleep(.4)
            destination=T/'runs'/run_id;destination.parent.mkdir(exist_ok=True)
            adb(['pull',f'/sdcard/Android/data/{OWN}/files/phase15/'+run_id,str(destination)],P/'pull-output.txt',12,optional=True)
            if destination.exists():
                # Keep final analysis location stable; every run also retains its own directory.
                shutil.copytree(destination,T/'device',dirs_exist_ok=True)
            if pid:adb(['logcat','-d','-t','600','-v','threadtime','--pid='+pid],T/'own-logcat.txt',5,optional=True)
            adb(['shell','dumpsys','media.audio_flinger'],T/'audioflinger-after.txt',6,optional=True)
        state['module_after']=adb(['shell','run-as',TARGET,'sha256sum',CACHE],P/'module-after.txt').split()[0]
        state['target_base_after']=adb(['shell','sha256sum',base],P/'target-base-after.txt').split()[0] if 'base' in globals() else None
        state['target_pid_after']=adb(['shell','pidof',TARGET],P/'target-pid-after.txt',optional=True).strip()
        state['target_preserved']=state['module_after']==EXPECTED and state['target_base_after']==base_hash
        adb(['logcat','-b','crash','-d','-t','100','-v','threadtime'],T/'crash-buffer.txt',5,optional=True)
    finally:
        state['this_run_seconds']=time.monotonic()-start
        state['total_device_seconds']=prior_seconds+state['this_run_seconds']
        state['utc_end']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        STATE.write_text(json.dumps(state,indent=2),encoding='utf-8')
        print(json.dumps(state,indent=2))
