"""Offline recorded-output identification, independent of sender hook acknowledgments."""
import collections,hashlib,json,math,re,subprocess,wave
from pathlib import Path
import numpy as np
ROOT=Path('test-results/phase11');TEMP=Path('.tools/phase11')
state=json.loads((ROOT/'state.json').read_text());record=Path(state['recorded_file'])
sources={'A':Path('../../backend/data/media/0275d431bc3c4d2b91ed0c54d54f0b78.mp4'),
         'B':Path('../../backend/data/media/47ca398498084dcbb510b69adae93db2.mp4')}
def ff(args):return subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-v','error',*args],capture_output=True,check=True).stdout
def audio(path):return np.frombuffer(ff(['-i',str(path),'-vn','-ac','1','-ar','8000','-f','f32le','-']),dtype='<f4').astype(np.float64)
def frames(path):return np.frombuffer(ff(['-i',str(path),'-an','-vf','fps=30,scale=54:96','-pix_fmt','gray','-f','rawvideo','-']),dtype=np.uint8).reshape(-1,96,54)
def vectors(x):
    y=x[:,12:90,12:46].astype(np.float32).reshape(len(x),-1);y-=y.mean(axis=1,keepdims=True)
    return y/np.maximum(np.linalg.norm(y,axis=1,keepdims=True),1e-12)
def normalize_corr(signal,template):
    x=template-template.mean();y=signal; n=len(x)
    size=1<<(len(y)+n-2).bit_length()
    corr=np.fft.irfft(np.fft.rfft(y,size)*np.fft.rfft(x[::-1],size),size)[n-1:len(y)]
    sums=np.r_[0,np.cumsum(y)];energy=np.r_[0,np.cumsum(y*y)]
    norm=(energy[n:]-energy[:-n])-(sums[n:]-sums[:-n])**2/n
    return corr/np.sqrt(np.maximum(norm*float(x@x),1e-24))
record_audio=audio(record);ref_audio={k:audio(v) for k,v in sources.items()}
video=frames(record);reference_frames={k:frames(v) for k,v in sources.items()}
rv=vectors(video);scores={};indices={}
for key,images in reference_frames.items():
    dot=rv@vectors(images).T;indices[key]=np.argmax(dot,axis=1);scores[key]=np.max(dot,axis=1)
labels=np.where(scores['A']>=scores['B'],'A','B')
segments=[];start=0
for i in range(1,len(labels)+1):
    if i==len(labels) or labels[i]!=labels[start]:
        key=str(labels[start]);segments.append({'source':key,'start_seconds':start/30,'end_seconds':i/30,'frames':i-start,
                                               'minimum_frame_correlation':float(scores[key][start:i].min())});start=i
# Audio identity in the middle of every actual video segment; no log-based source labels.
for segment in segments:
    center=(segment['start_seconds']+segment['end_seconds'])/2
    begin=max(segment['start_seconds']+.15,center-1)
    n=min(2,segment['end_seconds']-begin)
    if n<.2:continue
    sample=record_audio[round(begin*8000):round((begin+n)*8000)]
    matches={}
    for key,ref in ref_audio.items():
        corr=normalize_corr(np.tile(ref,3),sample);at=int(np.argmax(np.abs(corr)))
        matches[key]={'correlation':float(corr[at]),'source_offset_seconds':(at/8000)%(len(ref)/8000)}
    segment['audio_probe_start_seconds']=begin;segment['audio_matches']=matches
    probes=[]
    for at in np.arange(segment['start_seconds']+.5,segment['end_seconds']-1,.75):
        sample=record_audio[round(at*8000):round((at+1)*8000)]
        ref=ref_audio[segment['source']];corr=normalize_corr(np.tile(ref,3),sample);k=int(np.argmax(np.abs(corr)))
        probes.append({'recorded_start_seconds':float(at),'correlation':float(corr[k]),'source_offset_seconds':(k/8000)%(len(ref)/8000)})
    segment['one_second_source_probes']=probes
    if probes:segment['one_second_probe_correlation_range']=[min(abs(q['correlation']) for q in probes),max(abs(q['correlation']) for q in probes)]
transitions=[]
for prev,segment in zip(segments,segments[1:]):
    if segment['frames']<3:continue
    key=segment['source'];t=segment['start_seconds']
    # Match a distinctive incoming 0.1..0.7s audio excerpt near the actual pixel transition.
    template_start=.1;template=ref_audio[key][800:5600]
    begin=max(0,t-.5);end=min(len(record_audio)/8000,t+1.5)
    corr=normalize_corr(record_audio[round(begin*8000):round(end*8000)],template)
    k=int(np.argmax(np.abs(corr)));audio_origin=begin+k/8000-template_start
    lo=round((t+.1)*30);hi=min(len(video),round((t+.8)*30))
    video_origins=np.arange(lo,hi)/30-indices[key][lo:hi]/30
    video_origin=float(np.median(video_origins))
    # Resolve old/new waveform identity across the boundary in 40ms windows (10ms steps).
    old=prev['source'];old_begin=max(prev['start_seconds'],t-1)
    outgoing_sample=record_audio[round(old_begin*8000):round((old_begin+.6)*8000)]
    outgoing_corr=normalize_corr(np.tile(ref_audio[old],3),outgoing_sample);old_at=int(np.argmax(np.abs(outgoing_corr)))
    outgoing_offset=old_at/8000-old_begin
    local=[]
    for center in np.arange(t-.08,t+.13,.01):
        begin_sample=round((center-.02)*8000);length=320
        observed=record_audio[begin_sample:begin_sample+length];observed=observed-observed.mean()
        correlations={}
        for label,offset in [(old,outgoing_offset),(key,-audio_origin)]:
            reference=ref_audio[label];index=round((begin_sample/8000+offset)*8000)
            # Outgoing match may have landed in one of the tiled source copies.
            index=index%len(reference)
            expected=np.tile(reference,2)[index:index+length];expected=expected-expected.mean()
            correlations[label]=float((observed@expected)/max(float(np.linalg.norm(observed)*np.linalg.norm(expected)),1e-12))
        local.append({'center_relative_to_first_new_pixel_ms':round((center-t)*1000,3),'correlations':correlations})
    transitions.append({'previous_source':prev['source'],'new_source':key,'first_new_pixel_seconds':t,
                        'estimated_video_source_origin_seconds':video_origin,'estimated_audio_source_origin_seconds':audio_origin,
                        'incoming_audio_correlation':float(corr[k]),'audio_lag_relative_to_video_ms':(audio_origin-video_origin)*1000,
                        'measurement_resolution_ms':33.3333,'boundary_waveform_windows_40ms':local})
# Full-file silence measurement is separate from engine underrun counters.
pcm16=np.frombuffer(ff(['-i',str(record),'-vn','-ac','1','-ar','48000','-f','s16le','-']),dtype='<i2')
nonzero=np.flatnonzero(pcm16);leading=(int(nonzero[0]) if len(nonzero) else len(pcm16))/48
audio_events=[json.loads(l) for l in (ROOT/'final-audio.jsonl').read_text().splitlines()]
bridge=[json.loads(l) for l in (ROOT/'final-bridge.jsonl').read_text().splitlines()]
capture_start=next(e['ns'] for e in audio_events if e['kind']=='audio_capture_start')
capture_stop=next(e['ns'] for e in audio_events if e['kind']=='audio_capture_stop' and e['ns']>capture_start)
active_video=[e for e in bridge if capture_start<=e['ns']<=capture_stop]
missing=[e for e in audio_events if e.get('missingFrames',0)>0]
commits=[e for e in audio_events if e['kind']=='audio_source_commit' and capture_start<e['ns']<capture_stop]
read_transitions=[e for e in audio_events if e['kind']=='audio_read_source_transition']
for e in commits:
    first=next((r for r in read_transitions if r['key']==e['key'] and r['ns']>=e['ns']),None)
    if first:e['first_read_commit_delay_ms']=(first['ns']-e['ns'])/1e6
gaps=[int(re.search(r'gapNs=(\d+)',e['detail'])[1])/1e6 for e in active_video if e['kind']=='output_present']
counts=collections.Counter(e['kind'] for e in active_video)
value={'recording':str(record),'sha256':hashlib.sha256(record.read_bytes()).hexdigest(),'audio_duration_seconds':len(record_audio)/8000,
       'video_segments':segments,'recorded_source_transitions':transitions,'leading_exact_zero_pcm_ms':leading,
       'recorded_rms_dbfs':20*math.log10(max(float(np.sqrt(np.mean(record_audio**2))),1e-12)),
       'audio_event_counts':dict(collections.Counter(e['kind'] for e in audio_events)),
       'missing_pcm_frames':sum(e['missingFrames'] for e in missing),'missing_pcm_ms':sum(e['missingFrames'] for e in missing)/44.1,
       'underruns':missing,'source_commits_during_capture':commits,
       'active_camera_counts':dict(counts),'active_video_max_gap_ms':max(gaps,default=None),
       'active_output_frames':len(gaps),'active_output_pts_regressions':sum(b['outputPtsNs']<=a['outputPtsNs'] for a,b in zip([e for e in active_video if e['kind']=='output_present'],[e for e in active_video if e['kind']=='output_present'][1:])),
       'lifecycle_errors':[e for e in audio_events if 'error' in e['kind']],
       'device_seconds':state['spent'],
       'limitations':['Video identity/origin is estimated from decoded subject ROI at 30 fps (~33ms resolution)',
                      'Audio correlation measures waveform identity, not subjective audibility',
                      'Negative audio lag means soundtrack origin is earlier than corresponding video origin',
                      'Silence from missing PCM is reported even when shorter than analysis resolution']}
baseline=Path('.tools/phase10/recorded-video-analysis.wav')
if baseline.exists():
    with wave.open(str(baseline),'rb') as wav:
        original=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2');at=np.flatnonzero(original)
        value['phase10_recorded_leading_zero_pcm_ms']=float((at[0] if len(at) else len(original))*1000/wav.getframerate())
(ROOT/'analysis.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
np.savez_compressed(TEMP/'recorded-frame-matches.npz',labels=labels,scores_a=scores['A'],scores_b=scores['B'],indices_a=indices['A'],indices_b=indices['B'])
print(json.dumps({k:v for k,v in value.items() if k not in ['underruns','active_camera_counts','lifecycle_errors']},indent=2))
