"""Offline content alignment of the actual recorded file against fixture A.

Audio uses independently matched waveform offsets; video uses decoded frame appearance.
No sender clock log is substituted for recorded-output measurements.
"""
import json, subprocess
from pathlib import Path
import numpy as np
root=Path('test-results/phase10')
source=Path('../../backend/data/media/0275d431bc3c4d2b91ed0c54d54f0b78.mp4')
recording=Path('.tools/phase10/2026-10-24-093633174.mp4')
def frames(path):
    b=subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-v','error','-i',str(path),'-an','-vf','fps=30,scale=54:96','-pix_fmt','gray','-f','rawvideo','-'],capture_output=True,check=True).stdout
    return np.frombuffer(b,dtype=np.uint8).reshape(-1,96,54)
src=frames(source);rec=frames(recording)
# Subject ROI avoids moving watermarks and outer consumer letterboxing.
def vectors(images):
    x=images[:,12:90,12:46].astype(np.float64).reshape(len(images),-1)
    x-=x.mean(axis=1,keepdims=True)
    return x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-12)
ref=vectors(src);period=len(src)/30;result=[]
for segment in json.loads((root/'recorded-video-audio-analysis.json').read_text())['segments']:
    t=segment['recording_start_seconds']+segment['duration_seconds']/2
    match=segment['source_matches']['A'];aud=(match['source_offset_seconds']+segment['duration_seconds']/2)%period
    i=min(len(rec)-1,round(t*30));scores=ref@vectors(rec[i:i+1])[0]
    k=int(np.argmax(scores));video=k/30
    delta=((aud-video+period/2)%period-period/2)*1000
    result.append({'recorded_midpoint_seconds':t,'audio_source_midpoint_seconds':aud,'video_source_seconds':video,
                   'audio_waveform_correlation':match['correlation'],'video_frame_correlation':float(scores[k]),
                   'audio_ahead_of_video_ms':delta,'recorded_frame_index':i,'source_frame_index':k})
value={'method':'Independent waveform correlation and subject-ROI grayscale frame matching at 30 fps',
       'source_video_period_seconds':period,'samples':result,
       'minimum_offset_ms':min(e['audio_ahead_of_video_ms'] for e in result),
       'maximum_offset_ms':max(e['audio_ahead_of_video_ms'] for e in result),
       'endpoint_offset_change_ms':result[-1]['audio_ahead_of_video_ms']-result[0]['audio_ahead_of_video_ms'],
       'limitations':['Frame matching has at least one-frame (~33 ms) temporal uncertainty',
                      'Two-second audio windows smooth local discontinuities; not a lip-sync or jitter certification',
                      'A single short source-A recording does not verify A/B switching or long-term drift']}
(root/'recorded-sync-analysis.json').write_text(json.dumps(value,indent=2),encoding='utf-8');print(json.dumps(value,indent=2))
