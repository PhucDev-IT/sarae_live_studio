"""Summarize the opt-in full-app measurements and copy unchanged WAVs for listening.

Usage: python scripts/tts_phase6_report.py INPUT_DIR OUTPUT_DIR
Never reads production storage or modifies audio.
"""
import csv
import hashlib
import html
import json
import math
import shutil
import statistics
import sys
import wave
from collections import defaultdict
from pathlib import Path


def main(source:Path,target:Path):
    target.mkdir(parents=True,exist_ok=True)
    rows=json.loads((source/'measurements.json').read_text(encoding='utf-8'))
    for row in rows:
        if row['scenario']=='hit_direct' and row['job']['chars']==len('Dạ vâng ạ.'):
            row['scenario']='soak_file_db_short'
    groups=defaultdict(list)
    matched=0;pcm=0;audio_files={}
    for r in rows:
        uid=r['utterance'];job=r['job']
        wav=source/(uid+'.wav')
        if not wav.exists():wav=source/f"{r['scenario']}-{r['i']:02d}.wav"
        if not wav.exists():continue
        audio_files[uid]=wav
        key=hashlib.sha256(json.dumps(['omnivoice',job['voice_id'],uid,job['job_id'],'demo_default'],sort_keys=True).encode()).hexdigest()
        caches=list(source.glob(f'agent-home-*/voice/cache/{key}.wav'))
        if any(hashlib.sha256(p.read_bytes()).digest()==hashlib.sha256(wav.read_bytes()).digest() for p in caches):matched+=1
        with wave.open(str(wav)) as w:
            if (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,24000):pcm+=1
        groups[(r['scenario'],job['source'],r['playing']['audio_path'])].append(r)
    summaries=[]
    fields={'e2e_ms':lambda r:r.get('e2e_ms'),'orchestration_ms':lambda r:r.get('orchestration_ms'),
        'delivery_ms':lambda r:r.get('delivery_ms'),'gpu_queue_ms':lambda r:r['job']['timings'].get('queue_ms',0),
        'inference_ms':lambda r:r['job']['timings'].get('inference_ms',0),
        'wait_audio_ms':lambda r:r['playing'].get('wait_audio_ms'),
        'download_ms':lambda r:r['playing'].get('download_ms'),'obs_startup_ms':lambda r:r['playing'].get('obs_startup_ms')}
    for (scenario,cache,path),items in sorted(groups.items()):
        entry={'scenario':scenario,'source':cache,'path':path,'n':len(items),'metrics':{}}
        for name,get in fields.items():
            values=sorted(v for r in items if (v:=get(r)) is not None)
            if values:entry['metrics'][name]={'n':len(values),'p50':round(statistics.median(values),1),
                'p95':round(values[math.ceil(.95*len(values))-1],1) if len(values)>=20 else None,'min':min(values),'max':max(values)}
        summaries.append(entry)
    result={'samples':len(rows),'available_wavs':len(audio_files),'byte_identical_agent_cache':matched,
        'pcm16_mono_24k':pcm,'percentile_method':'nearest-rank p95 only n>=20; p50 median', 'groups':summaries}
    (target/'latency.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (target/'latency.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['scenario','source','path','n','metric','metric_n','p50_ms','p95_ms','min_ms','max_ms'])
        for g in summaries:
            for metric,v in g['metrics'].items():writer.writerow([g['scenario'],g['source'],g['path'],g['n'],metric,v['n'],v['p50'],v['p95'],v['min'],v['max']])
    with (target/'samples.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(['sample','scenario','source','path',*fields])
        for i,r in enumerate(rows):writer.writerow([i,r['scenario'],r['job']['source'],r['playing']['audio_path'],*[get(r) for get in fields.values()]])
    selected=[]
    for scenario in ('miss_direct','hit_direct','hit_relay','intro_body','prepared','reconnect'):
        chosen=[r for r in rows if r['scenario']==scenario and r['utterance'] in audio_files][:3]
        for r in chosen:
            filename=f"{scenario}-{len(selected):02d}.wav";shutil.copy2(audio_files[r['utterance']],target/filename)
            intro=source/(r['utterance']+'-intro.wav')
            if intro.exists():
                intro_name=filename.replace('.wav','-intro.wav');shutil.copy2(intro,target/intro_name)
                selected.append((scenario+' — intro',intro_name))
            selected.append((scenario+' — body',filename))
    content='<!doctype html><meta charset="utf-8"><title>Sarae Phase6 — nghe duyệt WAV</title><style>body{font:16px system-ui;max-width:900px;margin:40px auto;padding:16px;background:#f6f5fb;color:#24213a}article{background:white;border:1px solid #ddd;padding:16px;border-radius:12px;margin:12px 0}audio{width:100%}</style><h1>Sarae Phase6 — bộ WAV nghe duyệt</h1><p>WAV nguyên byte từ voice-server, không EQ/resample. Intro và body nằm riêng theo đúng thứ tự; trình nghe không tự nối hoặc sửa waveform. Kiểm tra từ đầu/cuối, click, tên, giá, size, “nha/nhé/ạ”. Chưa coi ASR hoặc OBS PLAYING là phê duyệt chất lượng.</p>'
    for label,file in selected:content+=f'<article><h3>{html.escape(label)}</h3><audio controls src="{html.escape(file)}"></audio><a href="{html.escape(file)}">WAV gốc</a></article>'
    (target/'listen.html').write_text(content,encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
