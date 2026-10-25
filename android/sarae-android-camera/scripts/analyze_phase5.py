"""Analyze independent-consumer evidence; never returns TikTok integration PASS."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

root = Path('test-results/phase5')

def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]

def number(row, key):
    return int(re.search(rf'\b{key}=(\d+)', row['detail'])[1])

results = {}
for method, expected in [('modern', 4), ('legacy', 1), ('shared', 0)]:
    directory = root / method
    bridge = rows(directory / 'bridge.jsonl')
    consumer = rows(directory / 'consumer.jsonl')
    frames = [r for r in consumer if r['kind'] == 'frame']
    output = [r for r in bridge if r['kind'] == 'output_present']
    complete = [r for r in bridge if r['kind'] == 'switch_complete' and (r.get('switchId') or 0) > 0]
    commands = rows(directory / 'switch-commands.jsonl') if expected else []
    switches = []
    for i, event in enumerate(complete):
        pattern = 'A' if event['source'].endswith('/A.mp4') else 'B'
        # Require three actual consumer pixel samples after commit, before the next commit.
        end = min(event['ns'] + 1_000_000_000, complete[i+1]['ns'] if i+1 < len(complete) else 2**63)
        observed = [r for r in frames if event['ns'] <= r['ns'] < end and f'pattern={pattern} ' in r['detail']]
        switches.append({'id': event['switchId'], 'source': pattern, 'matching_pixel_samples': len(observed),
                         'matches_command': i < len(commands) and commands[i]['source'] == pattern})
    timestamps = [number(r, 'timestampNs') for r in frames]
    regressions = sum(b <= a for a, b in zip(timestamps, timestamps[1:]))
    output_pts = [r['outputPtsNs'] for r in output]
    output_regressions = sum(b <= a for a, b in zip(output_pts, output_pts[1:]))
    black = sum('black=true' in r['detail'] for r in frames)
    unknown = sum('pattern=PHYSICAL_OR_UNKNOWN ' in r['detail'] for r in frames)
    gaps = [number(r, 'gapNs') for r in frames]
    errors = [r for r in bridge + consumer if r['kind'] in ('error', 'pipeline_error', 'decoder_error', 'preload_failed')]
    fatal = [line for line in (directory / 'relevant-logcat.txt').read_text(encoding='utf-8-sig').splitlines()
             if re.search(r'FATAL EXCEPTION|Fatal signal|backtrace:|\bF DEBUG\b', line)]
    egl = [r for r in bridge if r['kind'] == 'egl_created']
    criteria = {'one_consumer_session': sum(r['kind'] == 'session_started' for r in consumer) == 1,
                'no_pipeline_or_observed_fatal_crash': not errors and not fatal,
                'frames_observed': len(frames) >= 30}
    if expected:
        criteria.update({
            'correct_switches': len(commands) == len(switches) == expected and all(r['matching_pixel_samples'] >= 3 and r['matches_command'] for r in switches),
            'one_redirect': sum(r['kind'] == 'session_redirect' for r in bridge) == 1,
            'one_1920x1080_egl': len(egl) == 1 and 'size=1920x1080 ' in egl[0]['detail'],
            'clean_egl_release': sum(r['kind'] == 'egl_released' for r in bridge) == 1,
            'consumer_pts_monotonic': regressions == 0,
            'renderer_pts_monotonic': len(output) >= 30 and output_regressions == 0,
            'zero_black_and_unknown_frames': black == 0 and unknown == 0,
            'maximum_consumer_gap_under_100ms': max(gaps, default=10**9) < 100_000_000,
            'stable_consumer_surface': len(set(number(r, 'surface') for r in consumer if r['kind'] in ('session_request', 'session_started'))) == 1,
        })
        if method == 'modern':
            redirects = [r for r in bridge if r['kind'] == 'session_config_redirect']
            criteria['executor_callback_and_parameters_preserved'] = len(redirects) == 1 and all(
                f'{key}=true' in redirects[0]['detail'] for key in ('executorSame', 'callbackSame', 'parametersSame'))
    else:
        criteria.update({'unsupported_config_logged': sum(r['kind'] == 'session_unsupported' for r in bridge) == 1,
                         'no_redirect_or_video_producer': not egl and not output and not any(r['kind'] in ('session_redirect', 'target_redirect') for r in bridge)})
    results[method] = {'result': 'PASS' if all(criteria.values()) else 'FAIL', 'criteria': criteria,
                       'run': json.loads((directory / 'run.json').read_text(encoding='utf-8-sig')),
                       'frames': len(frames), 'switches': switches, 'black_frames': black, 'unknown_frames': unknown,
                       'consumer_timestamp_regressions': regressions, 'renderer_timestamp_regressions': output_regressions,
                       'maximum_gap_ms': max(gaps, default=0) / 1e6, 'errors': errors, 'fatal_lines': fatal}

budgets = [json.loads(p.read_text(encoding='utf-8-sig')) for p in root.glob('*device-budget.json')]
device_seconds = sum(b['elapsedSeconds'] for b in budgets)
criteria = {'independent_consumer_tests': all(r['result'] == 'PASS' for r in results.values()),
            'total_device_time_under_300s': device_seconds < 300,
            'each_foreground_run_under_60s': all(r['run']['durationSeconds'] < 60 for r in results.values()),
            'tiktok_package_metadata_unchanged': (root/'tiktok-before.txt').read_bytes() == (root/'tiktok-after.txt').read_bytes()}
summary = {'result': 'PASS' if all(criteria.values()) else 'FAIL', 'scope': 'Independent Camera2 bridge validation only',
           'criteria': criteria, 'tests': results, 'device_seconds_including_preflight_install_and_pulls': device_seconds,
           'foreground_seconds': sum(r['run']['durationSeconds'] for r in results.values()),
           'tiktok_integration': 'NOT VERIFIED / BLOCKED: no supported continuous camera input found; no instrumented TikTok test authorized',
           'endurance': 'NOT RUN; reuse Phase 1–4'}
(root/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')

inventory = {}
for path in [Path('.tools/lspatch-v1.2-487-debug.jar'), Path('.tools/phase4-static/base.apk'),
             Path('.tools/phase5-consumer/consumer-test-debug-487-lspatched.apk'),
             Path('app/build/outputs/apk/debug/app-debug.apk'), Path('consumer-test/build/outputs/apk/debug/consumer-test-debug.apk'),
             Path('camera-bridge/src/main/java/co/sarae/camera/bridge/Camera2HookPrototype.kt'),
             Path('consumer-test/src/main/java/co/sarae/camera/consumer/ConsumerActivity.kt')]:
    entry = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    if 'lspatched.apk' in path.name:
        with zipfile.ZipFile(path) as archive:
            entry['config'] = json.loads(archive.read('assets/lspatch/config.json'))
            # The known local LSPatch output intentionally links entries inside origin.apk.
            # Bound only this entry and verify its hash against our independently built input.
            origin = archive.getinfo('assets/lspatch/origin.apk')
            assert origin.file_size < 10_000_000
            origin._end_offset = None
            entry['embedded_origin_sha256'] = hashlib.sha256(archive.read(origin)).hexdigest()
            assert entry['embedded_origin_sha256'] == hashlib.sha256(Path('consumer-test/build/outputs/apk/debug/consumer-test-debug.apk').read_bytes()).hexdigest()
            assert entry['config']['sigBypassLevel'] == 0
            entry['embedded_modules'] = [name for name in archive.namelist() if name.startswith('assets/lspatch/modules/')]
    inventory[str(path)] = entry
(root/'artifact-inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
raise SystemExit(0 if all(criteria.values()) else 1)
