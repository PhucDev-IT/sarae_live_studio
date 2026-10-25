"""Offline assessment; owned success never promotes target/LIVE readiness."""
import hashlib,json,re,struct,subprocess,zipfile
from pathlib import Path
import numpy as np
P=Path('test-results/phase20');T=Path('.tools/phase20')
state=json.loads((P/'device-state.json').read_text());D=T/state['run'];assert state['suiteDone'] and (D/'DONE').exists() and not (D/'ERROR').exists()
events=[json.loads(line) for line in (D/'events.jsonl').read_text().splitlines()]
def one(name):return next(e for e in events if e['event']==name)
def sha(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def distribution(values,scale=1):
    a=np.asarray(values,dtype=np.float64)/scale
    return {'count':len(a),'median':float(np.median(a)),'p99':float(np.percentile(a,99)),'max':float(a.max())}
def mic_encoded_match(reference,recorded):
    a=reference.reshape(-1,2).mean(axis=1);b=recorded.reshape(-1,2).mean(axis=1);n=1<<int(len(a)+len(b)-1).bit_length()
    x=np.fft.irfft(np.conj(np.fft.rfft(a,n))*np.fft.rfft(b,n),n);lags=np.arange(-4096,4097);lag=int(lags[np.argmax(x[lags%n])])
    a=a[max(0,-lag):];b=b[max(0,lag):];length=min(len(a),len(b));a=a[:length]-a[:length].mean();b=b[:length]-b[:length].mean()
    return {'lagFrames':lag,'lagMs':lag/48,'correlation':float(np.dot(a,b)/max(1e-20,np.linalg.norm(a)*np.linalg.norm(b)))}
valid=one('binding_valid')['stats'];final=one('binding_cleanup')['stats'];direct=one('direct_module_pin')
assert direct['stats'][6]==-43 and 'RTLD_NOLOAD' in direct['error']
assert valid[6:8]==[0,1] and valid[10]==valid[8]+valid[9] and valid[31:33]==[1,1]
for event,code in [('target_missing',-40),('reject_hash',-31),('reject_build',-32),('reject_fingerprint',-33)]:
    s=one(event)['stats'];assert s[6]==code and s[11]==0 and s[30]==0
assert one('ambiguous_policy_unit')['synthetic'] and one('ambiguous_policy_unit')['stats'][6]==-34
null=one('engine_null_rejection')['stats'];duplicate=one('engine_duplicate_rejection')['stats']
assert null[18:20]==[-1,1] and duplicate[20:22]==[-1,1] and duplicate[11]==1
callbacks=[e['values'] for e in events if e['event']=='binding_callback'];capture=[e['values'] for e in events if e['event']=='binding_callback' and e['mode']=='OBSERVED']
assert len(callbacks)==final[12]==final[13] and sorted(e[0] for e in callbacks)==list(range(len(callbacks)))
assert len(callbacks)-len(capture)==128 and final[14:18]==[0,4,0,0]
created=one('recorder_created')['stats'];registered=one('observer_registered')['stats'];buffers=[e for e in events if e['event']=='buffer_complete']
assert len(capture)==len(buffers)==one('record_complete')['stats'][0]
assert all(e[5]==created[8] and e[6]==registered[9] for e in capture)
assert all(e[5:7]==[0x1234,0x5678] for e in callbacks if e[0]<128)
assert [e['sequence'] for e in buffers]==list(range(len(buffers)))
assert all(e['index']==e['sequence']%2 and e['address']==created[5+e['index']] and e['enqueueResult']==0 for e in buffers)
cooperative=[e for e in events if e['event']=='observer_callback'];assert len(cooperative)==len(capture)
assert one('record_complete')['stats'][1:5]==[0,0,0,0]
assert one('binding_after_stop')['stats'][12:18]==one('binding_after_destroy')['stats'][12:18]
for name in ['observer_after_stop','observer_after_destroy']:
    e=one(name);assert e['before'][0]==e['after'][0] and e['after'][5:7]==[0,0]
for name in ['unhooked_pattern_test','nested_pattern_test','pattern_after_unhook']:
    values=one(name)['values'];assert values[1]-values[0]==128 and values[5]==0
assert final[11]==0 and final[22]==1 and final[23:25]==[2,2] and final[28]==32 and final[30]==0 and final[32:34]==[0,1]
raw=(D/'code.bin').read_bytes();assert len(raw)==384
before,patch,trampoline=raw[:32],raw[32:64],raw[64:192]
assert before[:12].hex()=='f40f1ef8f37b01a933100191' and trampoline[:12]==before[:12]
assert patch[12:]==before[12:] and struct.unpack_from('<Q',trampoline,20)[0]==valid[10]+12
assert one('pattern_after_unhook')['stats'][12]==final[12]
mic=np.fromfile(D/'OBSERVED-mic.s16',dtype='<i2').astype(np.float64);delivered=np.fromfile(D/'OBSERVED-delivered.s16',dtype='<i2').astype(np.float64)
assert np.array_equal(mic,delivered)
r=subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-v','error','-i',str(D/'OBSERVED.mp4'),'-f','s16le','-acodec','pcm_s16le','-ar','48000','-ac','2','-'],capture_output=True,timeout=15,check=True)
encoded=np.frombuffer(r.stdout,dtype='<i2').astype(np.float64);match=mic_encoded_match(mic,encoded);assert match['correlation']>.95
logcat=(T/'own-logcat.txt').read_text(errors='replace');fatal=[line for line in logcat.splitlines() if re.search(r'FATAL EXCEPTION|Fatal signal|Abort message:',line)]
assert not fatal and not (T/'crash-buffer.txt').read_text().strip()
assert state['targetPreserved'] and state['targetPidBefore']==state['targetPidAfter']=='8226' and state['deviceSeconds']<300
assert sha(T/'tiktok-phase18-rollback.apk')=='66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120'
preservation={}
for phase in ['phase12','phase14','phase15','phase16','phase17','phase18','phase19']:
    manifest=json.loads(Path(f'test-results/{phase}/evidence-sha256.json').read_text())
    evidence={name:digest for name,digest in manifest.items() if name.replace('\\','/').startswith(('test-results/','.tools/'))}
    changed=[name for name,digest in evidence.items() if sha(Path(name))!=digest];assert not changed
    preservation[phase]={'evidenceFiles':len(evidence),'changed':changed}
result={'gates':{'targetAbi':'STATICALLY_SUPPORTED two-pointer void callback; runtime NOT TESTED',
    'ownedExactFirst12AndContinuation':'PASS','ownedPcRelativeBranch':'PASS','BLRelocationAtByte12':'NOT EXERCISED: pinned runtime emitted 12-byte patch; BL continued in original code',
    'ownedIdentityResolution':'PASS with cooperative owner pin; direct observer pin FAIL',
    'hashBuildFingerprintRejection':'PASS','ambiguousInstanceGuard':'PASS synthetic shared-policy check; real duplicate instance NOT TESTED',
    'missingTargetNoHook':'PASS in owned process; not evidence about TikTok backend',
    'realEngineNullDuplicateRejection':'PASS','ownedCallbackLifecycleAudio':'PASS',
    'targetBindingAndDeployment':'NO-GO','realLiveAudioPreparation':'NO-GO','realReceiverSoundtrack':'NOT TESTED'},
    'pinBlocker':direct,'binding':{'stats':valid,'enumerations':final[1],'ownedMatches':final[2],'targetMatchesInOwnedProcess':final[3],
        'cooperativeOwnerPin':one('owned_owner_pin'),'heldReferenceReleased':True},
    'hook':{'entries':len(callbacks),'originalForwards':final[13],'capturedCallbacks':len(capture),'nestedControlEntries':128,'maxNested':final[15],
        'drops':final[16],'errors':final[17],'patchBytes':12,'first32Restored':True,'originalContinuation':hex(valid[10]+12),
        'trampolineBytes':trampoline.hex(),'patchedEntryBytes':patch.hex(),'branchEntries':final[28],
        'wrapperAdditionalUs':distribution([(e[4]-e[1])-(e[3]-e[2]) for e in capture],1000),
        'callbackGapMs':distribution(np.diff([e['callbackNs'] for e in buffers]),1e6),
        'timingLimit':'Instrumented wall-time section only; excludes final publication/decrement and entry/return branches. Not total hook overhead or a realtime bound.'},
    'encodedOutput':{'micExactlyEqualsDelivered':True,'pcmSeconds':len(mic)/2/48000,'encodedSeconds':len(encoded)/2/48000,'comparison':match},
    'realEngineRejections':{'null':null,'duplicate':duplicate,'sourceCommit':'05a09ac6807a6bb1726350e40ea4b127c1c79809'},
    'state':state,'priorEvidence':preservation,'fatalErrors':fatal,
    'targetInterceptionDeployed':False,'targetPcmAccessed':False,'targetModuleUpdated':False,
    'remainingUnknowns':['Foreign target lifetime/reference acquisition across linker namespaces',
        'Runtime liblyrax presence/load bias in actual TikTok and selected LIVE backend',
        'Any patch size beyond validated first12 bytes / BL relocation',
        'Safe installation before native capture starts; concurrent target patching not validated',
        'Actual target client PCM format/buffer ownership/handoff to encoder',
        'Independent LIVE receiver soundtrack and video stability']}
(P/'analysis.json').write_text(json.dumps(result,indent=2));(P/'events.jsonl').write_text((D/'events.jsonl').read_text())
(P/'runtime-excerpts.json').write_text(json.dumps([e for e in events if e['event'] not in ['binding_callback','buffer_complete','observer_callback']],indent=2))
print(json.dumps({key:result[key] for key in ['gates','hook','encodedOutput','pinBlocker']},indent=2))
