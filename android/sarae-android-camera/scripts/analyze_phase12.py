"""Offline analysis only. Never connects to a device or changes a LIVE session."""
import collections
import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path('test-results/phase12')
FFMPEG = 'D:/ffmpeg/bin/ffmpeg.exe'
VIEWER = Path('C:/Users/nguye/Downloads/IMG_5061.MP4')
SOURCES = {
    'A': Path('C:/Users/nguye/Downloads/c56c3dcfeb91499d970d953604771f88.mp4'),
    'B': Path('C:/Users/nguye/Downloads/5baed9ab52bf4730a3db889c3707dd12.mp4'),
}


def ff(args):
    return subprocess.run([FFMPEG, '-v', 'error', *args], capture_output=True,
                          check=True, timeout=120).stdout


def rows(name):
    return [json.loads(line) for line in (ROOT / name).read_text(encoding='utf-8').splitlines() if line]


def runs(mask, fps):
    result = []
    start = None
    for i, active in enumerate([*mask, False]):
        if active and start is None:
            start = i
        if not active and start is not None:
            # Difference i compares frames i and i+1; interval has endpoint i/fps.
            if (i - start) / fps >= 1:
                result.append({'start_s': start / fps, 'end_s': i / fps,
                               'duration_s': (i - start) / fps})
            start = None
    return sorted(result, key=lambda r: r['duration_s'], reverse=True)


def audio(path):
    return np.frombuffer(ff(['-i', str(path), '-vn', '-ac', '1', '-ar', '4000',
                            '-f', 'f32le', '-']), dtype='<f4').astype(np.float64)


def correlation(signal, template):
    template = template - template.mean()
    n = len(template)
    size = 1 << (len(signal) + n - 2).bit_length()
    dot = np.fft.irfft(np.fft.rfft(signal, size) * np.fft.rfft(template[::-1], size), size)[n - 1:len(signal)]
    sums = np.r_[0, np.cumsum(signal)]
    squares = np.r_[0, np.cumsum(signal * signal)]
    norm = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    values = dot / np.sqrt(np.maximum(norm * float(template @ template), 1e-24))
    at = int(np.argmax(np.abs(values)))
    return {'correlation': float(values[at]), 'source_offset_s': at / 4000}


fps = 10
# Screen recording: fixed content ROI, excluding clock, chat, bottom controls and notifications.
video = np.frombuffer(ff(['-i', str(VIEWER), '-an', '-vf',
    'fps=10,crop=740:900:100:350,scale=148:180', '-pix_fmt', 'gray', '-f', 'rawvideo', '-']),
    dtype=np.uint8).reshape(-1, 180, 148)
mad = np.mean(np.abs(np.diff(video.astype(np.float32), axis=0)), axis=(1, 2))
motion = {'fps': fps, 'roi_original_pixels': [100, 350, 740, 900],
          'metric': 'adjacent-frame mean absolute grayscale difference (0..255)',
          'hold_threshold_sensitivity': {str(t): runs(mad < t, fps)[:8] for t in [.1, .25, .5, 1]},
          'per_5s_median_mad': [{'start_s': i / fps, 'median': float(np.median(mad[i:i+50])),
                               'maximum': float(np.max(mad[i:i+50]))} for i in range(0, len(mad), 50)]}
from PIL import Image, ImageDraw
times = [0, 10, 16, 17, 18, 20, 30, 60, 90, 107]
strip = Image.new('RGB', (148 * len(times), 205), 'white')
draw = ImageDraw.Draw(strip)
for j, t in enumerate(times):
    strip.paste(Image.fromarray(video[min(round(t * fps), len(video)-1)]).convert('RGB'), (j*148, 25))
    draw.text((j*148+4, 5), f'{t}s', fill='black')
strip.save(ROOT/'viewer-content-timeline.png')

recorded_audio = audio(VIEWER)
reference_audio = {k: audio(v) for k, v in SOURCES.items()}
audio_probes = []
for begin in [0, 10, 15, 20, 35, 60, 90, 100]:
    sample = recorded_audio[begin * 4000:(begin+5) * 4000]
    audio_probes.append({'viewer_start_s': begin, 'duration_s': len(sample)/4000,
                         'rms_dbfs': float(20*np.log10(max(np.sqrt(np.mean(sample**2)), 1e-12))),
                         'matches': {k: correlation(v, sample) for k, v in reference_audio.items()}})

state = json.loads((ROOT/'state.json').read_text())
bridge_all = rows('frozen-bridge.jsonl')
audio_all = rows('frozen-audio.jsonl')
bridge = [e for e in bridge_all if e['ns'] > state['preview_video_ns']]
aud = [e for e in audio_all if e['ns'] > state['preview_audio_ns']]
redirects = [e for e in bridge if e['kind']=='session_redirect']
session_start = redirects[-1]['ns']
active = [e for e in bridge if e['ns'] >= session_start]
out = [e for e in active if e['kind']=='output_present']
gaps = [int(re.search(r'gapNs=(\d+)', e['detail'])[1])/1e6 for e in out]
texture = [e.get('sourceTextureNs') for e in out]
texture_intervals = []
previous_change = out[0]['ns']
for previous, current in zip(out, out[1:]):
    if current.get('sourceTextureNs') != previous.get('sourceTextureNs'):
        texture_intervals.append((current['ns']-previous_change)/1e6)
        previous_change = current['ns']
summary = {
    'viewer': {'path': str(VIEWER), 'sha256': hashlib.sha256(VIEWER.read_bytes()).hexdigest(),
               'duration_s': json.loads((ROOT/'viewer-ffprobe.json').read_text())['format']['duration'],
               'motion': motion, 'audio_5s_waveform_probes': audio_probes},
    'agent_active_device_seconds': state['spent_seconds'],
    'live_session_count': state['session_count'], 'live_switch_commands': state['switches'],
    'preview_new_media_switch_commands': state['preview_switches_new_media'],
    'runtime': {
        'window': 'new SessionConfiguration near human LIVE start through last frozen snapshot; not receiver-synchronized',
        'session_start_ns': session_start,
        'bridge_counts_after_preview': dict(collections.Counter(e['kind'] for e in bridge)),
        'audio_counts_after_preview': dict(collections.Counter(e['kind'] for e in aud)),
        'camera_events': [e for e in bridge if e['kind'] in ['session_observed', 'session_redirect', 'camera_geometry', 'camera_close', 'egl_created', 'egl_released']],
        'source_commits': [e for e in aud if e['kind']=='audio_source_commit'],
        'output_frames': len(out), 'output_window_seconds': (out[-1]['ns']-out[0]['ns'])/1e9,
        'max_gap_ms': max(gaps), 'gaps_ge_100ms': sum(g>=100 for g in gaps),
        'output_pts_nonincreasing': sum(b['outputPtsNs']<=a['outputPtsNs'] for a,b in zip(out,out[1:])),
        'source_texture_first_ns': texture[0], 'source_texture_last_ns': texture[-1],
        'source_texture_changes': sum(b!=a for a,b in zip(texture,texture[1:])),
        'max_interval_between_texture_changes_ms': max(texture_intervals),
        'renderer_geometry': [e for e in active if e['kind']=='geometry_applied'],
        'audio_capture_starts': sum(e['kind']=='audio_capture_start' for e in aud),
        'audio_read_confirmed': sum(e['kind']=='audio_read_confirmed' for e in aud),
        'pcm_substituted': sum(e['kind']=='pcm_substituted' for e in aud),
        'pcm_underrun_events': [e for e in aud if e.get('missingFrames', 0)>0],
        'lifecycle_errors': [e for e in active+aud if 'error' in e['kind']],
        'live_pid': state['live_pid'], 'cleanup_pid': (ROOT/'final-pid.txt').read_text().strip(),
        'cleanup_audio_attachment': rows('mode-restored-audio.jsonl'),
        'crash_buffer_bytes': (ROOT/'crash-buffer.txt').stat().st_size,
    },
    'limitations': [
        'ROI hold detection uses 10fps grayscale samples; compression noise is handled with sensitivity thresholds, not exact frame hashes.',
        'Small waveform correlations cannot exclude transformed/mixed music by themselves; interpret alongside user observation and capture-path diagnostics.',
        'Renderer output logging does not prove downstream TikTok encoder consumption or receiver movement.',
        'No shared sender/receiver monotonic clock; network delay and actual LIVE wall duration cannot be derived from these logs.',
        'Cleanup current PID differs from LIVE PID; no process termination reason was retained. Agent did not restart TikTok.',
    ],
}
(ROOT/'analysis.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
results = {
    'phase': 12, 'overall': 'FAIL', 'device_seconds': state['spent_seconds'],
    'real_live_sessions': state['session_count'], 'additional_live_allowed': False,
    'gates': {
        'phase11_cached_module': 'PASS', 'preview_orientation_observed': 'PASS',
        'actual_live_started': 'CONFIRMED', 'receiver_video_continuity': 'FAIL',
        'receiver_mp4_soundtrack': 'FAIL', 'live_ab_video_audio_switch': 'NOT_TESTED',
        'live_av_sync': 'UNVERIFIED', 'local_egl_cadence_window': 'PASS',
        'whole_session_crash_free': 'UNVERIFIED',
    },
    'human_observations': {
        'microphone_only': True, 'sender_and_receiver_frozen': True,
        'first_freeze_reported_viewer_seconds': 17, 'user_manually_ended_live': True,
    },
    'live_switch_commands_sent': state['switches'],
    'pre_live_preview_switch_commands': state['preview_switches_new_media'],
    'blockers': [
        'Sustained frozen receiver pixels despite continuing EGL output; exact downstream fault not established.',
        'No Java AudioRecord replacement activity in LIVE; active native/system MIC capture is not intercepted by this bridge.',
        'LIVE A/B transitions and MP4 A/V synchronization not tested after freeze; single-session opportunity ended.',
    ],
    'cleanup': {
        'human_ended': True, 'agent_restarted_target': False, 'agent_reinstalled_target': False,
        'agent_cleared_target_data': False, 'mic_broadcast_sent_after_human_end': True,
        'current_pid_attachment_mode': 'MICROPHONE', 'fresh_mode_ack_observed': False,
        'current_pid_changed_since_live': True, 'process_exit_reason': 'UNKNOWN',
    },
}
(ROOT/'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
private = Path('.tools/phase12/private')
native_audio = (private/'live-A-audioflinger.txt').read_text(encoding='utf-8', errors='replace')
begin = native_audio.index('Input thread ', native_audio.index('name AudioIn_62E')-50)
end = native_audio.find('0 Effect Chains', begin)
final_camera = (private/'final-camera.txt').read_text(encoding='utf-8', errors='replace')
evidence = ('LIVE AudioFlinger (native/system capture observation; exact client API not proven):\n'
            + native_audio[begin:end+15] + '\nNative API clue (not proof of capture entry point):\n'
            + (ROOT/'live-A-audio-api-lines.txt').read_text() + '\nCleanup CameraService:\n'
            + final_camera[:final_camera.index('== Camera service events log')])
(ROOT/'runtime-evidence.txt').write_text(evidence, encoding='utf-8')
manifest = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(ROOT.iterdir())
            if p.is_file() and p.name != 'evidence-sha256.json'}
for p in [VIEWER, *SOURCES.values(), Path('scripts/analyze_phase12.py'),
          Path('scripts/phase12_live.py'), Path('scripts/prepare_phase12_sources.py'),
          *sorted(private.glob('*.txt'))]:
    manifest[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
(ROOT/'evidence-sha256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps({'motion_holds': motion['hold_threshold_sensitivity'], 'runtime': {k:v for k,v in summary['runtime'].items() if k not in ['camera_events', 'source_commits', 'pcm_underrun_events']},
                  'audio_probes': audio_probes}, indent=2))
