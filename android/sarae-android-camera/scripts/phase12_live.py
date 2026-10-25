"""One human-controlled LIVE. Read-only diagnostics plus the existing VIDEO/A/B controls.

No start/end broadcast, process restart, package install, module write or UI tap exists here.
Budget counts active host verification stages; idle waiting for human/viewer replies is excluded.
"""
import argparse,datetime,hashlib,json,re,subprocess,time
from pathlib import Path
P=Path('test-results/phase12');T=Path('.tools/phase12');S=P/'state.json'
P.mkdir(exist_ok=True);(T/'private').mkdir(parents=True,exist_ok=True)
ADB='D:/window/Android/Sdk/platform-tools/adb.exe';PKG='com.ss.android.ugc.trill';FILES=f'/sdcard/Android/data/{PKG}/files'
EXPECTED='2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0'
parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['preflight','preview','mark-live','observe','to-b','to-a','finish'])
parser.add_argument('--label',default='observation');parser.add_argument('--human',default='');parser.add_argument('--receiver',default='')
a=parser.parse_args();assert re.fullmatch('[a-z0-9-]{1,40}',a.label)
if S.exists():s=json.loads(S.read_text())
else:s={'spent_seconds':0,'session_count':0,'live_active':False,'switches':0,'finished':False,'viewer_ready':True}
assert not s['finished'],'One execution already finished; never request another LIVE'
start=time.monotonic()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def elapsed():return s['spent_seconds']+time.monotonic()-start
def save():S.write_text(json.dumps(s,indent=2),encoding='utf-8')
def event(kind,details):
    with (P/'protocol.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'utc':utc(),'kind':kind,'detail':details,'active_device_seconds':elapsed()},ensure_ascii=False)+'\n')
def adb(args,name=None,timeout=5,optional=False,private=False):
    # Reserve >=35 s for final collection. No automatic action ends a LIVE at this limit.
    remaining=(295 if a.stage=='finish' else 260)-elapsed()
    if remaining<1:raise TimeoutError('Verification budget reached; do not interrupt or restart broadcast')
    assert not any(x in args for x in ['force-stop','install','uninstall','install-create','install-commit','clear','keyevent','tap','swipe'])
    begin=time.monotonic();row={'utc':utc(),'args':args,'timeout':min(timeout,remaining)}
    try:
        r=subprocess.run([ADB,*args],capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
        row['exit_code']=r.returncode
        if name:((T/'private' if private else P)/name).write_bytes(r.stdout)
        if r.returncode and not optional:raise RuntimeError(r.stderr.decode(errors='replace'))
        return r.stdout.decode(errors='replace')
    finally:
        row['seconds']=time.monotonic()-begin
        with (P/'adb.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
def rows(file):
    result=[]
    if file.exists():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            try:result.append(json.loads(line))
            except json.JSONDecodeError:pass
    return result
def collect(label):
    pid=adb(['shell','pidof',PKG],label+'-pid.txt',3).strip();assert re.fullmatch(r'\d+',pid),'Main PID missing; no restart'
    adb(['pull',f'{FILES}/phase11/audio-{pid}.jsonl',str(P/(label+'-audio.jsonl'))],label+'-audio-pull.txt',5)
    adb(['pull',f'{FILES}/phase6/bridge-{pid}.jsonl',str(P/(label+'-bridge.jsonl'))],label+'-bridge-pull.txt',6)
    return pid
def shot(label):
    adb(['shell','screencap','-p','/sdcard/sarae-phase12.png'],timeout=4)
    adb(['pull','/sdcard/sarae-phase12.png',str(P/(label+'.png'))],timeout=4)
def mode_video():adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_MODE','-p',PKG,'--es','mode','VIDEO'],'mode-video.txt',4)
def runtime(label):
    adb(['shell','dumpsys','media.camera'],label+'-camera.txt',6,True,True)
    adb(['shell','dumpsys','media.audio_flinger'],label+'-audioflinger.txt',6,True,True)
    adb(['logcat','-d','-t','2500','-v','threadtime','--pid='+s['pid']],label+'-pid-logcat.txt',5,True,True)
try:
    if a.stage=='preflight':
        assert s['session_count']==0 and not s.get('preflight_pass')
        assert adb(['get-state']).strip()=='device'
        assert adb(['shell','getprop','ro.product.model'],'model.txt').strip()=='SM-G996U'
        package=adb(['shell','dumpsys','package',PKG],'package-before.txt',6,private=True)
        assert 'versionName=47.4.3' in package and 'versionCode=470403' in package
        paths=adb(['shell','pm','path',PKG],'paths-before.txt').splitlines();assert len(paths)==61
        base=next(x.removeprefix('package:') for x in paths if x.endswith('/base.apk'));s['base']=base
        assert adb(['shell','sha256sum',base],'base-hash.txt').split()[0]=='4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b'
        cache='cache/lspatch/co.sarae.camera.poc/183496786.apk'
        actual=adb(['shell','run-as',PKG,'sha256sum',cache],'module-hash.txt').split()[0]
        assert actual==EXPECTED,'Module cache is not Phase 11; restore BEFORE any LIVE, never during'
        assert hashlib.sha256(Path('.tools/phase11/module.apk').read_bytes()).hexdigest()==EXPECTED
        source=adb(['shell','sha256sum',f'{FILES}/A.mp4',f'{FILES}/B.mp4'],'media-hashes.txt').splitlines()
        assert [x.split()[0] for x in source]==['c152467cdce40156894d1b49722faeaae6b11e29b76f6c64578caf7254543afa','f9b0356d7cb51ece315c3953060f89ad075ab3d7242ca2ae95a6e355005eb7f4']
        config=json.loads(adb(['shell','cat',f'{FILES}/sarae-render.json'],'render-config.json'))
        assert config['autoCameraOrientation'] and config.get('rotationOverride') is None
        s['pid']=collect('preflight');audio=rows(P/'preflight-audio.jsonl')
        assert any(e.get('pipelineVersion')=='phase11' for e in audio),'No Phase 11 attachment for current PID'
        s['preflight_audio_ns']=max(e['ns'] for e in audio)
        mode_video();time.sleep(.7);collect('mode-ack')
        assert any(e['kind']=='audio_mode' and e.get('mode')=='VIDEO' and e['ns']>s['preflight_audio_ns'] for e in rows(P/'mode-ack-audio.jsonl')),'No fresh Phase 11 VIDEO ack'
        shot('preflight');s['preflight_pass']=True
        event('preflight_pass',{'pid':s['pid'],'module_hash':actual,'base_unchanged':True,'viewer_ready':True})
    elif a.stage=='preview':
        assert s.get('preflight_pass') and s['session_count']==0
        if (P/'source-media.json').exists():
            media=json.loads((P/'source-media.json').read_text())
            actual=adb(['shell','sha256sum',f'{FILES}/A.mp4',f'{FILES}/B.mp4'],'active-media-hashes.txt').splitlines()
            assert [x.split()[0] for x in actual]==[media[k]['sha256'] for k in ['A','B']]
            s['source_revision']={k:media[k]['sha256'] for k in ['A','B']}
        mode_video();time.sleep(.6)
        pid=collect('preview');assert pid==s['pid'],'PID changed before LIVE; recheck code, do not start'
        shot('preview');audio=rows(P/'preview-audio.jsonl');video=rows(P/'preview-bridge.jsonl')
        assert any(e['kind']=='output_present' for e in video),'No renderer output'
        assert any(e['kind']=='audio_pcm_ready' for e in audio),'No PCM readiness'
        latest=next(e for e in reversed(video) if e['kind']=='output_present')
        assert latest.get('source','').endswith('/A.mp4'),'Initial source is not A'
        s['preview_audio_ns']=max(e['ns'] for e in audio);s['preview_video_ns']=max(e['ns'] for e in video)
        s['preview_ns']=latest['ns'];s['preview_pass']=True
        event('preview_ready',{'source':'A','video_ns':latest['ns'],'StartLIVEPressed':False})
    elif a.stage=='mark-live':
        assert s.get('preview_pass') and s['session_count']==0 and a.human
        s['session_count']=1;s['live_active']=True;s['human_start']=a.human;s['human_start_utc']=utc();save()
        event('human_live_started',a.human)
        s['receiver_A']=a.receiver
        pid=collect('live-A');shot('live-A');s['live_pid']=pid
        runtime('live-A')
    elif a.stage=='observe':
        assert s['session_count']==1
        collect(a.label);shot(a.label)
        if a.receiver:s['receiver_'+a.label]=a.receiver;event('receiver_observation',a.receiver)
    elif a.stage in ['to-b','to-a']:
        assert s['session_count']==1 and s['live_active']
        expected=0 if a.stage=='to-b' else 1;assert s['switches']==expected
        assert a.receiver,'Record prior receiver observation before moving to the next source'
        s['receiver_A' if expected==0 else 'receiver_B']=a.receiver
        event('receiver_observation',a.receiver)
        source='B' if expected==0 else 'A'
        event('switch_request',source)
        adb(['shell','am','broadcast','-a','co.sarae.camera.TEST_SWITCH','-p',PKG,'--es','source',source,'--es','mode','now'],f'live-switch-{expected+1}-{source}.txt',4)
        s['switches']+=1;save();time.sleep(8)
        pid=collect('live-'+('B' if expected==0 else 'A-return'));shot('live-'+('B' if expected==0 else 'A-return'))
        event('switch_snapshot',{'source':source,'pid':pid,'pid_changed':pid!=s['live_pid']})
    elif a.stage=='finish':
        assert s['session_count']==1 and s['live_active'] and a.human,'Human-ended confirmation required; never end LIVE automatically'
        s['human_end']=a.human;s['human_end_utc']=utc();s['live_active']=False
        if a.receiver:s['receiver_final']=a.receiver
        event('human_live_ended',a.human)
        collect('final');shot('after-human-end');runtime('final')
        adb(['logcat','-b','crash','-d','-t','150','-v','threadtime'],'crash-buffer.txt',4,True)
        # Only after human end: restore default microphone pass-through.
        adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_MODE','-p',PKG,'--es','mode','MICROPHONE'],'restore-mic.txt',4,True)
        time.sleep(.6);collect('mode-restored')
        s['finished']=True
except Exception as e:
    event('stage_error',str(e));s['last_error']=str(e);raise
finally:
    duration=time.monotonic()-start;s['spent_seconds']+=duration
    with (P/'budget.jsonl').open('a') as f:f.write(json.dumps({'stage':a.stage,'seconds':duration,'idle_human_wait_excluded':True})+'\n')
    save();print(json.dumps({'stage':a.stage,'active_device_seconds':s['spent_seconds'],'session_count':s['session_count'],'live_active':s['live_active'],'switches':s['switches']},indent=2),flush=True)
