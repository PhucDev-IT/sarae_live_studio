"""Evaluate the actual lab TikTok preview, using its UI pixels and scoped bridge trace."""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

root=Path('test-results/phase6')
preview=root/'actual-preview'
def read(path): return path.read_text(encoding='utf-8-sig')
def load(path): return json.loads(read(path))
def rows(path): return [json.loads(l) for l in read(path).splitlines() if l.strip()]
def value(row,name): return int(re.search(rf'\b{name}=(\d+)',row['detail'])[1])

trace=rows(next((preview/'jsonl-after').rglob('*.jsonl')))
switches=[r for r in trace if r['kind']=='switch']
assert len(switches)==2
first=switches[0]['ns']
session=next(r for r in reversed(trace) if r['kind']=='session_redirect' and r['ns']<first)
generation=sum(r['kind']=='session_redirect' and r['ns']<=session['ns'] for r in trace)
closed=next(r for r in trace if r['kind']=='camera_close' and r['ns']>first)
active=[r for r in trace if session['ns']<=r['ns']<closed['ns']]
window=[r for r in active if r['ns']>=first]
output=[r for r in active if r['kind']=='output_present']
window_output=[r for r in window if r['kind']=='output_present']
completes=[r for r in window if r['kind']=='switch_complete']
requests=[r for r in window if r['kind']=='switch_request']
commands=rows(preview/'switch-commands.jsonl')
pixels=load(preview/'pixel-evidence.json')
observations=[]
for name,expected in [('A-before.png','A'),('B-after.png','B'),('A-after.png','A')]:
    p=next(p for p in pixels if p['image']==name)
    red,green,blue=p['rgb']
    actual='A' if red>200 and green<40 and blue<40 else 'B' if blue>200 and red<40 and green<40 else 'UNKNOWN'
    observations.append({**p,'expected':expected,'observed':actual,'sha256':hashlib.sha256((preview/name).read_bytes()).hexdigest()})

def metrics(events):
    pts=[r['outputPtsNs'] for r in events]
    return {'frames':len(events),'output_pts_non_increasing':sum(b<=a for a,b in zip(pts,pts[1:])),
            'maximum_output_gap_ms':max((value(r,'gapNs') for r in events),default=0)/1e6,
            'event_cadence_fps':(len(events)-1)*1e9/(events[-1]['ns']-events[0]['ns']) if len(events)>1 else None}

fatal=[]
for path in [root/'loader-only/startup-logcat.txt',root/'bridge/startup-logcat.txt',preview/'pid-relevant-logcat.txt',preview/'crash-buffer.txt']:
    for line in read(path).splitlines():
        if re.search(r'FATAL EXCEPTION|Fatal signal|backtrace:|>>> com\.ss\.android\.ugc\.trill <<<',line):
            fatal.append({'file':str(path),'line':line})
errors=[r for r in active if r['kind'] in ('pipeline_error','decoder_error','preload_failed')]
before=ET.fromstring(read(preview/'ui-before.xml'))
after=ET.fromstring(read(preview/'ui-after.xml'))
live_button=lambda ui:any(n.get('text')=='Phát LIVE' and n.get('package')=='com.ss.android.ugc.trill' for n in ui.iter('node'))
loader=load(root/'loader-only/run.json')
budget=rows(root/'device-budget.jsonl')
total=sum(r['seconds'] for r in budget)
egl=[r for r in active if r['kind']=='egl_created']
surface=value(session,'original')
checks={
 'loader_only_gate_pass':loader['loaderGate']=='PASS',
 'module_apk_installed':read(root/'bridge/install.txt').strip()=='Success',
 'hook_installed_in_actual_target':'event=installed scope=com.ss.android.ugc.trill' in read(root/'bridge/startup-logcat.txt'),
 'actual_session_configuration_intercepted':len(egl)==1 and 'method=SessionConfiguration' in session['detail'] and 'size=1920x1080 ' in egl[0]['detail'],
 'actual_tiktok_prebroadcast_ui':live_button(before) and live_button(after),
 'actual_preview_pixels_A_B_A':all(o['expected']==o['observed'] for o in observations),
 'two_correct_completed_switches':len(commands)==len(completes)==len(requests)==2 and all(r['source'].endswith('/'+command['source']+'.mp4') for r,command in zip(completes,commands)),
 'one_session_and_egl_during_switches':not any(r['kind'] in ('session_redirect','session_observed','egl_created','egl_released') for r in window),
 'same_original_surface_on_switches':all(value(r,'surface')==surface for r in switches),
 'renderer_pts_strictly_monotonic':metrics(output)['output_pts_non_increasing']==0 and len(output)>100,
 'no_observed_java_native_pipeline_failure':not fatal and not errors,
 'device_budget_under_300_seconds':total<300,
 'no_more_than_five_switches':len(commands)<=5,
}
latency=[{'id':r['switchId'],'source':command['source'],'request_to_complete_ms':(r['ns']-req['ns'])/1e6} for r,req,command in zip(completes,requests,commands)]
functional_pass=all(checks.values())
cadence_pass=metrics(output)['maximum_output_gap_ms']<100 and metrics(window_output)['maximum_output_gap_ms']<100
summary={'result':('PASS' if cadence_pass else 'PARTIAL_PASS') if functional_pass else 'FAIL',
 'functional_goal_result':'PASS' if functional_pass else 'FAIL',
 'prior_phase3_gap_target_on_actual_app':{'target_maximum_gap_ms':100,'result':'PASS' if cadence_pass else 'FAIL',
                                      'note':'Threshold retained; not a claim of Phase 3 cadence quality in TikTok'},
 'scope':'Modified lab TikTok 47.4.3 on SM-G996U: pre-broadcast LIVE camera preview only',
 'loader_gate':'PASS' if checks['loader_only_gate_pass'] else 'FAIL',
 'camera_hook_gate':'PASS' if checks['actual_session_configuration_intercepted'] else 'FAIL',
 'actual_preview_injection_gate':'PASS' if checks['actual_preview_pixels_A_B_A'] and checks['two_correct_completed_switches'] and checks['one_session_and_egl_during_switches'] else 'FAIL',
 'criteria':checks,'loader_only_pid':loader['observedPid'],'module_target_pid':load(preview/'run.json')['pid'],
 'session_generation_in_full_process_trace':generation,'original_surface':surface,
 'active_session_seconds':(closed['ns']-session['ns'])/1e9,'full_active_session_metrics':metrics(output),
 'controlled_switch_window_metrics':metrics(window_output),'switches':latency,'pixel_observations':observations,
 'device_seconds_conservative':total,'budget_stages':budget,'errors':errors,'fatal_lines':fatal,
 'limits':['No LIVE broadcast, recording, upload or receiver test','Three screenshots do not exclude transient black frames between samples',
           'Camera1, NDK, multi-output, orientation, other capture modes and future TikTok versions unverified',
           'HAL drain snapshot was CONFIGURED, zero produced frames, no in-flight request; capture-result synchronization unverified; no audio injection',
           'No endurance test; user performed login and navigation manually; agent never performed login/account operations'],
 'final_device_state':'Patched lab app remains installed; preview closed with HOME. Original APKs saved; account/application data not restored.'}
(root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('pixel_observations','budget_stages','limits')},indent=2))
raise SystemExit(0 if functional_pass else 1)
