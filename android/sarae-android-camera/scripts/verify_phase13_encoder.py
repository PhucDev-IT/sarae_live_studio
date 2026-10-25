"""Two local encoder checks; no TikTok actions and no network transmission."""
import json
import subprocess
import time
from pathlib import Path
P=Path('test-results/phase13');TMP=Path('.tools/phase13');PKG='co.sarae.camera.consumer'
FILES=f'/sdcard/Android/data/{PKG}/files';ADB='D:/window/Android/Sdk/platform-tools/adb.exe'
assert not (P/'encoder-finished.json').exists()
prior=json.loads((P/'device-timing.json').read_text())['conservative_total_seconds']
assert prior<260,'Not enough reserved verification budget'
start=time.monotonic()
def adb(args,dest=None,timeout=5):
    remaining=285-prior-(time.monotonic()-start);assert remaining>1,'Budget reached; no retries'
    assert 'com.ss.android.ugc.trill' not in args
    r=subprocess.run([ADB,*args],capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
    if dest:Path(dest).write_bytes(r.stdout)
    assert r.returncode==0,r.stderr.decode(errors='replace')
    return r.stdout.decode(errors='replace')
try:
    top=adb(['shell','dumpsys','activity','activities'])
    assert any('com.sec.android.app.launcher' in l for l in top.splitlines() if 'topResumedActivity=' in l)
    adb(['install','-r',str(TMP/'consumer-test-debug-487-lspatched.apk')],P/'encoder-install.txt',10)
    adb(['push',str(TMP/'encoder-source.mp4'),FILES+'/A.mp4'],P/'encoder-push.txt',5)
    for name,disabled in [('encoder-baseline',True),('encoder-corrected',False)]:
        case=P/name;case.mkdir()
        adb(['shell','rm','-f',FILES+'/phase13-disable-submit-remap'])
        adb(['shell','touch',FILES+'/phase13-disable-owner-guard'] if disabled else ['shell','rm','-f',FILES+'/phase13-disable-owner-guard'])
        adb(['shell','am','force-stop',PKG])
        adb(['shell','am','start','-W','-n',PKG+'/.ConsumerActivity','--es','testSessionMethod','modern',
             '--ez','testPrebuiltRequest','true','--ez','testCaptureCallbacks','true','--ez','testRetiredClose','true',
             '--ez','testEncodeCapture','true'],case/'start.txt',6)
        time.sleep(10.5)
        top=adb(['shell','dumpsys','activity','activities'])
        assert any('com.sec.android.app.launcher' in l for l in top.splitlines() if 'topResumedActivity=' in l),'Probe did not finish; do not issue UI actions'
        pid=adb(['shell','pidof',PKG]).strip()
        for kind in ['consumer','bridge']:
            adb(['pull',f'{FILES}/phase3/{kind}-{pid}.jsonl',str(case/(kind+'.jsonl'))],case/(kind+'-pull.txt'))
        adb(['pull',FILES+'/phase13-encoded.mp4',str(TMP/(name+'.mp4'))],case/'mp4-pull.txt')
    adb(['shell','rm','-f',FILES+'/phase13-disable-owner-guard'])
    adb(['shell','dumpsys','media.camera'],P/'encoder-cleanup-camera.txt')
    (P/'encoder-finished.json').write_text(json.dumps({'live_sessions':0,'tiktok_actions':0,'duration_seconds':time.monotonic()-start},indent=2))
finally:
    elapsed=time.monotonic()-start
    with (P/'device-budget.jsonl').open('a') as f:f.write(json.dumps({'stage':'local-encoder-harness','seconds':elapsed,'includes_waits_install_collection':True})+'\n')
print(json.dumps({'prior_seconds':prior,'encoder_seconds':elapsed,'conservative_total_seconds':prior+elapsed},indent=2))
