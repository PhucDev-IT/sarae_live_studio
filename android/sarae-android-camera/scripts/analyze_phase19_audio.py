"""Offline native loading/forwarding, encoded content, lifecycle and ELF gates."""
import hashlib,json,re,struct,subprocess,zipfile
from pathlib import Path
import numpy as np

P=Path('test-results/phase19');T=Path('.tools/phase19')
state=json.loads((P/'device-state.json').read_text());art=json.loads((P/'artifacts.json').read_text())
D=T/state['run'];assert (D/'DONE').exists() and not (D/'ERROR').exists()
events=[json.loads(line) for line in (D/'events.jsonl').read_text().splitlines()]
(P/'events.jsonl').write_text((D/'events.jsonl').read_text(),encoding='utf-8')
module=[json.loads(line) for line in (P/'module-events.jsonl').read_text().splitlines()]
loaded=next(e for e in module if e['event']=='native_loaded' and str(e['pid'])==state['ownPid'])
initialized=next(e for e in events if e['event']=='observer_library_initialized')['stats']
cleanup=next(e for e in events if e['event']=='observer_library_cleanup')['stats']
maps=(T/'own-maps.txt').read_text(errors='replace')
# Android's /data/user/0 package path and /data/data alias name the same user-0
# storage; proc maps reports the latter. Preserve both observed spellings.
mapped_library=loaded['library'].replace('/data/user/0/','/data/data/')
loading=(loaded['sha256']==state['loadedObserverSha256']==art['observerSoSha256'] and mapped_library in maps
         and initialized[0]==0x53415241454f4253 and initialized[1]==1 and initialized[2]==initialized[4]==int(state['ownPid'])
         and initialized[3]==loaded['tid']==initialized[5] and art['observerAbsentFromHostApk'])

def distribution(data,divisor=1):
    x=np.asarray(data,dtype=np.float64)/divisor
    return {'count':len(x),'min':float(x.min()),'median':float(np.median(x)),'p99':float(np.percentile(x,99)),'max':float(x.max())}
def read_pcm(path):return np.fromfile(path,dtype='<i2').reshape(-1,2).astype(np.float64)/32768
def match(reference,recorded):
    a=reference.mean(axis=1);b=recorded.mean(axis=1)
    n=1<<int(len(a)+len(b)-1).bit_length()
    c=np.fft.irfft(np.conj(np.fft.rfft(a,n))*np.fft.rfft(b,n),n)
    lags=np.arange(-4096,4097);lag=int(lags[np.argmax(c[lags%n])])
    a=a[max(0,-lag):];b=b[max(0,lag):];length=min(len(a),len(b));a=a[:length];b=b[:length]
    a=a-a.mean();b=b-b.mean()
    return {'lagFrames':lag,'lagMs':lag/48,'correlation':float(np.dot(a,b)/max(1e-20,np.linalg.norm(a)*np.linalg.norm(b)))}

results={'device':state,'loading':{'result':'PASS' if loading else 'FAIL','librarySha256':loaded['sha256'],
    'loadPid':initialized[2],'loadTid':initialized[3],'currentWorkerTid':initialized[5],
    'moduleJavaThread':loaded['javaThread'],'loadedFrom':loaded['library'],'mappedFrom':mapped_library,'moduleClassLoader':module[0]['classLoader'],
    'method':'LSPatch Xposed module extracts only its packaged observer to owned code cache; System.load + JNI_OnLoad + C API',
    'scope':'owned harness only; TikTok-process load NOT TESTED'},'cases':{}}
for name in ['BASELINE','OBSERVED']:
    rows=[e for e in events if e.get('mode')==name]
    buffers=[e for e in rows if e['event']=='buffer_complete'];complete=next(e for e in rows if e['event']=='record_complete')
    mic=read_pcm(D/f'{name}-mic.s16');out=read_pcm(D/f'{name}-delivered.s16')
    raw=subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-v','error','-i',str(D/f'{name}.mp4'),'-f','s16le','-acodec','pcm_s16le','-ar','48000','-ac','2','-'],capture_output=True,timeout=15,check=True).stdout
    encoded=np.frombuffer(raw,dtype='<i2').reshape(-1,2).astype(np.float64)/32768
    correlation=match(mic,encoded)
    timestamps=np.array([e['callbackNs'] for e in buffers],dtype=np.int64)
    created=next(e for e in rows if e['event']=='recorder_created')['stats']
    item={'blocks':len(buffers),'pcmSeconds':len(mic)/48000,'encodedSeconds':len(encoded)/48000,
          'micExactlyEqualsDelivered':bool(np.array_equal(mic,out)),'micRmsDbfs':float(20*np.log10(max(1e-12,np.sqrt(np.mean(mic*mic))))),
          'encodedVsOriginalMic':correlation,'sequenceContiguous':[e['sequence'] for e in buffers]==list(range(len(buffers))),
          'fifoAddressesMatch':all(e['index']==e['sequence']%2 and e['address']==created[5+e['index']] for e in buffers),
          'nativeStats':complete['stats'],'callbackGapMs':distribution(np.diff(timestamps),1e6),
          'recorderWorkBeforeHandoffUs':distribution([e['callbackWorkNs'] for e in buffers],1000)}
    probe=subprocess.run(['D:/ffmpeg/bin/ffprobe.exe','-v','error','-show_streams','-show_format','-of','json',str(D/f'{name}.mp4')],capture_output=True,timeout=8,check=True)
    (P/f'{name}-ffprobe.json').write_bytes(probe.stdout)
    item['result']='PASS' if item['micExactlyEqualsDelivered'] and item['sequenceContiguous'] and item['fifoAddressesMatch'] and complete['stats'][1:4]==[0,0,0] and correlation['correlation']>.95 else 'FAIL'
    results['cases'][name]=item

obs=[e for e in events if e['event']=='observer_callback']
registered=next(e for e in events if e['event']=='observer_registered')['stats']
after_stop=next(e for e in events if e['event']=='observer_after_stop')
after_destroy=next(e for e in events if e['event']=='observer_after_destroy')
final=after_destroy['after']
arguments=all(e['queue']==registered[10] and e['forwardedContext']==registered[9] and e['originalFunction']==registered[8] and e['flags']==0 for e in obs)
forwarding=(len(obs)==final[0]==final[1]==results['cases']['OBSERVED']['blocks'] and final[2:7]==[0,0,0,0,0] and arguments
            and [e['sequence'] for e in obs]==list(range(len(obs))) and all(e['entryNs']<=e['originalStartNs']<=e['originalEndNs']<=e['exitNs']<=e['receiptNs'] for e in obs))
core=[e['exitNs']-e['entryNs'] for e in obs]
original=[e['originalEndNs']-e['originalStartNs'] for e in obs]
overhead=[full-work for full,work in zip(core,original)]
results['observation']={'result':'PASS' if forwarding else 'FAIL','callbacks':len(obs),'forwardedCalls':final[1],
    'originalArgumentsPreserved':arguments,'telemetryDrops':final[2],'queueMismatches':final[3],'concurrentCallbacks':final[4],
    'lateCallbacks':final[6],'callbackNativeTids':sorted({e['nativeTid'] for e in obs}),'tidChanges':final[15],
    'originalFunction':registered[8],'originalContext':registered[9],'queue':registered[10],'relayContext':registered[11],
    'packetCapacity':256,'telemetryStorageBytes':256*80,
    'observerCoreUs':distribution(core,1000),'originalCallbackInclusiveUs':distribution(original,1000),
    'additionalMeasuredObserverUs':distribution(overhead,1000),
    'timingLimit':'Includes packet copy and timestamp up to publication; excludes final release-store/flag-clear/in-flight decrement/return. Not a hard realtime bound.',
    'eventReceiptMs':distribution([e['receiptNs']-e['entryNs'] for e in obs],1e6)}
expected_errors=[e for e in events if e['event']=='expected_native_error']
lifecycle=(after_stop['before']==after_stop['after'] and after_destroy['before']==after_destroy['after'] and final[7]==1
           and cleanup[7:11]==[0,0,1,1] and cleanup[11]==1 and len(expected_errors)==1)
results['lifecycle']={'result':'PASS' if lifecycle else 'FAIL','postStopWindowMs':after_stop['windowMs'],
    'postDestroyWindowMs':after_destroy['windowMs'],'postStopStable':after_stop['before']==after_stop['after'],
    'postDestroyStable':after_destroy['before']==after_destroy['after'],'cleanupStats':cleanup,
    'expectedError':expected_errors,'nativeLibraryUnloaded':False,'note':'Relay resources freed; JNI library intentionally remains mapped until process exit'}
results['unexpectedErrors']=[e for e in events if 'error' in e['event'] and e['event']!='expected_native_error']
results['decodersCreated']=sum(e['event']=='audio_decoder_created' for e in events)


results['overall']='PASS: owned observer; native-hook gates evaluated separately' if loading and forwarding and lifecycle and all(x['result']=='PASS' for x in results['cases'].values()) and not results['unexpectedErrors'] else 'FAIL'
(P/'controlled-audio-analysis.json').write_text(json.dumps(results,indent=2))
print(json.dumps({k:v for k,v in results.items() if k not in ['loading','cases']},indent=2))
for name,item in results['cases'].items():print(name,json.dumps(item,indent=2))
