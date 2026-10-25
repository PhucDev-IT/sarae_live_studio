"""User-authorized extra <=30 s: restore MIC and collect saved clips, no new test."""
import datetime,json,re,subprocess,time
from pathlib import Path
root=Path('test-results/phase10');start=time.monotonic();rows=[]
previous=json.loads((root/'authorized-extra-collection.json').read_text()) if (root/'authorized-extra-collection.json').exists() else {'seconds':0,'commands':[]}
def adb(args,name=None,timeout=5):
    remaining=29-previous['seconds']-(time.monotonic()-start)
    if remaining<1:raise TimeoutError('Extra collection budget exhausted')
    begin=time.monotonic()
    r=subprocess.run(['D:/window/Android/Sdk/platform-tools/adb.exe',*args],capture_output=True,timeout=min(timeout,remaining),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    rows.append({'args':args,'seconds':time.monotonic()-begin,'exit_code':r.returncode})
    if name:(root/name).write_bytes(r.stdout)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace'))
    return r.stdout.decode(errors='replace')
try:
    adb(['shell','am','broadcast','-a','co.sarae.camera.AUDIO_MODE','-p','com.ss.android.ugc.trill','--es','mode','MICROPHONE'],'restore-microphone.txt',4)
    videos=adb(['shell','content','query','--uri','content://media/external/video/media','--projection','_id:_data:date_added:duration','--where','"_id>1000074320"','--sort','"date_added DESC"'],'new-videos.txt',4)
    paths=[p for p in re.findall(r'_data=([^,\r\n]+)',videos) if not p.endswith('2026-10-24-093237572.mp4')][:2]
    for i,path in enumerate(paths):
        adb(['pull',path,str(Path('.tools/phase10')/Path(path).name)],f'pull-saved-{i+1}.txt',6)
    pid=adb(['shell','pidof','com.ss.android.ugc.trill'],'collected-pid.txt',3).strip();assert re.fullmatch(r'\d+',pid)
    base='/sdcard/Android/data/com.ss.android.ugc.trill/files'
    adb(['pull',f'{base}/phase10/audio-{pid}.jsonl',str(root/'final-audio.jsonl')],'final-audio-pull.txt',4)
    adb(['pull',f'{base}/phase6/bridge-{pid}.jsonl',str(root/'final-bridge.jsonl')],'final-bridge-pull.txt',4)
    if time.monotonic()-start<25:
        adb(['logcat','-b','crash','-d','-t','100','-v','threadtime'],'crash-buffer.txt',3)
finally:
    result={'authorization':'User explicitly allowed extra <=30 seconds for saved-file collection and MICROPHONE restore only',
            'seconds':previous['seconds']+time.monotonic()-start,'commands':previous['commands']+rows,'new_tests':0,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (root/'authorized-extra-collection.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
