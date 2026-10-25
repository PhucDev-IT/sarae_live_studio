"""Validate retained evidence and write Phase 13 gates/hashes without any device access."""
import hashlib
import json
from pathlib import Path

P=Path('test-results/phase13')
a=json.loads((P/'analysis.json').read_text());cases=a['cases'];encoder=a['local_encoder_checks']
assert cases['prebuilt-baseline']['submit_errors'] and cases['prebuilt-baseline']['capture_completed_last_count']==0
assert cases['prebuilt-corrected']['request_identity_preserved'] and not cases['prebuilt-corrected']['submit_errors']
assert cases['retired-close-baseline']['early_egl_release'] and cases['retired-close-baseline']['output_frames_after_retired_close']==0
assert not cases['retired-close-corrected']['early_egl_release'] and cases['retired-close-corrected']['output_frames_after_retired_close']>100
assert encoder['encoder-corrected']['encoded_packets']>encoder['encoder-baseline']['encoded_packets']
assert encoder['encoder-corrected']['packet_pts_nonincreasing']==0 and encoder['encoder-corrected']['max_packet_pts_gap_ms']<100
assert a['device_seconds']<300 and a['new_live_sessions']==0 and a['tiktok_cache_module_unchanged']
old=json.loads(Path('test-results/phase12/evidence-sha256.json').read_text())
for name,digest in old.items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest, 'Original Phase 12 evidence modified: '+name
assert (P/'tiktok-module-final.txt').read_text().split()[0]=='2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0'
assert 'Active Camera Clients:\n[]' in (P/'encoder-cleanup-camera.txt').read_text()
value={'phase':13,'overall':'PARTIAL_PASS','live_readiness':'NO_GO','new_live_sessions':0,
       'device_seconds_conservative':a['device_seconds'],
       'original_phase12_evidence_hashes_verified':len(old),
       'classifications':a['root_cause_classification'],
       'corrections':{'camera_close_ownership':'PASS_INDEPENDENT_CONSUMER_AND_ENCODER',
                      'prebuilt_request_mapping':'PASS_INDEPENDENT_CONSUMER',
                      'tiktok_candidate_deployment':'NOT_PERFORMED',
                      'native_audio_extension':'NOT_IMPLEMENTED_ENTRY_UNKNOWN'},
       'mandatory_go_gates':{'ownership_local_repro':'PASS','prebuilt_local_repro':'PASS',
                            'moving_mp4_local_avc_encoder':'PASS',
                            'tiktok_candidate_hash_load_and_lifecycle':'UNVERIFIED',
                            'tiktok_capture_metadata_submission':'UNVERIFIED',
                            'tiktok_hevc_encoder_configuration':'UNVERIFIED',
                            'native_pcm_capture_entry_and_buffer_ownership':'UNKNOWN',
                            'soundtrack_on_native_capture_output':'NOT_TESTED',
                            'capture_result_injected_timestamp_contract':'OPEN',
                            'native_av_ab_switch_and_sync':'NOT_TESTED'},
       'future_live_policy':'Do not request or initiate a future LIVE until all mandatory readiness gates pass; Phase 13 performs no LIVE.',
       'preservation':{'tiktok_package_reinstalled':False,'tiktok_cache_module_reloaded':False,
                       'tiktok_force_stopped':False,'account_data_cleared':False,'original_phase12_evidence_modified':False},
       'remaining_blockers':['Historical close caller identity/stack missing; stale camera/finalizer remains SUSPECTED.',
                             'Native LIVE capture entry and selected backend not confirmed; preview has no active RECORD thread.',
                             'CaptureResult timestamp/target callback contract is not paired with injected frames.',
                             'Local AVC output is not proof of TikTok HEVC encoding or network delivery.']}
(P/'results.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
artifacts=[Path('app/build/outputs/apk/debug/app-debug.apk'),Path('consumer-test/build/outputs/apk/debug/consumer-test-debug.apk'),
           Path('.tools/phase13/consumer-test-debug-487-lspatched.apk'),
           *Path('.tools/phase13').glob('*.mp4')]
(P/'artifact-hashes.json').write_text(json.dumps({str(p):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in artifacts},indent=2))
files=[p for p in P.rglob('*') if p.is_file() and p.name!='evidence-sha256.json']
files += [*Path('.tools').glob('phase13-*')]
files += [p for p in Path('.tools/phase13').rglob('*') if p.is_file()]
files += [Path('camera-bridge/src/main/java/co/sarae/camera/bridge/Camera2HookPrototype.kt'),
          Path('app/src/main/java/co/sarae/camera/poc/HookEntry.kt'),
          *Path('consumer-test/src/main/java/co/sarae/camera/consumer').glob('*.kt'),
          *Path('scripts').glob('*phase13*.py')]
manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
(P/'evidence-sha256.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps({'readiness':value['live_readiness'],'device_seconds':a['device_seconds'],
                  'phase12_original_hashes_verified':len(old),'phase13_hashes':len(manifest),'new_live_sessions':0},indent=2))
