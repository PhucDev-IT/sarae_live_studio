"""Analyze real TikTok MP4 runs without installing or changing the app."""
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

root=Path('test-results/phase7')
def read(p): return p.read_text(encoding='utf-8-sig')
def load(p): return json.loads(read(p))
def rows(p): return [json.loads(s) for s in read(p).splitlines() if s.strip()]
def number(r,key): return int(re.search(rf'\b{key}=(\d+)',r['detail'])[1])
def stats(events):
    gaps=[number(r,'gapNs')/1e6 for r in events if number(r,'gapNs')>0]
    pts=[r['outputPtsNs'] for r in events]
    return {'frames':len(events),'maximum_gap_ms':max(gaps,default=0),
            'gaps_over_100ms':sum(g>100 for g in gaps),
            'output_pts_non_increasing':sum(b<=a for a,b in zip(pts,pts[1:])),
            # Phase subsets can be non-contiguous: do not divide their count by
            # first-to-last wall time, which includes the other phase's samples.
            'gap_weighted_fps':len(gaps)*1000/sum(gaps) if gaps and sum(gaps)>0 else None}

results={}
for name,db in [('trace-run','.tools/phase7/pipeline.sqlite'),('trace-complete','.tools/phase7/pipeline-complete.sqlite')]:
    directory=root/name
    trace=rows(next((directory/'jsonl').rglob('*.jsonl')))
    commands=rows(directory/'switch-commands.jsonl')
    controls=[r for r in trace if r['kind']=='switch'][-len(commands):]
    start=controls[0]['ns']
    session=next(r for r in reversed(trace) if r['kind']=='session_redirect' and r['ns']<start)
    end=next(r['ns'] for r in trace if r['kind']=='camera_close' and r['ns']>start)
    active=[r for r in trace if session['ns']<=r['ns']<end]
    (directory/'active-session.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in active),encoding='utf-8')
    output=[r for r in active if r['kind']=='output_present']
    requests=[r for r in active if r['kind']=='switch_request']
    preloads=[r for r in active if r['kind']=='preload_start' and (r.get('switchId') or 0)>0]
    completes=[r for r in active if r['kind']=='switch_complete' and (r.get('switchId') or 0)>0]
    # Include first output after commit: switching is request -> commit + 1 second.
    intervals=[(a['ns'],b['ns']+1_000_000_000) for a,b in zip(requests,completes)]
    switching=[r for r in output if any(r['ns']>=a and r['ns']-number(r,'gapNs')<=b for a,b in intervals)]
    normal=[r for r in output if r not in switching]
    switch_result=[]
    for command,request,preload,complete in zip(commands,requests,preloads,completes):
        displayed=[r for r in output if r.get('switchId')==complete['switchId']]
        switch_result.append({'index':command['index'],'source':command['source'],'id':complete['switchId'],
            'correct_source':complete['source'].endswith('/'+command['source']+'.mp4'),
            'slot_setup_boundary_ms':(preload['ns']-request['ns'])/1e6,
            'request_to_renderer_commit_ms':(complete['ns']-request['ns'])/1e6,
            'output_frames_for_selection':len(displayed),
            'unique_source_texture_timestamps':len({r.get('sourceTextureNs') for r in displayed if r.get('sourceTextureNs') is not None})})
    connection=sqlite3.connect(db);connection.row_factory=sqlite3.Row
    covered=connection.execute('select min(ts),max(ts+dur) from sched').fetchone()
    losses=[dict(r) for r in connection.execute("select name,value from stats where value>0 and (name like '%overrun%' or name like '%overwritten%' or name like '%dropped%' or name like '%data_loss%')")]
    pid=int(load(directory/'run.json')['pid'])
    egl_thread=connection.execute("select t.utid,t.tid,t.name from thread t join process p on p.upid=t.upid where p.pid=? and t.name like 'sarae-egl%'",(pid,)).fetchall()[-1]
    slice_sql="""select s.name,count(*) n,max(s.dur)/1e6 maximum_ms,avg(s.dur)/1e6 mean_ms
                 from slice s join track t on s.track_id=t.id join args a on a.arg_set_id=t.dimension_arg_set_id and a.key='utid'
                 where a.int_value=? and s.dur>=0 group by s.name order by max(s.dur) desc limit 15"""
    slice_summary=[dict(r) for r in connection.execute(slice_sql,(egl_thread['utid'],))]
    pause_sql="""select s.name,max(s.dur)/1e6 maximum_ms from slice s join track tr on tr.id=s.track_id
                 join args a on a.arg_set_id=tr.dimension_arg_set_id and a.key='utid'
                 join thread t on t.utid=a.int_value join process p on p.upid=t.upid
                 where p.pid=? and s.name like '%Suspend%' and s.dur>=0 group by s.name order by max(s.dur) desc limit 10"""
    pauses=[dict(r) for r in connection.execute(pause_sql,(pid,))]
    gap_evidence=[]
    for r in output:
        if number(r,'gapNs')<=100_000_000: continue
        i=active.index(r)
        gap_evidence.append({'output_ns':r['ns'],'gap_ms':number(r,'gapNs')/1e6,
            'system_trace_covers_interval':covered[0]<=r['ns']-number(r,'gapNs') and r['ns']<=covered[1],
            'context':active[max(0,i-20):i+3]})
    errors=[r for r in active if r['kind'] in ('pipeline_error','decoder_error','preload_failed')]
    checks={'five_complete_correct_switches':len(commands)==len(switch_result)==len(requests)==len(completes)==5 and all(r['correct_source'] and r['output_frames_for_selection']>=10 and r['unique_source_texture_timestamps']>=5 for r in switch_result),
            'one_camera_session_and_egl':sum(r['kind']=='session_redirect' for r in active)==1 and sum(r['kind']=='egl_created' for r in active)==1,
            'same_surface_at_each_switch':all(number(r,'surface')==number(session,'original') for r in controls),
            'strictly_monotonic_renderer_pts':stats(output)['output_pts_non_increasing']==0,
            'no_pipeline_failure':not errors,
            'output_gap_under_100ms':stats(output)['maximum_gap_ms']<100}
    result={'result':'PASS' if all(checks.values()) else 'FAIL','criteria':checks,'session':session,
        'generation_in_full_pid_trace':sum(r['kind']=='session_redirect' and r['ns']<=session['ns'] for r in trace),
        'active_session_seconds':(end-session['ns'])/1e9,'egl_created':[r for r in active if r['kind']=='egl_created'],
        'all_output':stats(output),'normal_output':stats(normal),'switching_output':stats(switching),
        'switch_window_definition':'Any output interval overlapping request to renderer commit + 1 second; all other active-session outputs are normal',
        'switches':switch_result,'source_frame_events':sum(r['kind']=='frame_rendered' for r in active),
        'source_arrivals':sum(r['kind']=='source_arrival' for r in active),'source_coalesced':sum(r['kind']=='source_coalesced' for r in active),
        'loops':sum(r['kind']=='loop' for r in active),'last_output_counters':output[-1]['detail'],
        'lifecycle_cleanup':[r for r in trace if end<=r['ns']<=end+1_000_000_000 and r['kind'] in ('camera_close','egl_released','slot_released','decoder_stop')],
        'trace':{'file':str(directory/'pipeline.pftrace'),'sha256':hashlib.sha256((directory/'pipeline.pftrace').read_bytes()).hexdigest(),
                 'scheduling_coverage_ns':list(covered),'data_loss':losses,'egl_thread':dict(egl_thread),'egl_slices':slice_summary,'app_suspend_slices':pauses},
        'gaps_over_100ms':gap_evidence,'errors':errors}
    results[name]=result
    (directory/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

fatal=[]
for name in results:
    for p in [root/name/'relevant-logcat.txt',root/name/'crash-buffer.txt']:
        fatal.extend({'file':str(p),'line':line} for line in read(p).splitlines() if re.search(r'FATAL EXCEPTION|Fatal signal|>>> com\.ss\.android\.ugc\.trill <<<',line))
budget=rows(root/'device-budget.jsonl')
source_inventory={}
for label,path in [('A',Path('../../backend/data/media/0275d431bc3c4d2b91ed0c54d54f0b78.mp4')),('B',Path('../../backend/data/media/47ca398498084dcbb510b69adae93db2.mp4'))]:
    media=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],text=True))
    source_inventory[label]={'local_path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,
        'duration_seconds':float(media['format']['duration']),
        'video':[{k:s.get(k) for k in ['codec_name','width','height','avg_frame_rate','r_frame_rate','side_data_list']} for s in media['streams'] if s['codec_type']=='video'],
        'audio_track_present':any(s['codec_type']=='audio' for s in media['streams']),'audio_used_by_engine':False}
baseline=load(Path('test-results/phase6/artifact-inventory.json'))
unchanged={p:hashlib.sha256(Path(p).read_bytes()).hexdigest()==v['sha256'] for p,v in baseline.items() if p.endswith('.kt') or p=='app\\build\\outputs\\apk\\debug\\app-debug.apk'}
checks={'ten_renderer_switches':sum(len(r['switches']) for r in results.values())==10 and all(r['criteria']['five_complete_correct_switches'] for r in results.values()),
        'session_surface_continuity_each_run':all(r['criteria']['one_camera_session_and_egl'] and r['criteria']['same_surface_at_each_switch'] for r in results.values()),
        'zero_output_pts_regressions':all(r['criteria']['strictly_monotonic_renderer_pts'] for r in results.values()),
        'no_pipeline_or_observed_fatal_crash':not fatal and all(r['criteria']['no_pipeline_failure'] for r in results.values()),
        'total_device_time_under_300s':sum(r['seconds'] for r in budget)<300,
        'apk_package_metadata_unchanged':all(read(root/'package-before.txt')==read(root/name/'package-after.txt') for name in results),
        'module_and_engine_unchanged':all(unchanged.values()),
        'all_output_gaps_under_100ms':all(r['criteria']['output_gap_under_100ms'] for r in results.values())}
summary={'result':'PASS' if all(checks.values()) else 'FAIL','criteria':checks,
    'normal_maximum_gap_ms':max(r['normal_output']['maximum_gap_ms'] for r in results.values()),
    'switching_maximum_gap_ms':max(r['switching_output']['maximum_gap_ms'] for r in results.values()),
    'successful_runs':{name:{'result':r['result'],'outputs':r['all_output']['frames'],'max_gap_ms':r['all_output']['maximum_gap_ms'],'loops':r['loops']} for name,r in results.items()},
    'device_seconds':sum(r['seconds'] for r in budget),'budget_stages':budget,
    'sources':source_inventory,'unchanged_source_and_module_hashes':unchanged,'motion_evidence':load(root/'motion-evidence.json'),
    'root_cause':'UNRESOLVED: the 191.926 ms interval was overwritten in the 32 MiB system trace; the loss-free 128 MiB trace did not reproduce >100 ms gaps. Neither GC, GPU nor log I/O is established as the cause.',
    'fix':'NOT APPLIED: no established component-level cause; installed APK, module and EGL engine retained',
    'orientation':'FAIL: real portrait footage visibly rotated about 90 degrees in the TikTok preview; no transform fix applied',
    'fatal_lines':fatal,'limits':['Renderer timestamps and source event delivery are logged on the EGL thread; decoder worker emit timestamps are not preserved separately',
        '10 renderer source commits, before/after actual preview and motion samples; no per-switch/per-frame compositor capture',
        'One session per measured 5-switch run; preview reopened between runs for the bounded trace retry',
        'No LIVE broadcast, audio integration, account operation, APK reinstall or engine rebuild']}
(root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ('sources','budget_stages','motion_evidence','unchanged_source_and_module_hashes')},indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
