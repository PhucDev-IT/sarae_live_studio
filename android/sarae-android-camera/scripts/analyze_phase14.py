"""Offline analysis of target preview evidence; does not contact a device."""
import collections,hashlib,json,re
from pathlib import Path
import numpy as np
from PIL import Image
P=Path('test-results/phase14');T=Path('.tools/phase14')
def rows(path):return [json.loads(l) for l in path.read_text().splitlines() if l]
def number(detail,key):
    m=re.search(r'\b'+key+r'=(-?\d+)',detail);return int(m[1]) if m else None
state=json.loads((P/'state.json').read_text());assert state['finished'] and state['spent']<300
events=rows(P/'final-bridge.jsonl');audio=rows(P/'final-audio.jsonl')
sessions=[e for e in events if e['kind']=='session_redirect'];result=[]
for i,s in enumerate(sessions):
    until=sessions[i+1]['ns'] if i+1<len(sessions) else events[-1]['ns']+1
    segment=[e for e in events if s['ns']<=e['ns']<until]
    out=[e for e in segment if e['kind']=='output_present']
    gap=[number(e['detail'],'gapNs')/1e6 for e in out]
    results=[e for e in segment if e['kind']=='capture_result_observed']
    starts=[e for e in segment if e['kind']=='capture_started_observed']
    result.append({'generation':number(s['detail'],'generation'),'redirect':s,
                   'egl_created':[e for e in segment if e['kind']=='egl_created'],
                   'egl_released':[e for e in segment if e['kind']=='egl_released'],
                   'output_frames':len(out),'output_span_seconds':(out[-1]['ns']-out[0]['ns'])/1e9 if out else None,
                   'max_output_gap_ms':max(gap,default=None),'gaps_ge_100ms':sum(x>=100 for x in gap),
                   'pts_nonincreasing':sum(b['outputPtsNs']<=a['outputPtsNs'] for a,b in zip(out,out[1:])),
                   'last_sampled_capture_start_count':number(starts[-1]['detail'],'count') if starts else 0,
                   'last_sampled_final_result_count':number(results[-1]['detail'],'count') if results else 0,
                   'submissions':[e for e in segment if e['kind']=='capture_submit_result'],
                   'remaps':[e for e in segment if e['kind']=='request_submit_remap']})
replay=next(e for e in events if e['kind']=='retired_close_replay')
generation=number(replay['detail'],'generation');start=next(s['ns'] for s in sessions if number(s['detail'],'generation')==generation)
owner_close=next(e for e in events if e['kind']=='camera_close' and e['ns']>replay['ns'])
post=[e for e in events if replay['ns']<e['ns']<owner_close['ns']]
ignored=next(e for e in post if e['kind']=='camera_close_ignored')
capture_before=[e for e in events if start<e['ns']<replay['ns'] and e['kind']=='capture_result_observed']
capture_after=[e for e in post if e['kind']=='capture_result_observed']
ownership={'replay':replay,'ignored':ignored,'true_owner_close':owner_close,
           'frames_after_replay_before_owner_close':sum(e['kind']=='output_present' for e in post),
           'egl_releases_between_replay_and_owner_close':sum(e['kind']=='egl_released' for e in post),
           'seconds_replay_to_real_owner_close':(owner_close['ns']-replay['ns'])/1e9,
           'capture_count_before':number(capture_before[-1]['detail'],'count'),
           'capture_count_after':number(capture_after[-1]['detail'],'count')}
native=[]
for label in ['live-one-runtime','after-retired','final']:
    text=(T/(label+'-audioflinger.txt')).read_text()
    native.append({'label':label,'record_threads':len(re.findall(r'type 3 \(RECORD\)',text))})
api_imports=[l.strip() for l in (T/'lyrax-dynsyms.txt').read_text().splitlines() if any(x in l for x in ['slCreateEngine','SL_IID_RECORD','SL_IID_ANDROIDSIMPLEBUFFERQUEUE','nativeSetRecordingCallback'])]
native_summary={'classification':'SUSPECTED_OPENSL_ES','exact_pcm_entry':'UNKNOWN',
                'liblyrax_sha256':hashlib.sha256((T/'liblyrax.so').read_bytes()).hexdigest(),
                'build_id':'800d53bec62ddf0a1774842f5a16e1693ab7f0de','imports_and_candidate_export':api_imports,
                'callback_export_address':'0x665704','callback_export_bytes':104,
                'actual_preview_capture':native,'java_capture_events':[e for e in audio if e['kind'] in ['audio_capture_start','audio_read_confirmed']],
                'audio_attachment':[e for e in audio if e['kind']=='audio_bridge_attached'],
                'native_audio_correction_deployed':False,
                'limitations':['Static RegisterCallback string identifies a candidate SimpleBufferQueueCallback, not its runtime address or live recorder ownership.',
                               'Exported nativeSetRecordingCallback is not established as the capture PCM entry; objdump nearest-symbol+offset labels are not actual callee names.',
                               'No active RECORD thread exists in the collected non-broadcast previews; actual LIVE native lifecycle/buffers cannot be measured here.']}
def camera_excerpt(label):
    text=(T/(label+'-camera.txt')).read_text()
    summary={key:re.findall(pattern,text)[:1] for key,pattern in {
        'status':r'Device status: ([A-Z]+)','request_counter':r'Request ID counter: (\d+)',
        'produced_and_last_timestamp':r'Frames produced: (\d+), last timestamp: (\d+) ns',
        'consumer':r'Consumer name: ([^\n]+)'}.items()}
    clients=text.split('Active Camera Clients:')[1].split('Allowed user IDs:')[0].strip()
    summary['active_clients_present']=clients!='[]'
    summary['device_dump_is_historical']=clients=='[]'
    return summary
images={}
for label in ['camera-two','after-retired']:
    im=np.asarray(Image.open(T/(label+'.png')).convert('RGB'))
    images[label]=im[500:1450,230:850].astype(np.float32)
pixel_mad=float(np.abs(images['after-retired']-images['camera-two']).mean())
value={'pid':state['pid'],'candidate_module_sha256':state['candidate_hash'],'device_seconds':state['spent'],
       'new_live_sessions':0,'sessions':result,'ownership':ownership,
       'camera_snapshots':{label:camera_excerpt(label) for label in ['live-one-runtime','after-retired','final']},
       'subject_roi_screenshot_change_mad_0_to_255':pixel_mad,
       'screenshot_limitation':'Two snapshots show source movement and upright subject; not a continuous black/freeze detector.',
       'camera_observer_errors':[e for e in events if e['kind']=='capture_observer_error'],
       'camera_errors':[e for e in events if e['kind']=='capture_device_error'],
       'pipeline_errors':[e for e in events if e['kind']=='pipeline_error'],
       'close_events':[e for e in events if e['kind'] in ['camera_close_observed','camera_close','camera_close_ignored']],
       'native_audio':native_summary,
       'base_hash_preserved':(P/'base-before.txt').read_text().split()[0]==(P/'base-after.txt').read_text().split()[0],
       'remaining_limits':['Framework/Binder final-result observations are not direct instrumentation of TikTok app onCaptureCompleted execution.',
                           'CaptureResult sensor timestamps are not paired with injected MP4 frame PTS.',
                           'onCaptureBufferLost target callback remapping and multi-output cases remain unverified.',
                           'No encoder output/receiver evidence exists for this non-broadcast target test.']}
(P/'analysis.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
print(json.dumps({'seconds':state['spent'],'sessions':[{k:v for k,v in r.items() if k not in ['redirect','egl_created','egl_released','submissions','remaps']} for r in result],
                  'ownership':{k:v for k,v in ownership.items() if k not in ['replay','ignored','true_owner_close']},
                  'native_capture':native,'pixel_mad':pixel_mad,'observer_errors':value['camera_observer_errors'],'pipeline_errors':value['pipeline_errors']},indent=2))
