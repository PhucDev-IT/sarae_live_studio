"""Offline Phase 8 gates. Renderer cadence is distinct from compositor pixel proof."""
import json,re,hashlib,sqlite3,sys,argparse
from pathlib import Path
parser=argparse.ArgumentParser(); parser.add_argument('--run',default='auto-run'); args=parser.parse_args()
root=Path('test-results/phase8'); run=root/args.run
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def number(r,key):
    m=re.search(r'\b'+re.escape(key)+r'=([\d.-]+)',r.get('detail',''))
    return float(m[1]) if m else 0
record=load(run/'run.json'); pid=int(record['pid'])
trace=rows(run/'jsonl'/f'bridge-{pid}.jsonl')
requests=[r for r in trace if r['kind']=='switch_request'][-5:]
assert len(requests)==5,'Incomplete switch run'
session=[r for r in trace if r['kind']=='session_redirect' and r['ns']<requests[0]['ns']][-1]
end=next(r['ns'] for r in trace if r['kind']=='camera_close' and r['ns']>requests[-1]['ns'])
active=[r for r in trace if session['ns']<=r['ns']<end]
(run/'active-session.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in active),encoding='utf-8')
out=[r for r in active if r['kind']=='output_present']
commits=[r for r in active if r['kind']=='switch_complete' and r.get('switchId',0)>0]
commands=rows(run/'switch-commands.jsonl')
windows=[(a['ns'],b['ns']+1_000_000_000) for a,b in zip(requests,commits)]
switching=[r for r in out if any(r['ns']>=a and r['ns']-number(r,'gapNs')<=b for a,b in windows)]
normal=[r for r in out if r not in switching]
def stats(data):
    gaps=[number(r,'gapNs')/1e6 for r in data if number(r,'gapNs')>0]
    pts=[r['outputPtsNs'] for r in data if 'outputPtsNs' in r]
    return {'outputs':len(data),'maximum_gap_ms':max(gaps,default=0),'gaps_over_100ms':sum(x>=100 for x in gaps),
            'gap_weighted_fps':len(gaps)*1000/sum(gaps) if gaps else None,
            'non_increasing_pts':sum(b<=a for a,b in zip(pts,pts[1:]))}
timing_fields=['tickLateNs','pendingLatchNs','activeLatchNs','drawNs','swapNs','tickToPresentNs','previousOutputLogNs']
timings={key:max((r.get('timingsNs',{}).get(key,0) for r in out),default=0)/1e6 for key in timing_fields}
logger=[r for r in active if r['kind']=='logger_timing']
logger_stats={key:max((r.get(key,0) for r in logger),default=0)/1e6 for key in ['serializeNs','writeNs','flushNs','logcatNs','totalNs']}
callback=max((r.get('timingsNs',{}).get('handlerQueueNs',0) for r in active),default=0)/1e6
worker=max((r.get('timingsNs',{}).get('workerDurationNs',0) for r in active),default=0)/1e6
switch_results=[]
for command,request,commit in zip(commands,requests,commits):
    frames=[r for r in out if r.get('switchId')==commit['switchId']]
    geometry=[r for r in active if r['kind']=='geometry_applied' and r.get('switchId')==commit['switchId']]
    switch_results.append({'source':command['source'],'id':commit['switchId'],
        'correct_source':commit['source'].endswith('/'+command['source']+'.mp4'),
        'latency_ms':(commit['ns']-request['ns'])/1e6,'outputs':len(frames),
        'source_texture_timestamps':len({r.get('sourceTextureNs') for r in frames}),
        'geometry':geometry})
gaps=[]
for r in out:
    if number(r,'gapNs')<100_000_000: continue
    i=active.index(r)
    gaps.append({'gap_ms':number(r,'gapNs')/1e6,'row':r,'context':active[max(0,i-15):i+3]})
errors=[r for r in active if r['kind'] in ('pipeline_error','decoder_error','preload_failed','configuration_error')]
metadata=[r for r in trace if session['ns']-20_000_000<=r['ns']<end and r['kind'] in ('camera_geometry','video_format','retriever_metadata','texture_transform','geometry_applied')]
native={}
db=Path('.tools/phase8')/('pipeline.sqlite' if args.run=='auto-run' else f'pipeline-{args.run}.sqlite')
if db.exists():
    cx=sqlite3.connect(db); cx.row_factory=sqlite3.Row
    coverage=list(cx.execute('select min(ts),max(ts+dur) from sched').fetchone())
    losses=[dict(r) for r in cx.execute("select name,value from stats where value>0 and (name like '%overrun%' or name like '%overwritten%' or name like '%dropped%' or name like '%data_loss%')")]
    slices=[dict(r) for r in cx.execute("""select s.name,count(*) count,max(s.dur)/1e6 max_ms
        from slice s join track tr on tr.id=s.track_id join args a on a.arg_set_id=tr.dimension_arg_set_id and a.key='utid'
        join thread t on t.utid=a.int_value join process p on p.upid=t.upid
        where p.pid=? and s.dur>=0 and (s.name like 'Sarae.%' or s.name like '%Suspend%') group by s.name order by max(s.dur) desc""",(pid,))]
    native={'coverage_ns':coverage,'data_loss':losses,'slices':slices}
    for g in gaps:
        r=g['row'];g['native_covers_gap']=coverage[0]<=r['ns']-number(r,'gapNs') and r['ns']<=coverage[1]
        g['native_covers_current_tick']=coverage[0]<=r['timingsNs']['tickStartNs'] and r['ns']<=coverage[1]
budgets=rows(root/'device-budget.jsonl'); resumed=rows(root/'device-budget-resume.jsonl')
total_before=sum(r['seconds'] for r in budgets); total_resume=sum(r['seconds'] for r in resumed)
fatal=[]
for p in [run/'crash-buffer.txt',run/'logcat.txt']:
    fatal.extend(line for line in p.read_text(encoding='utf-8-sig',errors='replace').splitlines() if re.search(r'FATAL EXCEPTION|Fatal signal|>>> com\.ss\.android\.ugc\.trill <<<',line))
criteria={'five_correct_renderer_switches':len(commits)==len(commands)==5 and all(s['correct_source'] and s['source_texture_timestamps']>=5 for s in switch_results),
          'one_camera_session':sum(r['kind']=='session_redirect' for r in active)==1,
          'one_egl_output':sum(r['kind']=='egl_created' for r in active)==1 and not any(r['kind']=='egl_released' for r in active),
          'no_output_pts_regressions':stats(out)['non_increasing_pts']==0,
          'cadence_under_100ms':stats(out)['maximum_gap_ms']<100,
          'no_observed_pipeline_or_fatal_error':not errors and not fatal,
          'no_diagnostic_readback_in_cadence_run':not any(r['kind']=='diagnostic_capture' for r in active),
          'automatic_geometry_no_override':all('rotationOverride=null' in r['detail'] and 'autoCameraOrientation=true' in r['detail'] and 'outputMirrorX=true' in r['detail'] for r in active if r['kind']=='geometry_applied'),
          'resumed_budget_under_300s':total_resume<=300}
controls=[r for r in active if r['kind']=='switch']
criteria['same_surface_at_switch']=len(controls)==5 and all(number(r,'surface')==number(session,'original') for r in controls)
result={'result':'PASS' if all(criteria.values()) else 'FAIL','criteria':criteria,'pid':pid,
        'session':session,'seconds_active_session':(end-session['ns'])/1e9,
        'all_output':stats(out),'normal_output':stats(normal),'switching_output':stats(switching),
        'switching_window':'request to commit + 1 second, including overlapping output intervals',
        'switches':switch_results,'timing_maxima_ms':timings,'logger_maxima_ms':logger_stats,
        'decoder_worker_max_ms':worker,'decoder_emit_to_handler_receipt_max_ms':callback,
        'source_arrivals':sum(r['kind']=='source_arrival' for r in active),'source_coalesced':sum(r['kind']=='source_coalesced' for r in active),
        'loops':sum(r['kind']=='loop' for r in active),'last_output_counters':out[-1]['detail'],
        'gaps':gaps,'errors':errors,'fatal':fatal,'metadata':metadata,'native':native,
        'budget':{'initial_execution_seconds':total_before,'initial_execution_exceeded_300s':total_before>300,
                  'resumed_authorized_execution_seconds':total_resume,'cumulative_device_seconds':total_before+total_resume,
                  'authorization':'User explicitly allowed testing to continue after lost internet; original overrun remains recorded'},
        'limits':['Renderer cadence is not end-to-end compositor cadence',
                  'Native trace begins after preview setup; no claim about an uncovered gap',
                  'Calibration readbacks and rejected/timeout attempts retained separately; no readback in the measured run',
                  'No LIVE broadcast/audio/uplink/receiver validation; rear camera and device rotations not device-tested']}
(run/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
(root/f'analysis-{args.run}.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ('metadata','gaps','switches')},indent=2))
raise SystemExit(0 if all(criteria.values()) else 1)
