"""Evidence/gate finalization on the host; no ADB or app operations."""
import hashlib,json
from pathlib import Path
P=Path('test-results/phase14');T=Path('.tools/phase14')
a=json.loads((P/'analysis.json').read_text());s=json.loads((P/'state.json').read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
assert s['finished'] and s['live_sessions']==0 and s['spent']<300
assert sha(T/'module.apk')==s['candidate_hash']==(P/'cache-after.txt').read_text().split()[0]
assert sha(T/'rollback-module.apk')=='2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0'
assert a['base_hash_preserved'] and not a['camera_snapshots']['final']['active_clients_present']
old=json.loads(Path('test-results/phase12/evidence-sha256.json').read_text())
for name,digest in old.items():assert sha(Path(name))==digest,'Original Phase 12 evidence changed: '+name
sessions=a['sessions'];o=a['ownership']
preview_pass=all(x['remaps'] and x['submissions'] and x['last_sampled_final_result_count']>0 and x['output_frames']>0 for x in sessions)
owner_pass=o['frames_after_replay_before_owner_close']>0 and o['egl_releases_between_replay_and_owner_close']==0 and o['capture_count_after']>o['capture_count_before']
cadence_pass=all(x['max_output_gap_ms']<100 and x['pts_nonincreasing']==0 for x in sessions)
r={'phase':14,'overall':'PARTIAL_PASS','tiktok_camera2_preview_integration':'PASS' if preview_pass else 'FAIL',
   'target_ownership_replay':'PASS' if owner_pass else 'FAIL','target_preview_cadence':'PASS' if cadence_pass else 'FAIL',
   'native_audio_backend':'SUSPECTED_OPENSL_ES','native_pcm_entry':'UNKNOWN','native_audio_correction':'NOT_IMPLEMENTED',
   'actual_live_video_audio_delivery':'NOT_TESTED','live_readiness':'NO_GO','new_live_sessions':0,
   'device_seconds':s['spent'],'pid':s['pid'],'module_deployed_sha256':s['candidate_hash'],
   'deployments':['CameraDevice ownership guard','default-single-output request conversion remap for debuggable TikTok lab',
                  'request/capture/lifecycle diagnostics','bounded retired-device close replay diagnostic'],
   'source_only_changes':[],
   'not_implemented_or_deployed':['native Audio Bridge','LIVE encoder correction','timestamp/capture-result pairing correction'],
   'source_engines_unchanged':['StableFrameProvider/EGL','MediaCodec video decoder','PCM Engine','VideoAudioClock','Java Audio Bridge Phase 11'],
   'preservation':{'installed_base_hash_unchanged':True,'account_data_cleared':False,'apk_reinstalled_or_patched':False,
                   'force_stop_only_for_idle_module_update':True,'rollback_phase11_preserved':True,
                   'phase12_original_hashes_verified':len(old)},
   'remaining_go_blockers':['Exact native capture entry and client buffer ownership are unknown outside broadcasting.',
                            'No native soundtrack output evidence; Java recording results do not prove native LIVE audio.',
                            'CaptureResult/injected PTS pairing, onCaptureBufferLost target handling, and multi-output are unverified.',
                            'HEVC media graph and independent LIVE receiver video/audio remain unverified.']}
(P/'results.json').write_text(json.dumps(r,indent=2),encoding='utf-8')
events=[json.loads(l) for l in (P/'final-bridge.jsonl').read_text().splitlines()]
selected=[e for e in events if e['kind'] in ['attached','session_redirect','capture_submit','request_submit_remap','capture_submit_result',
          'capture_observer_error','retired_close_replay','camera_close_ignored','camera_close','egl_created','egl_released']]
(P/'runtime-excerpts.json').write_text(json.dumps(selected,indent=2),encoding='utf-8')
manifest={str(f):sha(f) for root in [P,T] for f in root.rglob('*') if f.is_file() and f.name!='evidence-sha256.json'}
for f in [Path('camera-bridge/src/main/java/co/sarae/camera/bridge/Camera2HookPrototype.kt'),Path('app/src/main/java/co/sarae/camera/poc/HookEntry.kt'),*Path('scripts').glob('*phase14*.py')]:manifest[str(f)]=sha(f)
(P/'evidence-sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'camera2':r['tiktok_camera2_preview_integration'],'ownership':r['target_ownership_replay'],
                  'cadence':r['target_preview_cadence'],'device_seconds':s['spent'],'readiness':r['live_readiness'],
                  'phase12_hashes_verified':len(old),'phase14_evidence_hashes':len(manifest)},indent=2))
