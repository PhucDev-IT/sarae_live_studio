import json
from pathlib import Path
from analyze_phase3 import read

result=Path('test-results/phase3/lifecycle')
evidence={}
for name in ('bridge','consumer'):
    rows=read(result/f'{name}.jsonl')
    evidence[name]=rows
    (result/f'{name}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
frames=[row for row in evidence['consumer'] if row['kind']=='frame']
generations=sorted({row['generation'] for row in frames})
errors=[row for row in evidence['bridge'] if row['kind'] in ('pipeline_error','decoder_error','preload_failed')]
creates=sum(row['kind']=='egl_created' for row in evidence['bridge'])
releases=sum(row['kind']=='egl_released' for row in evidence['bridge'])
switches=sum(row['kind']=='switch_complete' and (row.get('switchId') or 0)>0 for row in evidence['bridge'])
decoder_created=sum(row['kind']=='decoder_started' for row in evidence['bridge'])
decoder_released=sum(row['kind']=='decoder_released' for row in evidence['bridge'])
summary={'result':'PASS' if generations==[1,2] and creates==releases==2 and switches==3 and decoder_created==decoder_released and not errors else 'FAIL',
         'generations_with_frames':generations,'egl_created':creates,'egl_released':releases,
         'switches_completed':switches,'frames_received':len(frames),'errors':errors,
         'decoder_created':decoder_created,'decoder_released':decoder_released,
         'limit':'Surface destruction while backgrounded ends the session/producer; foreground creates a new session/EGL lifecycle. Gap acceptance applies only to foreground endurance.'}
(result/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
