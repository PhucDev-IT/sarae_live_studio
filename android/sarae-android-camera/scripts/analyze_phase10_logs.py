"""Summarize only collected evidence; no acceptance thresholds are changed here."""
import collections, hashlib, json, re
from pathlib import Path
root=Path('test-results/phase10')
def events(paths):
    unique={}
    for p in paths:
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            try:
                e=json.loads(line);unique[(e.get('pid'),e.get('kind'),e.get('ns'))]=e
            except json.JSONDecodeError: pass
    return sorted(unique.values(),key=lambda e:e.get('ns',0))
audio=events(root.glob('*-audio.jsonl')); video=events(root.glob('*-bridge.jsonl'))
sub=[e for e in audio if e.get('kind')=='pcm_substituted']
read=[e for e in audio if e.get('kind')=='audio_read_confirmed']
def errors(es):return [e for e in es if any(s in e.get('kind','') for s in ['error','dropped'])]
gaps=[]
for e in video:
    if e.get('kind')=='output_present':
        match=re.search(r'gapNs=(\d+)',e.get('detail',''))
        if match:gaps.append(int(match[1])/1e6)
result={'audio_event_counts':dict(collections.Counter(e.get('kind') for e in audio)),
        'confirmed_reads':read,'selftests':[e for e in audio if e.get('kind')=='audio_selftest_complete'],
        'substitution_modes':dict(collections.Counter(e.get('mode') for e in sub)),
        'logged_underrun_reads':[e for e in sub if e.get('missingFrames',0)>0],
        'audio_errors':errors(audio),'video_errors':errors(video),
        'source_commits':[e for e in audio if e.get('kind')=='audio_source_commit'],
        'clock_resyncs':[e for e in audio if e.get('kind')=='audio_clock_resync'],
        'maximum_observed_ring_overrun_frames':max([e.get('overruns',0) for e in audio]+[0]),
        'video_output_max_gap_ms':max(gaps,default=None),'video_gaps_over_100ms':sum(g>=100 for g in gaps),
        'limits':['PCM substitution logs sample successful reads; missing-frame reads are all logged',
                  'Clock drift is before the app encoder; recorded-output synchronization needs independent file analysis',
                  'A Java probe does not establish that every TikTok mode uses Java AudioRecord']}
(root/'runtime-analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['clock_resyncs','source_commits','logged_underrun_reads','confirmed_reads']},indent=2))
