"""Offline mechanism results, historical lifecycle correlation, and receiver pixel matching."""
import collections
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path
import numpy as np

OUT=Path('test-results/phase13');OLD=Path('test-results/phase12')
def rows(p):return [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l]
def field(text,key):
    m=re.search(r'\b'+key+r'=([^ ]+)',text);return m[1] if m else None
def camera_summary(p):
    text=p.read_text(encoding='utf-8',errors='replace')
    patterns={'produced':r'Frames produced: (\d+), last timestamp: (\d+) ns',
              'status':r'Device status: ([A-Z]+)', 'requests':r'Request ID counter: (\d+)',
              'consumer':r'Consumer name: ([^\n]+)'}
    return {k:re.findall(v,text)[:1] for k,v in patterns.items()}

cases={}
for name in ['prebuilt-baseline','prebuilt-corrected','retired-close-baseline','retired-close-corrected']:
    bridge=rows(OUT/name/'bridge.jsonl');consumer=rows(OUT/name/'consumer.jsonl')
    background=next(e['ns'] for e in consumer if e['kind']=='background')
    outputs=[e for e in bridge if e['kind']=='output_present' and e['ns']<background]
    captures=[e for e in consumer if e['kind']=='capture_completed' and e['ns']<background]
    trigger=next((e['ns'] for e in consumer if e['kind']=='retired_device_close_again'),None)
    released=[e for e in bridge if e['kind']=='egl_released' and e['ns']<background]
    sensor=int(field(captures[-1]['detail'],'sensorNs')) if captures else None
    value={'camera_snapshot':camera_summary(OUT/name/'camera.txt'),
           'output_frames':len(outputs),'receiver_texture_frames':sum(e['kind']=='frame' for e in consumer),
           'capture_completed_last_count':int(field(captures[-1]['detail'],'count')) if captures else 0,
           'submit_errors':[e for e in consumer if e['kind']=='request_submit_error'],
           'request_identity_preserved':all('requestSame=true' in e['detail'] for e in captures) if captures else None,
           'capture_failures':[e for e in consumer if e['kind'] in ['capture_failed','capture_buffer_lost']],
           'early_egl_release':released,'retired_close_ns':trigger,
           'output_frames_after_retired_close':sum(e['ns']>trigger for e in outputs) if trigger else None,
           'camera_close_events':[e for e in bridge if e['kind'] in ['camera_close_observed','camera_close_ignored','camera_close']],
           'request_conversion_events':[e for e in bridge if e['kind'].startswith('request_conver') or e['kind']=='request_submit_remap']}
    if trigger and released:value['close_to_egl_release_ms']=(released[0]['ns']-trigger)/1e6
    if sensor and outputs:
        nearest=min(outputs,key=lambda e:abs(e['ns']-captures[-1]['ns']))
        value['sensor_minus_nearest_injected_pts_ns']=sensor-nearest['outputPtsNs']
        value['callback_latency_from_sensor_ms']=(captures[-1]['ns']-sensor)/1e6
    cases[name]=value

old_bridge=rows(OLD/'frozen-bridge.jsonl');start=json.loads((OLD/'analysis.json').read_text())['runtime']['session_start_ns']
old=[e for e in old_bridge if e['ns']>=start]
close=next(e for e in old if e['kind']=='camera_close')
release=next(e for e in old if e['kind']=='egl_released')
last=next(e for e in reversed(old) if e['kind']=='output_present')
anchor_text=Path('.tools/phase12/private/live-A-pid-logcat.txt').read_text(encoding='utf-8',errors='replace')
anchor=re.search(r'10-24 (\d\d):(\d\d):(\d\d\.\d+).*?ns=(\d+) event=session_redirect method=SessionConfiguration original=8990532',anchor_text)
anchor_seconds=int(anchor[1])*3600+int(anchor[2])*60+float(anchor[3]);anchor_ns=int(anchor[4])
close_seconds=anchor_seconds+(close['ns']-anchor_ns)/1e9
final_camera=Path('.tools/phase12/private/final-camera.txt').read_text()
disconnect=re.search(r'10-24 (\d\d):(\d\d):(\d\d) : DISCONNECT device 1 client for package com.ss.android.ugc.trill \(PID 16744\)',final_camera)
disconnect_seconds=int(disconnect[1])*3600+int(disconnect[2])*60+int(disconnect[3])
historical={'phase12_camera_snapshot':camera_summary(Path('.tools/phase12/private/live-A-camera.txt')),
            'last_output_ns':last['ns'],'last_output_media_pts_ns':last['sourceTextureNs'],
            'close_ns':close['ns'],'egl_release_ns':release['ns'],
            'close_inferred_device_wall':f'{int(close_seconds//3600):02}:{int(close_seconds//60)%60:02}:{close_seconds%60:06.3f}',
            'inference_uses_same_pid_logcat_elapsed_anchor':True,
            'camera_service_disconnect_wall':f'{disconnect[1]}:{disconnect[2]}:{disconnect[3]}',
            'service_disconnect_after_hook_close_seconds':disconnect_seconds-close_seconds,
            'output_to_close_ms':(close['ns']-last['ns'])/1e6,
            'close_to_release_ms':(release['ns']-close['ns'])/1e6,
            'switch_commands_in_live':json.loads((OLD/'state.json').read_text())['switches'],
            'limitation':'Phase 12 close object identity/stack absent; lifecycle signature is consistent with reproduced retired-device close, not proof of that historical caller.'}
old_output=[e for e in old if e['kind']=='output_present']
historical['swap_timing_ms']={key:float(np.percentile([e['timingsNs']['swapNs']/1e6 for e in old_output],percentile)) for key,percentile in [('p50',50),('p95',95),('max',100)]}
historical['draw_timing_max_ms']=max(e['timingsNs']['drawNs']/1e6 for e in old_output)
historical['renderer_tick_to_present_max_ms']=max(e['timingsNs']['tickToPresentNs']/1e6 for e in old_output)

def ff(args):return subprocess.run(['D:/ffmpeg/bin/ffmpeg.exe','-v','error',*args],capture_output=True,timeout=90,check=True).stdout
source=Path('C:/Users/nguye/Downloads/c56c3dcfeb91499d970d953604771f88.mp4')
viewer=Path('C:/Users/nguye/Downloads/IMG_5061.MP4')
# Viewer content fills height: 540x960 -> 1080x1920, crop 100px horizontally.
# This maps viewer ROI (100,350,740,900) to source ROI (100,175,370,450).
reference=np.frombuffer(ff(['-ss','60','-i',str(source),'-t','50','-an','-vf','fps=30,crop=370:450:100:175,scale=37:45','-pix_fmt','gray','-f','rawvideo','-']),dtype=np.uint8).reshape(-1,45,37)
received=np.frombuffer(ff(['-i',str(viewer),'-an','-vf','fps=10,crop=740:900:100:350,scale=37:45','-pix_fmt','gray','-f','rawvideo','-']),dtype=np.uint8).reshape(-1,45,37)
def vectors(frames):
    v=frames.astype(np.float32).reshape(len(frames),-1);v-=v.mean(axis=1,keepdims=True)
    return v/np.maximum(np.linalg.norm(v,axis=1,keepdims=True),1e-12)
times=[8,10,15,16,17,17.8,18,20,22,24,30,35.5,40,60,90,107]
scores=vectors(received[[round(t*10) for t in times]])@vectors(reference).T
matches=[]
for t,score in zip(times,scores):
    k=int(np.argmax(score));matches.append({'viewer_seconds':t,'best_source_A_seconds':60+k/30,'correlation':float(score[k])})
budget_rows=rows(OUT/'device-budget.jsonl')
preview_launch=next(i for i,r in enumerate(budget_rows) if r.get('arguments',[])[:3]==['shell','am','start'])
first_preview=preview_launch-1  # Include preceding foreground safety check.
begin=datetime.datetime.fromisoformat(budget_rows[first_preview]['utc'])
last_preview=max(i for i,r in enumerate(budget_rows) if 'utc' in r)
end=datetime.datetime.fromisoformat(budget_rows[last_preview]['utc'])+datetime.timedelta(seconds=budget_rows[last_preview]['seconds'])
before_preview=sum(r['seconds'] for r in budget_rows[:first_preview])
after_preview=sum(r['seconds'] for r in budget_rows[last_preview+1:])
timing={'command_and_harness_active_seconds':sum(r['seconds'] for r in budget_rows),
        'before_preview_seconds':before_preview,'continuous_preview_stage_seconds':(end-begin).total_seconds(),
        'after_preview_encoder_seconds':after_preview,
        'conservative_total_seconds':before_preview+(end-begin).total_seconds()+after_preview,
        'preview_span_includes_navigation_wait_reasoning_and_cleanup':True,'maximum_seconds':300}
assert timing['conservative_total_seconds']<300
(OUT/'device-timing.json').write_text(json.dumps(timing,indent=2))
native=json.loads((OUT/'native-static.json').read_text())
lyrax=next(r for r in native['candidates'] if r['entry'].endswith('/liblyrax.so'))
preview_audio=Path('.tools/phase13-preview-audioflinger-settled.txt').read_text()
offset=float(np.median([m['best_source_A_seconds']-m['viewer_seconds'] for m in matches if m['viewer_seconds'] in [8,10,15]]))
historical['pre_freeze_source_minus_viewer_seconds']=offset
historical['predicted_last_renderer_frame_on_viewer_timeline_seconds']=last['sourceTextureNs']/1e9-offset
encoders={}
ref=np.frombuffer(ff(['-i','.tools/phase13/encoder-source.mp4','-an','-vf','fps=30,scale=54:96','-pix_fmt','gray','-f','rawvideo','-']),dtype=np.uint8).reshape(-1,96,54)
for name in ['encoder-baseline','encoder-corrected']:
    path=Path('.tools/phase13')/(name+'.mp4')
    probe=json.loads(subprocess.run(['D:/ffmpeg/bin/ffprobe.exe','-v','error','-show_streams','-show_format','-show_packets','-select_streams','v:0','-of','json',str(path)],capture_output=True,check=True,timeout=30).stdout)
    (OUT/(name+'-ffprobe.json')).write_text(json.dumps(probe,indent=2))
    packet_pts=[float(p['pts_time']) for p in probe['packets']]
    images=np.frombuffer(ff(['-i',str(path),'-an','-vf','fps=10,crop=270:480:185:0,scale=54:96','-pix_fmt','gray','-f','rawvideo','-']),dtype=np.uint8).reshape(-1,96,54)
    match=vectors(images)@vectors(ref).T
    correlations=np.max(match,axis=1)
    mad=np.mean(np.abs(np.diff(images.astype(np.float32),axis=0)),axis=(1,2))
    ce=rows(OUT/name/'consumer.jsonl');be=rows(OUT/name/'bridge.jsonl')
    trigger=next(e['ns'] for e in ce if e['kind']=='retired_device_close_again')
    packets=[e for e in ce if e['kind']=='encoded_packet']
    encoders[name]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'codec_start':next(e['detail'] for e in ce if e['kind']=='encoder_probe_started'),
        'summary':next(e['detail'] for e in ce if e['kind']=='encoder_probe_summary'),
        'duration_seconds':float(probe['format']['duration']),'encoded_packets':len(packet_pts),
        'packet_pts_nonincreasing':sum(y<=x for x,y in zip(packet_pts,packet_pts[1:])),
        'max_packet_pts_gap_ms':max((y-x)*1000 for x,y in zip(packet_pts,packet_pts[1:])),
        'source_frame_correlation_min':float(correlations.min()),'source_frame_correlation_median':float(np.median(correlations)),
        'sampled_subject_motion_mad_median':float(np.median(mad)),
        'encoded_packet_log_events_after_retired_close':sum(e['ns']>trigger for e in packets),
        'egl_outputs_after_retired_close':sum(e['kind']=='output_present' and e['ns']>trigger for e in be),
        'errors':[e for e in ce if e['kind']=='encoder_probe_error']}
value={'cases':cases,'phase12_lifecycle':historical,'receiver_source_pixel_matches':matches,
       'pixel_match_limitation':'Content alignment is estimated from crop geometry and 30fps source; compression and repeated source poses limit precision. Not a shared network clock.',
       'device_seconds':timing['conservative_total_seconds'],'device_timing':timing,
       'native_audio':{'apk_count_verified':native['apk_count'],'elf_count':native['elf_scanned'],
                       'liblyrax_sha256':lyrax['sha256'],
                       'installed_liblyrax_hash_matches':(OUT/'runtime-lyrax-hash.txt').read_text().startswith(lyrax['sha256']),
                       'liblyrax_mapped_in_preview':'/lib/arm64/liblyrax.so' in Path('.tools/phase13-preview-maps.txt').read_text(),
                       'preview_record_threads':len(re.findall(r'type 3 \(RECORD\)',preview_audio)),
                       'live_pcm_entry':'UNKNOWN','active_live_native_call_stack_available':False,
                       'backend':'SUSPECTED_OPENSL_ES; static liblyrax also contains AAudio and Java routes'},
       'new_live_sessions':0,'tiktok_cache_module_unchanged':(OUT/'tiktok-module-before.txt').read_text()==(OUT/'tiktok-module-after.txt').read_text(),
       'local_encoder_checks':encoders,
       'root_cause_classification':{'historical_live_video':'SUSPECTED','bridge_retired_close_mechanism':'CONFIRMED','bridge_prebuilt_request_failure':'CONFIRMED','native_live_backend':'SUSPECTED_OPENSL_ES','exact_native_pcm_entry':'UNKNOWN'}}
(OUT/'analysis.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
print(json.dumps({'cases':{k:{a:b for a,b in v.items() if a not in ['camera_close_events','request_conversion_events']} for k,v in cases.items()},'historical':historical,'matches':matches,'device_seconds':value['device_seconds']},indent=2))
