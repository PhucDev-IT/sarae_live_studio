"""Read-only metadata snapshots. No launch, attach, signal, hook or PCM capture.

Default is a single non-LIVE inventory. A future active-session snapshot needs
explicit human authorization; this tool cannot discover callback ABI/ownership.
"""
import argparse,datetime,hashlib,json,re,subprocess,time
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--label',default='nonlive-inventory')
parser.add_argument('--snapshot-existing-process',action='store_true')
args=parser.parse_args()
assert re.fullmatch(r'[a-z0-9-]{1,40}',args.label)
P=Path('test-results/phase16');T=Path('.tools/phase16')/args.label
P.mkdir(exist_ok=True);T.mkdir(parents=True,exist_ok=False)
ADB='D:/window/Android/Sdk/platform-tools/adb.exe';PKG='com.ss.android.ugc.trill'
MODULE='cache/lspatch/co.sarae.camera.poc/183496786.apk'
EXPECTED='53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351'
start=time.monotonic()
result={'utcStart':datetime.datetime.now(datetime.timezone.utc).isoformat(),'label':args.label,
        'scope':'read-only; does not detect or initiate LIVE; no native instrumentation',
        'targetMutations':[],'runtimePcmEntry':'UNKNOWN','nativeCallbackObservation':'NOT_INSTRUMENTED'}

def adb(command,name,timeout=5,optional=False):
    assert time.monotonic()-start<45,'Bounded snapshot deadline reached'
    before=time.monotonic()
    row={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'args':command,'timeoutSeconds':timeout}
    try:
        process=subprocess.run([ADB,*command],capture_output=True,timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
        row['exitCode']=process.returncode
        (T/name).write_bytes(process.stdout)
        if process.returncode and not optional:raise RuntimeError(process.stderr.decode(errors='replace'))
        return process.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        row['timedOut']=True;raise
    finally:
        row['seconds']=time.monotonic()-before
        with (P/'adb.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')

try:
    result['model']=adb(['shell','getprop','ro.product.model'],'model.txt').strip()
    result['api']=adb(['shell','getprop','ro.build.version.sdk'],'api.txt').strip()
    result['moduleSha256']=adb(['shell','run-as',PKG,'sha256sum',MODULE],'module-hash.txt').split()[0]
    assert result['moduleSha256']==EXPECTED,'Unexpected module; do not update/reload it'
    package_paths=adb(['shell','pm','path',PKG],'package-paths.txt')
    result['installedApkCount']=len(package_paths.splitlines())
    base=next(s.removeprefix('package:') for s in package_paths.splitlines() if s.endswith('/base.apk'))
    result['baseSha256']=adb(['shell','sha256sum',base],'base-hash.txt').split()[0]
    assert result['baseSha256']=='4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b'
    pid=adb(['shell','pidof',PKG],'pid.txt',optional=True).strip()
    result['pid']=pid or None
    if args.snapshot_existing_process and re.fullmatch(r'\d+',pid):
        # No debuggerd/ptrace/stack suspension: metadata only, existing process.
        adb(['exec-out','run-as',PKG,'cat',f'/proc/{pid}/maps'],'maps.txt',optional=True)
        adb(['shell','dumpsys','media.audio_flinger'],'audioflinger.txt',6)
        adb(['logcat','-d','-t','400','-v','threadtime','--pid='+pid],'pid-logcat.txt',5)
        audio=f'/sdcard/Android/data/{PKG}/files/phase11/audio-{pid}.jsonl'
        adb(['shell','tail','-n','100',audio],'audio-events-tail.jsonl',optional=True)
        result['note']='System tracks and mapped libraries do not prove native capture entry. No PCM contract inferred.'
    elif not pid:
        result['note']='No target process running. Did not launch TikTok. Native runtime observation unavailable.'
    result['status']='PASS: preserved package/module inventory only'
finally:
    result['deviceSeconds']=time.monotonic()-start
    result['utcEnd']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (P/(args.label+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
