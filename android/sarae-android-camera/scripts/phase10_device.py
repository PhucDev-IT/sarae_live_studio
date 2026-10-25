"""One bounded Phase 10 execution. Recording/save controls belong to the user."""
import argparse, datetime, hashlib, json, re, subprocess, time
from pathlib import Path

ROOT=Path('test-results/phase10'); STATE=ROOT/'device-state.json'
ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
PACKAGE='com.ss.android.ugc.trill'; FILES=f'/sdcard/Android/data/{PACKAGE}/files'
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v): p.write_text(json.dumps(v,indent=2),encoding='utf-8')
def journal(name,v):
    with (ROOT/name).open('a',encoding='utf-8') as f: f.write(json.dumps(v)+'\n')
parser=argparse.ArgumentParser(); parser.add_argument('stage',choices=['update','update-fix','pause','observe','mode','switch','finish'])
parser.add_argument('--value',default='MICROPHONE'); parser.add_argument('--label',default='observation')
args=parser.parse_args()
assert re.fullmatch('[a-zA-Z0-9-]{1,40}',args.label)
if args.stage=='update':
    assert not STATE.exists(),'Do not repeat the install/test execution'
    prior=sum(loads['seconds'] for loads in [json.loads(l) for l in (ROOT/'device-budget.jsonl').read_text().splitlines()])
    s={'spent_seconds':prior,'active_start':None,'switches':0,'finished':False}
else: s=load(STATE)
assert not s['finished'],'Device testing has finished'
start=time.monotonic()
def elapsed(): return s['spent_seconds']+(time.monotonic()-s['active_start'] if s['active_start'] is not None else time.monotonic()-start)
def adb(arguments,name=None,timeout=8,optional=False):
    # Leave 35 seconds for collection and pass-through restoration.
    remaining=(295 if args.stage=='finish' else 260)-elapsed(); assert remaining>1,'Phase 10 hard budget exhausted'
    begin=time.monotonic(); row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':arguments}
    try:
        r=subprocess.run([ADB,*arguments],capture_output=True,timeout=min(timeout,remaining),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        row['exit_code']=r.returncode
        if name:
            if args.stage=='update-fix': name='fixed-'+name.replace('private/','private-')
            (ROOT/name).parent.mkdir(parents=True,exist_ok=True);(ROOT/name).write_bytes(r.stdout)
        if r.returncode and not optional: raise RuntimeError(r.stderr.decode(errors='replace'))
        return r.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        row['timed_out']=True
        if not optional: raise
        return ''
    finally: row['seconds']=time.monotonic()-begin;journal('adb-commands.jsonl',row)
def shot(label):
    adb(['shell','screencap','-p','/sdcard/sarae-phase10.png'],timeout=4)
    adb(['pull','/sdcard/sarae-phase10.png',str(ROOT/f'{label}.png')],timeout=4)
def logs(label):
    pid=adb(['shell','pidof',PACKAGE],f'{label}-pid.txt',timeout=3).strip()
    assert re.fullmatch(r'\d+',pid),'Main PID missing/ambiguous'
    adb(['pull',f'{FILES}/phase10/audio-{pid}.jsonl',str(ROOT/f'{label}-audio.jsonl')],optional=True)
    adb(['pull',f'{FILES}/phase6/bridge-{pid}.jsonl',str(ROOT/f'{label}-bridge.jsonl')],optional=True)
    adb(['logcat','-d','-t','3000','-v','threadtime',f'--pid={pid}'],f'private/{label}-pid-logcat.txt',timeout=4)
    return pid
try:
    if args.stage in ['update','update-fix']:
        fixed=args.stage=='update-fix'
        if fixed: assert s.get('paused') and s['active_start'] is None
        assert adb(['get-state']).strip()=='device'
        assert adb(['shell','getprop','ro.product.model']).strip()=='SM-G996U'
        package=adb(['shell','dumpsys','package',PACKAGE],'private/package-before.txt')
        assert 'versionName=47.4.3' in package
        paths=adb(['shell','pm','path',PACKAGE],'paths-before.txt').splitlines();assert len(paths)==61
        base=next(p.removeprefix('package:').strip() for p in paths if p.strip().endswith('/base.apk'))
        expected=load(ROOT/'update-static.json')['base_sha256'] if fixed else '201abaaca8432a512ab283686c11014a399f74eeae0f4bd508393b084aafbe8b'
        assert adb(['shell','sha256sum',base],'installed-base-before.txt').split()[0]==expected
        if fixed:
            adb(['pull','/sdcard/DCIM/Camera/2026-10-24-093237572.mp4','.tools/phase10/microphone-probe.mp4'],'pull-microphone-probe.txt')
        gate=load(ROOT/('ring-fixed-update-static.json' if fixed else 'update-static.json'))
        apk=Path('.tools/phase10')/('ring-fixed' if fixed else 'patched')/'base-487-lspatched.apk'
        assert hashlib.sha256(apk.read_bytes()).hexdigest()==gate['base_sha256']
        size=str(apk.stat().st_size)
        adb(['push',str(apk),'/data/local/tmp/sarae-phase10-base.apk'],'push-base.txt',20)
        created=adb(['shell','pm','install-create','-r','-p',PACKAGE,'-S',size],'install-create.txt')
        install=re.search(r'\[(\d+)\]',created);assert install is not None
        install_id=install[1]
        try:
            assert 'Success' in adb(['shell','pm','install-write','-S',size,install_id,'base','/data/local/tmp/sarae-phase10-base.apk'],'install-write.txt',20)
            assert adb(['shell','pm','install-commit',install_id],'install-commit.txt',20).strip()=='Success'
        except Exception:
            adb(['shell','pm','install-abandon',install_id],'install-abandon.txt',4,True);raise
        adb(['shell','am','start','-W','-n',PACKAGE+'/com.ss.android.ugc.aweme.splash.SplashActivity'],'launch.txt',12)
        time.sleep(2)
        adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_SELF_TEST','-p',PACKAGE,'--es','source','A'],'selftest-command.txt')
        time.sleep(3)
        pid=logs('installed')
        adb(['pull',f'{FILES}/phase10/selftest-A-s16-48000-mono.pcm','.tools/phase10/'+('fixed-' if fixed else '')+'selftest-A-s16-48000-mono.pcm'],'selftest-pull.txt',5,True)
        shot('installed-screen')
        after=adb(['shell','dumpsys','package',PACKAGE],'private/package-after.txt')
        before_first=re.search(r'firstInstallTime=(.*)',package)[1]
        assert re.search(r'firstInstallTime=(.*)',after)[1]==before_first,'First install time changed'
        assert len(adb(['shell','pm','path',PACKAGE],'paths-after.txt').splitlines())==61
        s['pid']=pid;s['spent_seconds']+=time.monotonic()-start;s['active_start']=time.monotonic();s['paused']=False
    elif args.stage=='pause':
        # Only after the captured screenshot confirms the user stopped recording.
        logs(args.label)
        s['spent_seconds']=elapsed();s['active_start']=None;s['paused']=True
    elif args.stage=='observe':
        logs(args.label);shot(args.label)
        adb(['shell','dumpsys','media.audio_flinger'],f'private/{args.label}-audio-flinger.txt',5)
        adb(['shell','dumpsys','audio'],f'private/{args.label}-audio-service.txt',5)
    elif args.stage=='mode':
        assert args.value in ['VIDEO','MICROPHONE','MUTED']
        adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_MODE','-p',PACKAGE,'--es','mode',args.value],f'mode-{args.value}.txt')
        logs(args.label)
    elif args.stage=='switch':
        assert args.value in ['A','B'] and s['switches']<3
        adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p',PACKAGE,'--es','source',args.value,'--es','mode','now'],f'switch-{s["switches"]+1}.txt')
        s['switches']+=1
    elif args.stage=='finish':
        # No recording/save/post/LIVE controls. Restore pass-through only.
        adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_MODE','-p',PACKAGE,'--es','mode','MICROPHONE'],'restore-microphone.txt',4,True)
        logs('final');shot('final')
        rows=adb(['shell','content','query','--uri','content://media/external/video/media','--projection','_id:_data:date_added:duration','--where','"_id>1000074320"','--sort','"date_added DESC"'],'new-videos.txt')
        for index,path in enumerate(re.findall(r'_data=([^,\r\n]+)',rows)):
            if index>=3:break
            adb(['pull',path,str(Path('.tools/phase10')/f'recording-{index+1}.mp4')],f'pull-recording-{index+1}.txt',8,True)
        adb(['logcat','-b','crash','-d','-t','150','-v','threadtime'],'crash-buffer.txt',4,True)
        s['spent_seconds']=elapsed();s['active_start']=None;s['finished']=True
finally:
    save(STATE,s)
    print(json.dumps({'device_seconds':elapsed(),'remaining_seconds':300-elapsed(),'state':s},indent=2),flush=True)
