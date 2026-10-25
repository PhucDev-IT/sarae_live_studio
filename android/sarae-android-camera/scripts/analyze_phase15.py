"""Verify actual AAC/MP4 outputs, original microphone, and controlled callback contract."""
import hashlib,json,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np

P=Path('test-results/phase15');T=Path('.tools/phase15')
device_state=json.loads((P/'device-state.json').read_text())
D=T/'runs'/device_state['run_id'] if device_state.get('protocol_correction') else T/'device'
assert (D/'DONE').exists() and not (D/'ERROR').exists(),'Only analyze the uniquely identified completed run'
FFMPEG='D:/ffmpeg/bin/ffmpeg.exe';FFPROBE='D:/ffmpeg/bin/ffprobe.exe'
rows=[json.loads(line) for line in (D/'events.jsonl').read_text().splitlines()]
for row in rows:
    if isinstance(row.get('stats'),str):row['stats']=json.loads(row['stats'])
(P/'events.jsonl').write_text((D/'events.jsonl').read_text(),encoding='utf-8')

def decode(path):
    data=subprocess.run([FFMPEG,'-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-acodec','pcm_s16le','-ar','48000','-ac','2','-'],capture_output=True,timeout=20,check=True).stdout
    return np.frombuffer(data,dtype='<i2').reshape(-1,2).astype(np.float64)/32768

def rms(x):return float(np.sqrt(np.mean(x*x))) if x.size else 0.0
def db(x):return float(20*np.log10(max(rms(x),1e-12)))
def mono(x):return x.mean(axis=1)
def match(ref,actual,maxlag=4096):
    ref=np.asarray(ref);actual=np.asarray(actual)
    n=1<<int(len(ref)+len(actual)-1).bit_length()
    corr=np.fft.irfft(np.conj(np.fft.rfft(ref,n))*np.fft.rfft(actual,n),n)
    lags=np.arange(-maxlag,maxlag+1)
    lag=int(lags[np.argmax(corr[lags%n])])
    a=ref[max(0,-lag):];b=actual[max(0,lag):]
    length=min(len(a),len(b));a=a[:length];b=b[:length]
    a=a-a.mean();b=b-b.mean()
    value=float(np.dot(a,b)/max(1e-20,np.linalg.norm(a)*np.linalg.norm(b)))
    return {'lagFrames':lag,'lagMs':lag/48,'correlation':value}

def silence(x):
    samples=np.max(np.abs(x),axis=1)
    nonzero=np.flatnonzero(samples>0)
    leading=int(nonzero[0]) if len(nonzero) else len(samples)
    # Both exact-zero duration and an explicit 10 ms low-energy criterion.
    n=len(samples)//480
    levels=np.array([db(x[i*480:(i+1)*480]) for i in range(n)])
    silent=levels < -60
    starts=np.flatnonzero(np.diff(np.r_[False,silent,False].astype(np.int8))==1)
    ends=np.flatnonzero(np.diff(np.r_[False,silent,False].astype(np.int8))==-1)
    return {'exactZeroLeadingMs':leading/48,'lowEnergyThresholdDbfs':-60,'windowMs':10,
            'longestLowEnergyMs':int(max(ends-starts,default=0))*10,'rmsDbfs':db(x),'peak':float(np.max(np.abs(x),initial=0))}

result={'device':device_state,'outputDirectory':str(D),'modes':{}}
source=decode(T/'source.mp4')
suite=next(r for r in rows if r['event']=='suite_start')
loop_frames=round(suite['loopNs']*48000/1e9)
source=np.pad(source,((0,max(0,loop_frames-len(source))),(0,0)))[:loop_frames]
for mode in ['MICROPHONE','VIDEO','MUTED']:
    events=[r for r in rows if r.get('mode')==mode]
    blocks=[r for r in events if r['event']=='buffer_complete']
    complete=next((r for r in events if r['event']=='record_complete'),None)
    if not complete:
        result['modes'][mode]={'status':'FAIL','reason':'no record_complete'};continue
    mic=np.fromfile(D/f'{mode}-mic.s16',dtype='<i2').reshape(-1,2).astype(np.float64)/32768
    delivered=np.fromfile(D/f'{mode}-delivered.s16',dtype='<i2').reshape(-1,2).astype(np.float64)/32768
    encoded=decode(D/f'{mode}.mp4')
    probe=json.loads(subprocess.run([FFPROBE,'-v','error','-show_streams','-show_format','-of','json',str(D/f'{mode}.mp4')],capture_output=True,timeout=10,check=True).stdout)
    (P/f'{mode}-ffprobe.json').write_text(json.dumps(probe,indent=2),encoding='utf-8')
    packets=json.loads(subprocess.run([FFPROBE,'-v','error','-select_streams','a:0','-show_packets','-show_entries','packet=pts_time,duration_time','-of','json',str(D/f'{mode}.mp4')],capture_output=True,timeout=10,check=True).stdout)
    (P/f'{mode}-packets.json').write_text(json.dumps(packets,indent=2),encoding='utf-8')
    packet_pts=np.array([float(p['pts_time']) for p in packets['packets']])
    times=np.array([r['callbackNs'] for r in blocks],dtype=np.int64)
    gaps=np.diff(times)/1e6
    work=np.array([r['callbackWorkNs'] for r in blocks])/1e6
    receipt=np.array([(r['receiptNs']-r['callbackNs'])/1e6 for r in blocks])
    common_clock=bool(np.max(np.abs(receipt))<10_000)
    seq=[r['sequence'] for r in blocks]
    created=next(r for r in events if r['event']=='recorder_created')
    addresses=created['stats'][5:7]
    ownership=all(r['index']==r['sequence']%2 and r['address']==addresses[r['index']] for r in blocks)
    stats=complete['stats']
    item={'status':'PASS','blocks':len(blocks),'pcmSeconds':len(delivered)/48000,'encodedSeconds':len(encoded)/48000,
          'stats':stats,'sequenceContiguous':seq==list(range(len(seq))),
          'encodedPacketPtsStrictlyIncreasing':bool(np.all(np.diff(packet_pts)>0)),
          'encodedPacketCount':len(packet_pts),'maximumEncodedPacketPtsStepMs':float(np.max(np.diff(packet_pts))*1000),
          'fifoBufferIdentityVerified':ownership,'registeredBuffers':addresses,
          'callbackGapMs':{'max':float(gaps.max(initial=0)),'median':float(np.median(gaps)),'p99':float(np.percentile(gaps,99))},
          'callbackWorkBeforeHandoffMs':{'max':float(work.max(initial=0)),'p99':float(np.percentile(work,99))},
          'receiptDelayMs':{'max':float(receipt.max(initial=0)),'p99':float(np.percentile(receipt,99))} if common_clock else 'UNVERIFIED: native/Java clocks differ',
          'callbackStartupMs':(times[0]-stats[10])/1e6,
          'nativeMissingFrames':stats[2],'captureQueueDrops':stats[1],'enqueueFailures':stats[3],
          'microphone':silence(mic),'delivered':silence(delivered),'encoded':silence(encoded)}
    if mode!='MUTED':item['encodedVsDelivered']=match(mono(delivered),mono(encoded))
    if mode=='MICROPHONE':
        item['micExactlyPreserved']=bool(np.array_equal(mic,delivered))
        item['gate']=item['micExactlyPreserved'] and item['encodedVsDelivered']['correlation']>=.95 and rms(mic)>1e-5
    elif mode=='VIDEO':
        reference=np.tile(source,(int(np.ceil(len(delivered)/loop_frames)),1))[:len(delivered)]
        item['soundtrackVsDelivered']=match(mono(reference),mono(delivered))
        item['soundtrackVsEncoded']=match(mono(reference),mono(encoded))
        item['originalMicVsDeliveredCorrelation']=match(mono(mic),mono(delivered),1024)['correlation']
        feeder=[r for r in events if r['event']=='feeder_block']
        item['engineMissingFrames']=sum(r['missingFrames'] for r in feeder)
        item['engineMissingBlocks']=[{'block':r['block'],'mediaNs':r['mediaNs'],'epoch':r['epoch'],'missingFrames':r['missingFrames']} for r in feeder if r['missingFrames']]
        item['loopEpochs']=sorted({r['epoch'] for r in feeder})
        item['oneSecondSoundtrackProbes']=[]
        for second in range(1,int(len(delivered)/48000)-1):
            at=second*48000
            item['oneSecondSoundtrackProbes'].append({'second':second,**match(mono(reference[at:at+48000]),mono(delivered[at:at+48000]),2048)})
        item['gate']=item['soundtrackVsEncoded']['correlation']>=.95 and item['soundtrackVsDelivered']['correlation']>=.95
    else:
        item['gate']=bool(np.count_nonzero(delivered)==0 and np.max(np.abs(encoded),initial=0)<1/32768)
    item['status']='PASS' if item['gate'] and ownership and item['sequenceContiguous'] and item['encodedPacketPtsStrictlyIncreasing'] and stats[1]==0 and stats[3]==0 else 'FAIL'
    result['modes'][mode]=item

result['errors']=[r for r in rows if 'error' in r['event']]
result['suiteComplete']=any(r['event']=='suite_complete' for r in rows)
result['controlledRecorder']='PASS' if result['suiteComplete'] and not result['errors'] and all(m.get('fifoBufferIdentityVerified') and not m.get('enqueueFailures') and not m.get('captureQueueDrops') for m in result['modes'].values()) else 'FAIL'
result['encodedOutput']='PASS' if all(m.get('status')=='PASS' for m in result['modes'].values()) else 'FAIL'
result['nativeReplacement']=result['encodedOutput']
result['tiktokLivePcmEntry']='UNKNOWN'
result['tiktokNativeBridgeReadiness']='NO-GO'
initial=P/'initial-events.jsonl'
if initial.exists():
    original_rows=[json.loads(line) for line in initial.read_text().splitlines()]
    feed=[r for r in original_rows if r['event']=='feeder_block']
    captured=sum(r['event']=='buffer_complete' and r.get('mode')=='VIDEO' for r in original_rows)
    writes=[r for r in original_rows if r['event']=='pcm_write' and r.get('epoch')==0]
    errors=[round(r['ptsUs']*48000/1e6)-i*1024 for i,r in enumerate(writes)]
    result['initialFailure']={'allFeederMissingFrames':sum(r['missingFrames'] for r in feed),
        'capturedVideoBlocks':captured,'capturedFeederMissingFrames':sum(r['missingFrames'] for r in feed if r['block']<captured),
        'ptsDeviationFrom1024SampleClockFrames':{'min':min(errors),'max':max(errors)},
        'note':'Initial analysis omitted untagged feeder rows; recomputed here from preserved raw JSONL. No original evidence changed.'}
test=ET.parse(P/'PcmRingTest.xml').getroot()
result['unitTests']={'PcmRingTest':{'tests':int(test.get('tests')),'failures':int(test.get('failures')),'errors':int(test.get('errors')),'evidence':'PcmRingTest.xml'}}
result['verificationScope']='Controlled OpenSL recorder and audio-only AAC/MP4; no TikTok native capture or video A/V synchronization validation'
result['hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [T/'source.mp4',Path('native-audio-test/build/outputs/apk/debug/native-audio-test-debug.apk'),*D.glob('*')] if p.is_file()}
original=json.loads(Path('test-results/phase12/evidence-sha256.json').read_text())
changed=[name for name,digest in original.items() if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest]
result['phase12OriginalEvidence']={'filesChecked':len(original),'changed':changed}
(P/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in result.items() if key not in ['hashes','modes']},indent=2))
for mode,item in result['modes'].items():
    print(mode,json.dumps({key:value for key,value in item.items() if key not in ['stats','oneSecondSoundtrackProbes','engineMissingBlocks']},indent=2))
