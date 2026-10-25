"""Bounded non-LIVE mechanism checks in Sarae's existing independent consumer only."""
import json
import re
import subprocess
import time
from pathlib import Path

OUT=Path('test-results/phase13');TMP=Path('.tools/phase13');TMP.mkdir(exist_ok=True)
ADB='D:/window/Android/Sdk/platform-tools/adb.exe';PKG='co.sarae.camera.consumer'
FILES=f'/sdcard/Android/data/{PKG}/files'
assert not (OUT/'harness-finished.json').exists(), 'Do not automatically repeat completed tests'
prior=sum(json.loads(x)['seconds'] for x in (OUT/'device-budget.jsonl').read_text().splitlines())
start=time.monotonic()

def adb(args, dest=None, timeout=6):
    remaining=285-prior-(time.monotonic()-start)
    assert remaining>1, 'Reserve cleanup time; budget exhausted'
    assert not ('com.ss.android.ugc.trill' in args and any(x in args for x in ['force-stop','install','uninstall','clear','start','broadcast']))
    before=time.monotonic()
    try:
        r=subprocess.run([ADB,*args],capture_output=True,timeout=min(timeout,remaining),creationflags=subprocess.CREATE_NO_WINDOW)
        if dest:Path(dest).write_bytes(r.stdout)
        assert r.returncode==0,r.stderr.decode(errors='replace')
        return r.stdout.decode(errors='replace')
    finally:
        with (OUT/'adb-harness.jsonl').open('a') as f:f.write(json.dumps({'args':args,'timeout_s':min(timeout,remaining),'seconds':time.monotonic()-before})+'\n')

def home_guard():
    activity=adb(['shell','dumpsys','activity','activities'])
    top='\n'.join(l for l in activity.splitlines() if 'topResumedActivity=' in l)
    assert 'com.sec.android.app.launcher' in top,'User activity ongoing; do not take foreground'
    camera=adb(['shell','dumpsys','media.camera'])
    clients=camera.split('Active Camera Clients:')[1].split('Allowed user IDs:')[0].strip()
    assert clients=='[]','Active camera client; stop independent tests'

case_results=[]
try:
    home_guard()
    adb(['shell','run-as','com.ss.android.ugc.trill','sha256sum','cache/lspatch/co.sarae.camera.poc/183496786.apk'],OUT/'tiktok-module-before.txt')
    adb(['install','-r',str(TMP/'consumer-test-debug-487-lspatched.apk')],OUT/'install-consumer.txt',30)
    adb(['shell','mkdir','-p',FILES])
    adb(['push','test-fixtures/A.mp4',FILES+'/A.mp4'],OUT/'push-own-fixture.txt',8)
    adb(['shell','pm','grant',PKG,'android.permission.CAMERA'])
    cases=[('prebuilt-baseline',True,False,True,False),('prebuilt-corrected',False,False,True,False),
           ('retired-close-baseline',False,True,False,True),('retired-close-corrected',False,False,False,True)]
    for name,disable_mapping,disable_owner,prebuilt,retired in cases:
        home_guard();case=OUT/name;case.mkdir()
        for marker,disabled in [('phase13-disable-submit-remap',disable_mapping),('phase13-disable-owner-guard',disable_owner)]:
            adb(['shell','touch' if disabled else 'rm','-f',FILES+'/'+marker] if not disabled else ['shell','touch',FILES+'/'+marker])
        adb(['shell','am','force-stop',PKG])
        adb(['shell','am','start','-W','-n',PKG+'/.ConsumerActivity','--es','testSessionMethod','modern',
             '--ez','testPrebuiltRequest',str(prebuilt).lower(),'--ez','testCaptureCallbacks','true',
             '--ez','testRetiredClose',str(retired).lower()],case/'start.txt',10)
        time.sleep(7 if retired else 5)
        pid=adb(['shell','pidof',PKG]).strip();assert re.fullmatch(r'\d+',pid)
        adb(['shell','dumpsys','media.camera'],case/'camera.txt',8)
        adb(['logcat','-d','--pid='+pid,'-t','800','-v','threadtime'],TMP/(name+'-logcat.txt'),5)
        # Only leave the activity we started. Never navigate away from new user activity.
        top=adb(['shell','dumpsys','activity','activities'])
        assert any(PKG in l for l in top.splitlines() if 'topResumedActivity=' in l),'Foreground changed; do not send HOME'
        adb(['shell','input','keyevent','KEYCODE_HOME']);time.sleep(.8)
        for kind in ['consumer','bridge']:
            adb(['pull',f'{FILES}/phase3/{kind}-{pid}.jsonl',str(case/(kind+'.jsonl'))],case/(kind+'-pull.txt'),6)
        case_results.append({'name':name,'pid':pid})
    adb(['shell','rm','-f',FILES+'/phase13-disable-submit-remap',FILES+'/phase13-disable-owner-guard'])
    adb(['shell','run-as','com.ss.android.ugc.trill','sha256sum','cache/lspatch/co.sarae.camera.poc/183496786.apk'],OUT/'tiktok-module-after.txt')
    adb(['shell','dumpsys','media.camera'],OUT/'camera-after.txt',8)
    (OUT/'harness-finished.json').write_text(json.dumps({'cases':case_results,'new_live_sessions':0,'tiktok_modified':False},indent=2))
finally:
    with (OUT/'device-budget.jsonl').open('a') as f:f.write(json.dumps({'stage':'independent-consumer-harness','seconds':time.monotonic()-start,'includes_waits_install_collection':True})+'\n')
print(json.dumps({'cases':case_results,'stage_seconds':time.monotonic()-start,'prior_device_seconds':prior},indent=2))
