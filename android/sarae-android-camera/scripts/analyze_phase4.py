"""Evaluate only the evidence-backed 1920x1080 own-consumer improvement; not TikTok injection."""
import hashlib
import json
import re
from pathlib import Path

root=Path('test-results/phase4')
bridge=[json.loads(x) for x in (root/'consumer-bridge.jsonl').read_text(encoding='utf-8').splitlines()]
consumer=[json.loads(x) for x in (root/'consumer-frames.jsonl').read_text(encoding='utf-8').splitlines()]
frames=[x for x in consumer if x['kind']=='frame']
completes=[x for x in bridge if x['kind']=='switch_complete' and (x.get('switchId') or 0)>0]
switches=[]
for event in completes:
    pattern='A' if event['source'].endswith('/A.mp4') else 'B'
    observed=any(x['ns']>=event['ns'] and x['ns']<event['ns']+1_000_000_000 and f'pattern={pattern} ' in x['detail'] for x in frames)
    switches.append({'id':event['switchId'],'pattern':pattern,'consumer_observed':observed})
timestamps=[int(re.search(r'timestampNs=(\d+)',x['detail'])[1]) for x in frames]
regressions=sum(b<=a for a,b in zip(timestamps,timestamps[1:]))
errors=[x for x in bridge if x['kind'] in ('pipeline_error','decoder_error','preload_failed')]
black=sum('black=true' in x['detail'] for x in frames)
egl=[x for x in bridge if x['kind']=='egl_created']
criteria={
    'output_extent_1920x1080':len(egl)==1 and 'size=1920x1080 ' in egl[0]['detail'],
    'three_correct_switches':len(switches)==3 and all(x['consumer_observed'] for x in switches),
    'one_camera_session':sum(x['kind']=='session_started' for x in consumer)==1,
    'timestamps_strictly_increasing':regressions==0,
    'zero_black_frames':black==0,
    'no_pipeline_or_native_java_failure':not errors and not (root/'consumer-crash.txt').read_text(encoding='utf-8-sig').strip(),
    'clean_egl_release':sum(x['kind']=='egl_released' for x in bridge)==1,
}
durations={}
for path in root.glob('*run*.txt'):
    match=re.search(r'durationSeconds=([\d.]+)',path.read_text(encoding='utf-8-sig'))
    if match:durations[path.name]=float(match[1])
result={'own_consumer_result':'PASS' if all(criteria.values()) else 'FAIL','criteria':criteria,
        'frames':len(frames),'black_frames':black,'timestamp_non_increasing':regressions,
        'maximum_gap_ms':max(int(re.search(r'gapNs=(\d+)',x['detail'])[1]) for x in frames)/1e6,
        'switches':switches,'errors':errors,
        'measured_device_observation_runs_seconds':durations,'total_measured_run_seconds':sum(durations.values()),
        'all_runs_under_60_seconds':max(durations.values())<60,'total_under_300_seconds':sum(durations.values())<300,
        'tiktok_injection_result':'UNVERIFIED / BLOCKED: no TikTok APK instrumentation authorized',
        'runtime_api_observation':'Camera2 API Level 2 and CameraDeviceImpl.close; exact capture-session overload unknown'}
(root/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
inventory={}
for path in (Path('.tools/phase4-static/base.apk'),Path('.tools/phase4-static/camera-biz.apk'),
             Path('.tools/phase4-consumer/consumer-test-debug-487-lspatched.apk')):
    inventory[str(path)]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
(root/'artifact-inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
raise SystemExit(0 if all(criteria.values()) else 1)
