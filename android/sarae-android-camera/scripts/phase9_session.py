"""Bounded diagnostics for a human-started/ended LIVE. Never presses broadcast controls."""
import argparse,datetime,hashlib,json,re,subprocess,time,xml.etree.ElementTree as ET
from pathlib import Path

ROOT=Path('test-results/phase9'); STATE=ROOT/'session-state.json'
ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
PACKAGE='com.ss.android.ugc.trill'; FILES=f'/sdcard/Android/data/{PACKAGE}/files'
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def save(p,v): p.write_text(json.dumps(v,indent=2),encoding='utf-8')
def journal(name,row):
    with (ROOT/name).open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['preflight','prepare','snapshot','mark-live','switches','finish'])
parser.add_argument('--label',default='observation');parser.add_argument('--user-evidence',default='');parser.add_argument('--ended',action='store_true')
a=parser.parse_args()
if not re.fullmatch('[a-z0-9-]{1,40}',a.label):parser.error('invalid label')
if a.stage=='preflight':
    if STATE.exists():raise SystemExit('One Phase 9 execution only; preserve existing state/budget')
    ROOT.mkdir(parents=True,exist_ok=True)
    s={'created_utc':utc(),'spent_seconds':0,'active_start':None,'phase':'PREFLIGHT','live_switches':0}
    save(STATE,s)
else:s=load(STATE)
start=time.monotonic()
def elapsed():return s['spent_seconds']+(time.monotonic()-s['active_start'] if s['active_start'] is not None else (time.monotonic()-start if a.stage=='preflight' else 0))
def adb(args,name=None,timeout=10,cleanup=False,optional=False):
    remaining=(295 if cleanup else 260)-elapsed()
    if remaining<=1:raise RuntimeError('Device testing deadline reached; no further command')
    limit=min(timeout,remaining);begin=time.monotonic()
    row={'utc':utc(),'args':args,'timeout_seconds':limit}
    try:
        result=subprocess.run([ADB,*args],capture_output=True,timeout=limit,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=result.stdout;row['exit_code']=result.returncode
        if name:(ROOT/name).write_bytes(data)
        if result.returncode and not optional:raise RuntimeError(f'ADB {args}: {result.stderr.decode(errors="replace")}')
        return data.decode('utf-8',errors='replace')
    except subprocess.TimeoutExpired as e:
        if name:(ROOT/name).write_bytes(e.stdout or b'')
        row['exit_code']=124;row['timed_out']=True
        if not optional:raise RuntimeError(f'ADB timed out: {args}')
        return ''
    finally:
        row['seconds']=time.monotonic()-begin;journal('adb-commands.jsonl',row)
def shot(label,cleanup=False):
    remote=f'/sdcard/sarae-phase9-{label}.png'
    adb(['shell','screencap','-p',remote],timeout=5,cleanup=cleanup)
    adb(['pull',remote,str(ROOT/f'{label}.png')],timeout=5,cleanup=cleanup)
def ui(label):
    remote='/sdcard/sarae-phase9-ui.xml'
    adb(['shell','timeout','12','uiautomator','dump','--compressed',remote],f'{label}-dump.txt',timeout=15)
    adb(['pull',remote,str(ROOT/f'{label}.xml')],timeout=5)
    return ET.parse(ROOT/f'{label}.xml')
def pull_json(label,cleanup=False):
    pid=adb(['shell','pidof',PACKAGE],f'{label}-pid.txt',timeout=4,cleanup=cleanup).strip()
    if not re.fullmatch(r'\d+',pid):raise RuntimeError('Main PID unavailable/ambiguous')
    adb(['pull',f'{FILES}/phase6/bridge-{pid}.jsonl',str(ROOT/f'{label}-bridge.jsonl')],timeout=7,cleanup=cleanup)
    return pid
def mark(kind,detail):journal('protocol.jsonl',{'utc':utc(),'kind':kind,'elapsed_seconds':elapsed(),'detail':detail})
try:
    if a.stage=='preflight':
        assert adb(['get-state'],'device-state.txt').strip()=='device'
        model=adb(['shell','getprop','ro.product.model'],'model.txt').strip();assert model=='SM-G996U',model
        package=adb(['shell','dumpsys','package',PACKAGE],'package-before-full.txt',timeout=6)
        assert 'versionName=47.4.3' in package and 'versionCode=470403' in package
        (ROOT/'package-before.txt').write_text('\n'.join(l for l in package.splitlines() if any(k in l for k in ['codePath=','versionCode=','versionName=','firstInstallTime=','lastUpdateTime=','signatures='])),encoding='utf-8')
        paths=adb(['shell','pm','path',PACKAGE],'paths-before.txt').splitlines()
        base=next(p.removeprefix('package:').strip() for p in paths if p.strip().endswith('/base.apk'))
        static=load(Path('test-results/phase8/final-update-static.json'))
        remote_hash=adb(['shell','sha256sum',base],'installed-base-sha256.txt',timeout=8).split()[0]
        assert remote_hash==static['base_sha256'],'Installed APK differs from Phase 8: stop, no reinstall'
        local=Path('.tools/phase8/final/module.apk');assert hashlib.sha256(local.read_bytes()).hexdigest()==static['module_sha256']
        media=load(Path('test-results/phase8/source-media.json'))
        hashes=adb(['shell','sha256sum',f'{FILES}/A.mp4',f'{FILES}/B.mp4'],'media-sha256.txt').splitlines()
        assert [l.split()[0] for l in hashes]==[media[k]['sha256'] for k in ['A','B']]
        config=adb(['shell','cat',f'{FILES}/sarae-render.json'],'render-config.json')
        value=json.loads(config);assert value['autoCameraOrientation'] and value.get('rotationOverride') is None and not value.get('diagnosticCaptureLabel')
        shot('preflight-screen')
        pull_json('preflight')
        s['phase']='PREFLIGHT_PASS';s['installed_base_sha256']=remote_hash;s['module_sha256']=static['module_sha256'];s['split_count']=len(paths)
        mark('preflight_pass','Installed Phase 8 APK/module, A/B and auto configuration retained')
    elif a.stage=='prepare':
        assert s['phase']=='PREFLIGHT_PASS','No automatic additional rounds'
        assert elapsed()<80,'Keep manual LIVE and cleanup reserve'
        s['active_start']=time.monotonic();s['phase']='PREPARING';save(STATE,s)
        adb(['shell','am','start','-W','-n',PACKAGE+'/com.ss.android.ugc.aweme.splash.SplashActivity'],'launch.txt',timeout=10)
        # Set initial A through the existing scoped controller; no configuration/APK edits.
        adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p',PACKAGE,'--es','source','A','--es','mode','now'],'initial-A.txt')
        time.sleep(2)
        adb(['shell','input','tap','540','2196']) # Phase 8 verified create-camera entry.
        time.sleep(2)
        tree=ui('mode')
        if not any(n.get('text')=='Phát LIVE' for n in tree.iter('node')):
            tab=next((n for n in tree.iter('node') if n.get('text')=='LIVE' and n.get('package')==PACKAGE),None)
            assert tab is not None,'LIVE mode tab unavailable; no speculative taps'
            x1,y1,x2,y2=map(int,re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',tab.get('bounds')).groups())
            adb(['shell','input','tap',str((x1+x2)//2),str((y1+y2)//2)])
            time.sleep(2)
        tree=ui('prelive')
        assert any(n.get('text')=='Phát LIVE' for n in tree.iter('node')),'Expected pre-broadcast preview missing'
        shot('preview-A');s['pid']=pull_json('preview')
        s['phase']='AWAITING_HUMAN_START';mark('preview_ready','Start LIVE button not pressed; awaiting human start')
    elif a.stage=='snapshot':
        assert s['active_start'] is not None
        shot(a.label);pid=pull_json(a.label)
        mark('observation',f'{a.label} pid={pid}')
    elif a.stage=='mark-live':
        assert s['phase']=='AWAITING_HUMAN_START' and a.user_evidence,'Human start and visual evidence required'
        s['phase']='LIVE_CONFIRMED';s['human_start_evidence']=a.user_evidence
        mark('human_live_confirmed',a.user_evidence)
    elif a.stage=='switches':
        assert s['phase']=='LIVE_CONFIRMED' and s['live_switches']==0,'Only one 2-switch sequence after manual LIVE start'
        assert elapsed()<190,'Insufficient manual-end/cleanup reserve; skip switches'
        mark('initial_live_A','Existing source A; receiver should record motion and streaming delay')
        time.sleep(4)
        for index,source in enumerate(['B','A'],1):
            mark('live_switch_request',f'{index}: {source}')
            adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p',PACKAGE,'--es','source',source,'--es','mode','now'],f'live-switch-{index}-{source}.txt')
            s['live_switches']+=1;save(STATE,s)
            time.sleep(8)
            shot(f'live-switch-{index}-{source}')
            pid=adb(['shell','pidof',PACKAGE],f'live-switch-{index}-pid.txt').strip()
            assert pid==s['pid'],'PID changed during LIVE; stop switching'
        pull_json('live-after-switches');mark('live_switches_complete','2/2 commands sent; request human end after receiver observation')
    elif a.stage=='finish':
        # No tap, BACK, HOME, force-stop, or broadcast-start/end operation here.
        if a.ended:
            assert a.user_evidence,'Record human end evidence'
            mark('human_end_reported',a.user_evidence)
        shot('after-human-end' if a.ended else 'final-observation',cleanup=True)
        pull_json('final',cleanup=True)
        adb(['logcat','-b','crash','-d','-t','150','-v','threadtime'],'crash-buffer.txt',timeout=4,cleanup=True,optional=True)
        adb(['logcat','-d','-t','400','-v','threadtime',f'--pid={s.get("pid",0)}','SaraeBridge:V','LSPatch:V','LSPosed:V','AndroidRuntime:V','libc:V','DEBUG:V','*:S'],'relevant-logcat.txt',timeout=4,cleanup=True,optional=True)
        package=adb(['shell','dumpsys','package',PACKAGE],'package-after-full.txt',timeout=5,cleanup=True,optional=True)
        (ROOT/'package-after.txt').write_text('\n'.join(l for l in package.splitlines() if any(k in l for k in ['codePath=','versionCode=','versionName=','firstInstallTime=','lastUpdateTime=','signatures='])),encoding='utf-8')
        s['manual_end_reported']=a.ended;s['manual_end_evidence']=a.user_evidence
        s['spent_seconds']=elapsed();s['active_start']=None;s['phase']='DEVICE_OBSERVATION_FINISHED'
        mark('collection_finished','No automatic LIVE end; non-broadcasting state must be visually reviewed')
except Exception as e:
    mark('stage_error',str(e));s['last_error']=str(e)
    raise
finally:
    if a.stage=='preflight':s['spent_seconds']+=time.monotonic()-start
    save(STATE,s)
    print(json.dumps({'phase':s['phase'],'device_seconds':elapsed() if s['active_start'] is not None else s['spent_seconds'],'live_switches':s['live_switches']},indent=2),flush=True)
