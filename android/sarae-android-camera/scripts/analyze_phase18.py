"""Offline gates: target load is separate from unimplemented native interception."""
import hashlib,json,re
from pathlib import Path
import numpy as np
from PIL import Image
P=Path('test-results/phase18');T=Path('.tools/phase18')
def rows(path):return [json.loads(line) for line in path.read_text().splitlines()]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
state=json.loads((P/'device-state.json').read_text());candidate=json.loads((P/'candidate.json').read_text())
assert state['finished'] and state['deployed'] and not state['liveStarted'] and state['spent']<300
native=rows(P/'final-native.jsonl');bridge=rows(P/'final-bridge.jsonl');audio=rows(P/'final-audio.jsonl')
load=next(e for e in native if e['event']=='native_loaded');begin=next(e for e in native if e['event']=='native_load_begin')
snapshots=[e for e in native if e['event']=='native_map_snapshot']
mapped=load['library'].replace('/data/user/0/','/data/data/')
executable=any(mapped in line and 'r-xp' in line for e in snapshots for line in e['observerMaps'])
loaded_hash=(P/'final-loaded-so-hash.txt').read_text().split()[0]
loading=(load['sha256']==loaded_hash==candidate['observerSoSha256'] and executable and load['pid']==int(state['pid'])
         and load['nativeStats'][:6]==[0x53415241454f4253,1,int(state['pid']),load['tid'],int(state['pid']),load['tid']]
         and all(e['nativeStats'][7:]==[0,0,0,0,0] for e in snapshots))
assert not [e for e in native if e['event']=='native_observer_error']
assert any(e['event']=='observation_worker_complete' for e in native)
out=[e for e in bridge if e['kind']=='output_present'];pts=np.array([e['outputPtsNs'] for e in out],dtype=np.int64)
presentation=np.array([e['emittedNs'] for e in out],dtype=np.int64);gaps=np.diff(presentation)/1e6
sessions=[e for e in bridge if e['kind']=='session_redirect'];callbacks=[e for e in bridge if e['kind']=='capture_result_observed']
errors=[e for e in bridge if e['kind'] in ['error','capture_submit_error','request_conversion_error','renderer_error']]
submitted=[e for e in bridge if e['kind']=='capture_submit_result']
assert submitted and all('error=none' in e['detail'] for e in submitted)
a=np.array(Image.open(T/'preview.png').convert('RGB').crop((180,430,900,1600)),dtype=np.float64)
b=np.array(Image.open(T/'preview-later.png').convert('RGB').crop((180,430,900,1600)),dtype=np.float64)
pixel=float(np.mean(np.abs(a-b)))
cleanup=any(e['kind']=='egl_released' for e in bridge) and any(e['kind']=='camera_close_observed' for e in bridge)
camera=(len(sessions)==1 and len(out)>100 and len(callbacks)>1 and pixel>1 and not errors and cleanup)
preservation={}
for phase in ['phase12','phase14','phase15','phase16','phase17']:
    manifest=json.loads(Path(f'test-results/{phase}/evidence-sha256.json').read_text())
    changed=[name for name,sha in manifest.items() if digest(Path(name))!=sha]
    expected=[name for name in changed if phase=='phase14' and name.replace('\\','/')=='app/src/main/java/co/sarae/camera/poc/HookEntry.kt']
    unexpected=[name for name in changed if name not in expected]
    preservation[phase]={'files':len(manifest),'expectedSourceChanges':expected,'unexpectedChanges':unexpected};assert not unexpected
for name in ['base','media','render']:
    extension='json' if name=='render' else 'txt'
    assert (P/f'{name}-before.{extension}').read_bytes()==(P/f'{name}-after.{extension}').read_bytes()
assert digest(T/'rollback-module.apk')==digest(T/'expected-rollback-module.apk')=='53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351'
assert (P/'module-after.txt').read_text().split()[0]==candidate['moduleSha256']
logcat=(T/'final-logcat.txt').read_text(errors='replace')
fatal=[line for line in logcat.splitlines() if re.search(r'FATAL EXCEPTION|Fatal signal|Abort message:',line)]
assert not fatal and not (T/'crash-buffer.txt').read_text().strip()
maps='\n'.join(f.read_text(errors='replace') for f in T.glob('*-maps.txt'))
result={'gates':{'nativeObserverLoadedInTikTok':'PASS' if loading else 'FAIL','targetScopedInterception':'UNVERIFIED',
    'actualRegistrationObserved':'NO — not instrumented; activity UNKNOWN','cameraBridgeRegression':'PASS' if camera else 'FAIL',
    'liveObservationReadiness':'NO-GO','nativePcmReplacementReadiness':'NO-GO'},
    'load':{'pid':load['pid'],'tid':load['tid'],'library':load['library'],'sha256':loaded_hash,'executableMap':executable,
        'moduleClassLoader':begin['moduleClassLoader'],'hostClassLoader':begin['hostClassLoader'],
        'mapSnapshots':len(snapshots),'workerComplete':True,'relaysCreated':0,'targetInterceptionInstalled':False,
        'lyraxMappedInNativeSnapshots':any(e['targetLibraryMaps'] for e in snapshots),'lyraxMappedInHostSnapshots':'liblyrax.so' in maps},
    'camera':{'sessions':len(sessions),'session':sessions,'submissionResults':submitted,'callbackSamples':len(callbacks),
        'lastCallback':callbacks[-1],'outputFrames':len(out),'outputWindowSeconds':float((presentation[-1]-presentation[0])/1e9),
        'outputFps':float((len(out)-1)*1e9/(presentation[-1]-presentation[0])),
        'timestampRegressions':int(np.sum(np.diff(pts)<=0)),'outputGapMs':{'max':float(max(gaps)),'p99':float(np.percentile(gaps,99)),'over100':int(np.sum(gaps>=100))},
        'screenshotRoiMeanAbsoluteDifference':pixel,'previewUpright':'visually confirmed in both screenshots','cleanClose':cleanup,'errors':errors},
    'audio':{'attachment':audio[0],'newNativePcmHook':False,'captureReadEvents':sum(e['kind']=='audio_read' for e in audio),
        'note':'Existing Phase11 engine prepares PCM on video commits, but Phase18 adds no decoder/recorder and does not select VIDEO audio.'},
    'deviceSeconds':state['spent'],'fatalErrorsInBoundedEvidence':fatal,'crashBufferEmpty':True,
    'priorEvidence':preservation,'targetIdentity':json.loads((P/'target-identity.json').read_text()),
    'limitations':['No target interception installed or validated','Registration/callback activity is UNKNOWN, not zero',
        'No LIVE, encoder or receiver test','No fresh audio mode/encoded-output test','Short preview does not establish long-term stability']}
(P/'analysis.json').write_text(json.dumps(result,indent=2))
(P/'runtime-excerpts.json').write_text(json.dumps({'load':load,'maps':snapshots[-1],'camera':sessions+submitted+callbacks[-1:],
    'close':[e for e in bridge if e['kind'] in ['camera_close_observed','egl_released']]},indent=2))
print(json.dumps({k:result[k] for k in ['gates','deviceSeconds','camera','priorEvidence']},indent=2))
