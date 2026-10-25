"""Independent native hook, trampoline, owned callback and preservation gates."""
import hashlib,json,re,zipfile
from pathlib import Path
import numpy as np
P=Path('test-results/phase19');T=Path('.tools/phase19')
state=json.loads((P/'device-state.json').read_text());art=json.loads((P/'artifacts.json').read_text());D=T/state['run']
assert state['suiteDone'] and (D/'DONE').exists() and not (D/'ERROR').exists()
events=[json.loads(line) for line in (D/'events.jsonl').read_text().splitlines()]
def one(name):return next(e for e in events if e['event']==name)
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def distribution(values,scale=1):
    a=np.asarray(values,dtype=np.float64)/scale
    return {'count':len(a),'median':float(np.median(a)),'p99':float(np.percentile(a,99)),'max':float(a.max())}
initial=one('native_hook_initial')['stats'];installed=one('native_hook_installed')['stats'];removed=one('native_hook_removed')['stats'];final=one('native_hook_final')['stats']
native=[e for e in events if e['event']=='native_hook_event']
samples=[e['values'] for e in native];capture=[e['values'] for e in native if e['mode']=='OBSERVED'];reentrant=[e['values'] for e in native if e['mode']=='REENTRANCY']
assert len(samples)==len({e[0] for e in samples})==final[6]==final[7]
assert sorted(e[0] for e in samples)==list(range(len(samples)))
assert len(reentrant)==128 and all(e[8] in range(4) for e in reentrant)
assert final[9]==4 and all(e[1]<=e[2]<=e[3]<=e[4] for e in samples)
mask=(1<<64)-1
for e in samples:
    q,c,seq,depth,result=e[5:10];q&=mask;c&=mask
    rotated=((c<<7)|(c>>57))&mask
    expected=((q^rotated)+(seq+depth)*17+0x19+depth*(depth+1)//2)&mask
    assert result&mask==expected,'original return changed'
created=next(e for e in events if e['event']=='recorder_created' and e['mode']=='OBSERVED')['stats']
assert all(e[5]==created[8] and e[6]==one('observer_registered')['stats'][9] and e[8]==0 for e in capture)
assert [e[7] for e in capture]==list(range(len(capture)))
recorded=next(e for e in events if e['event']=='record_complete' and e['mode']=='OBSERVED')
assert len(capture)==recorded['blocks']==recorded['stats'][0]
baseline=next(e for e in events if e['event']=='record_complete' and e['mode']=='BASELINE')
assert baseline['probe'][:2]==[baseline['blocks'],0]
assert recorded['probe'][0]-baseline['probe'][0]==len(samples) and recorded['probe'][1]==0
assert one('trampoline_reentrant_test')['result'][1]-one('trampoline_reentrant_test')['result'][0]==128
after=one('post_unhook_test');assert after['entriesBefore']==after['entriesAfter']==len(samples) and after['result'][2:]==[0,0]
assert after['result'][1]-after['result'][0]==128
assert final[4]==0 and final[8]==0 and final[10:13]==[0,0,0] and final[18:]==[1,1,1]
assert one('hook_after_stop')['stats'][6:12]==one('hook_after_destroy')['stats'][6:12]
rejected=one('hook_candidate_rejected')['stats'];busy=one('active_unhook_rejected')['stats']
assert rejected[5]==-22 and rejected[18]==0 and busy[5]==-16 and busy[4]==1 and busy[19]==0
audio=json.loads((P/'controlled-audio-analysis.json').read_text())
assert audio['overall'].startswith('PASS')
maps=(T/'own-maps.txt').read_text(errors='replace')
def mapping(address):
    for line in maps.splitlines():
        m=re.match(r'([0-9a-f]+)-([0-9a-f]+)\s+(\S+)',line)
        if m and int(m[1],16)<=address<int(m[2],16):return line
    raise AssertionError(f'No mapping for {address:x}')
hook_map=mapping(initial[16]);unhook_map=mapping(initial[17]);target_map=mapping(installed[15]);trampoline_map=mapping(installed[14])
assert 'r-x' in hook_map and 'r-x' in unhook_map and 'r-x' in target_map and 'r-x' in trampoline_map
loader='assets/lspatch/so/arm64-v8a/liblspatch.so'
with zipfile.ZipFile(art['patchedHarness']) as new,zipfile.ZipFile(T/'rollback-own-harness.apk') as old:
    new_loader=new.read(loader);old_loader=old.read(loader);assert new_loader==old_loader
    loader_sha=hashlib.sha256(new_loader).hexdigest();(T/'pinned-loader-arm64.so').write_bytes(new_loader)
target_apk=Path('.tools/phase10/ring-fixed/base-487-lspatched.apk')
assert sha(target_apk)==state['targetBaseBefore']==state['targetBaseAfter']
with zipfile.ZipFile(target_apk) as z:assert z.read(loader)==new_loader
module=[json.loads(line) for line in (P/'module-events.jsonl').read_text().splitlines()]
component=next(e for e in module if e['event']=='native_hook_component_loaded' and str(e['pid'])==state['ownPid'])
assert component['sha256']==art['hookSoSha256'] and component['library'].replace('/data/user/0/','/data/data/') in maps
assert state['targetPreserved'] and state['targetPidBefore']==state['targetPidAfter']=='8226'
fatal=[line for line in (T/'own-logcat.txt').read_text(errors='replace').splitlines() if re.search(r'FATAL EXCEPTION|Fatal signal|Abort message:',line)]
assert not fatal and not (T/'crash-buffer.txt').read_text().strip()
preservation={}
for phase in ['phase12','phase14','phase15','phase16','phase17','phase18']:
    manifest=json.loads(Path(f'test-results/{phase}/evidence-sha256.json').read_text())
    # Some historical manifests also contain source entries. Preserve all original evidence;
    # source modifications below are explicitly recorded, not mistaken for altered logs.
    evidence={name:digest for name,digest in manifest.items() if name.replace('\\','/').startswith(('test-results/','.tools/'))}
    changed=[name for name,digest in evidence.items() if sha(Path(name))!=digest]
    assert not changed;preservation[phase]={'evidenceFiles':len(evidence),'changed':changed}
raw=json.loads(Path('test-results/phase18/raw-evidence-sha256.json').read_text())
assert all(sha(Path(name))==digest for name,digest in raw.items())
result={'gates':{'nativeApiAvailability':'PASS','ownedFunctionInterception':'PASS','trampolineReturnArgumentsReentrancy':'PASS',
    'callbackLifecycleAndAudio':'PASS','safeFailureGuards':'PASS for sentinel rejection and active-unhook guard; underlying hook-engine failure UNVERIFIED',
    'targetObserverReadiness':'NO-GO for liblyrax interception; GO for a separately reviewed target-scoped implementation plan'},
    'api':{'version':initial[0],'nativeInitCount':initial[1],'ownedLibraryLoadNotifications':initial[3],
        'hookPointerMap':hook_map,'unhookPointerMap':unhook_map,'targetMap':target_map,'trampolineMap':trampoline_map,
        'loaderArm64Sha256':loader_sha,'installedOwnedHarnessLoaderExactlyMatchesPinnedCandidate':True,
        'installedTikTokBaseLoaderBinaryExactlyMatches':True,'targetApiInitializedInTikTok':False,'hookComponent':component},
    'hook':{'entries':len(samples),'originalCallsThroughTrampoline':final[7],'controlReentrantEntries':len(reentrant),
        'callbackProbeEntries':len(capture),'originalInvocationCountDelta':recorded['probe'][0]-baseline['probe'][0],
        'maxNestedInFlight':final[9],'drops':final[10],'errors':final[11],'callbackSequenceContiguous':True,
        'argumentsAndReturnsUnchanged':True,'hookCalls':final[18],'unhookCalls':final[19],'first16OriginalBytesRestored':bool(final[20]),
        'postStopDestroyCountersStable':True,'postUnhookOriginalCalls':128,'postUnhookReplacementCalls':0,
        'callbackThreadIds':sorted({e[10] for e in capture}),
        'instrumentedWrapperAdditionalUs':distribution([(e[4]-e[1])-(e[3]-e[2]) for e in capture],1000),
        'originalTrampolineInclusiveUs':distribution([e[3]-e[2] for e in capture],1000),
        'callbackProbeEntryIntervalMs':distribution(np.diff([e[1] for e in capture]),1e6),
        'timingLimit':'Wrapper measurement excludes entry counters, final telemetry publication/counter decrement and entry/return branches. Original duration includes trampoline. Not a total overhead or realtime bound.'},
    'callback':audio['observation'],'lifecycle':audio['lifecycle'],
    'encodedOutputs':audio['cases'],'phase18Gap':json.loads((P/'phase18-gap-analysis.json').read_text()),
    'device':state,'priorEvidence':preservation,'fatalErrors':fatal,
    'limitations':['No target-native hook or LIVE tested','Owned prologue is not liblyrax prologue relocation proof',
        'No forced underlying Dobby hook-install failure; guard paths only','Framework load-listener cannot unregister through this public API and remains until owned process exit',
        'No dlclose while function pointers/callbacks remain registered','No concurrent control-thread install/unhook stress test; control operations serialized']}
(P/'analysis.json').write_text(json.dumps(result,indent=2))
(P/'native-hook-events.jsonl').write_text('\n'.join(json.dumps(e) for e in native)+'\n')
(P/'runtime-excerpts.json').write_text(json.dumps([e for e in events if e['event'] in ['native_hook_initial','hook_candidate_rejected','native_hook_installed','trampoline_reentrant_test','active_unhook_rejected','hook_after_stop','hook_after_destroy','native_hook_removed','post_unhook_test','native_hook_final']],indent=2))
print(json.dumps({'gates':result['gates'],'hook':result['hook'],'api':{k:v for k,v in result['api'].items() if k!='hookComponent'},'deviceSeconds':state['deviceSeconds']},indent=2))
