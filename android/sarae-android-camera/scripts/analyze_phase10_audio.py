"""Waveform evidence, never equate successful PCM decoding with TikTok recorded output."""
import argparse,hashlib,json,math,re,subprocess,wave
from pathlib import Path
import numpy as np

p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--label',required=True);p.add_argument('--raw',action='store_true')
a=p.parse_args();assert re.fullmatch('[a-z0-9-]+',a.label)
root=Path('test-results/phase10');temp=Path('.tools/phase10')
def read_wav(path):
    with wave.open(str(path),'rb') as w:
        assert w.getsampwidth()==2 and w.getframerate()==48000 and w.getnchannels()==1
        return np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').astype(np.float64)/32768
if a.raw:
    signal=np.frombuffer(a.input.read_bytes(),dtype='<i2').astype(np.float64)/32768
else:
    out=temp/(a.label+'-analysis.wav')
    subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-y','-v','error','-i',str(a.input),'-vn','-ac','1','-ar','48000','-c:a','pcm_s16le',str(out)],check=True)
    signal=read_wav(out)
def match(segment,ref):
    # Compare waveform after identical 48 kHz extraction, decimated equally for bounded offline FFT.
    x=segment[::6].copy(); y=np.tile(ref,3)[::6].copy();n=len(x)
    if not n or n>len(y):return None
    x-=x.mean();y-=y.mean();ex=float(x@x)
    if ex<1e-12:return {'correlation':0,'source_offset_seconds':None,'reason':'silent segment'}
    size=1<<(len(y)+n-2).bit_length()
    dot=np.fft.irfft(np.fft.rfft(y,size)*np.fft.rfft(x[::-1],size),size)[n-1:len(y)]
    energy=np.r_[0,np.cumsum(y*y)];energy=energy[n:]-energy[:-n]
    corr=dot/np.sqrt(np.maximum(energy*ex,1e-24))
    k=int(np.argmax(np.abs(corr)))
    return {'correlation':float(corr[k]),'source_offset_seconds':(k/8000)%(len(ref)/48000)}
references={label:read_wav(temp/('source-'+label+'.wav')) for label in ['A','B']}
duration=len(signal)/48000
segments=[]
for start in sorted(set([0,max(0,duration/2-1),max(0,duration-2)])):
    segment=signal[int(start*48000):int((start+min(2,duration))*48000)]
    segments.append({'recording_start_seconds':start,'duration_seconds':len(segment)/48000,
                     'source_matches':{label:match(segment,ref) for label,ref in references.items()}})
result={'file':str(a.input),'sha256':hashlib.sha256(a.input.read_bytes()).hexdigest(),'duration_seconds':duration,
        'rms_dbfs':20*math.log10(max(float(np.sqrt(np.mean(signal*signal))),1e-12)),
        'peak_dbfs':20*math.log10(max(float(np.max(np.abs(signal))),1e-12)),
        'exact_zero_fraction':float(np.mean(signal==0)), 'segments':segments,
        'limits':['Correlation is waveform similarity, not proof of capture API or motion/AV sync','AAC re-encoding, app DSP and drift can reduce similarity; inspect evidence before assigning PASS','Silence detection is measured output amplitude, not merely MUTED mode acknowledgement']}
(root/(a.label+'-audio-analysis.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
