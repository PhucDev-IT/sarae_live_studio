# Phase 10 — Audio Engine & TikTok Audio Bridge

## Result: PARTIAL_PASS

Samsung SM-G996U, Android 14/API 34, non-root; TikTok lab `com.ss.android.ugc.trill` 47.4.3. Branch `feature/tools-android`. Recording and saving were performed by the user. No LIVE, publishing, login, account-setting change, uninstall or data clearing was performed by the agent.

| Gate | Result | Evidence / limit |
| --- | --- | --- |
| Build / PCM contracts | PASS | `build-ring-fix.txt`, `unit-test-summary.json`: 17/17 unit tests, zero failures. No consumer-test build task. |
| Audio decode / conversion | PASS with underruns | Actual `c2.android.aac.decoder`, PCM 44,100 Hz stereo S16. Two-second 48 kHz mono self-test waveform correlates 0.996134 with source A; 4,320/96,000 frames missing at startup. |
| Actual capture API | PASS | `audio_read_confirmed`: Java `AudioRecord.read(ByteBuffer,int)` called by `com.ss.android.vesdk.audio.TEAudioRecord.read`. Successful 7,104-byte reads = 1,776 stereo PCM16 frames. |
| Audio Bridge replacement | PASS for this recording path | `final-audio.jsonl`: VIDEO substitution, original count preserved; actual saved waveform matches MP4 A. No native AAudio/OpenSL hooks implemented. |
| MICROPHONE | PASS for sampled recording | User-recorded `2026-10-24-093237572.mp4`; passthrough reads, nonzero recorded PCM, RMS −40.191 dBFS, no strong A/B soundtrack match. Human voice content is not automatically classified. |
| VIDEO | PASS for source A recording | User-saved `2026-10-24-093633174.mp4`, 13.212 s decoded audio; A correlation 0.940832 / 0.997801 / 0.961409 across beginning/middle/end. Corresponding B correlations only about 0.105–0.112 magnitude. RMS −18.363 dBFS, peak −6.201 dBFS. |
| MUTED | PASS for saved output | `2026-10-24-093759473.mp4`, 6.641 s decoded audio: every S16 PCM sample is zero after AAC decode. −240 dBFS in analysis denotes numerical floor, not an acoustic noise measurement. |
| SYNC | PASS for coarse short-file alignment only | Independent audio waveform and video subject-frame matching: audio ahead 30.000 / 2.750 / 6.125 ms at recorded times 1.000 / 6.606 / 12.212 s. At least ±one-frame (~33 ms) uncertainty; not a lip-sync certification. |
| VIDEO SWITCH A/B | NOT TESTED | Zero A/B commands in this execution; device budget exhausted. Implementation follows actual renderer source commits, but that is not recorded-output evidence. Source B decode/conversion also remains unverified on device. |
| Gap-free audio / loops | FAIL | VIDEO capture logged 12,541 missing frames at 44,100 Hz (~284.376 ms), replaced by silence. Startup: 8,880 frames (~201.361 ms); loop boundary: 3,661 frames (~83.016 ms). |
| Existing <100 ms video cadence | FAIL remains | Output gap max 120.535 ms in PID 31961; one gap >=100 ms. Earlier probe PID 29363 max 119.770 ms. No threshold changed or video-engine fix attempted. |
| Crash observation | Limited PASS | Collected crash buffer empty, no fatal Java/native entry found in collected snapshots. Lifecycle exceptions are detailed below; no endurance coverage. |

The user reported hearing neither soundtrack nor microphone when playing the VIDEO clip. **The actual saved file has a strong, nonzero match to soundtrack A.** The cause of the listening discrepancy is unknown; playback volume/routing was not inspected. No speculative playback-setting fix was applied.

## Confirmed API and configuration

Observed recording configuration: rate 44,100 Hz, two channels, encoding `AudioFormat.ENCODING_PCM_16BIT` (2), source MIC (1), native buffer 2,664 frames; public read result 7,104 bytes. `AudioFlinger` corroborates a 44.1 kHz stereo PCM16 record thread with a 3,552-byte HAL buffer. Java stack identifies TEAudioRecord; this is direct evidence for the tested recording mode.

Older SDK startup logs mention OpenSL ES and AAudio configuration. Those entries did not establish the actual recording read path and were not used to justify additional hook implementations. Other TikTok modes, devices, LIVE and native capture paths remain unknown.

The bridge always invokes the original AudioRecord read for pacing and native capture bookkeeping. Negative/zero results and exceptions pass through unchanged. Replacement writes only the returned PCM region, retains result count and array offsets, and leaves direct ByteBuffer position/limit unchanged. Direct buffers are written from index zero, following the documented AudioRecord contract. MICROPHONE mode leaves PCM untouched; MUTED writes PCM silence. MIXED is an interface reservation and is rejected at runtime.

References: [Android AudioRecord](https://developer.android.com/reference/android/media/AudioRecord), [MediaCodec](https://developer.android.com/reference/android/media/MediaCodec), [PCM encoding metadata](https://developer.android.com/reference/android/media/MediaFormat#KEY_PCM_ENCODING).

## Implementation

- `:audio-engine`: MediaExtractor audio-track selection, MediaCodec buffer decode, bounded 500 ms PCM ring per deck, maximum two decks, linear sample-rate conversion and mono/stereo mapping. Unsupported output formats fail explicitly. Linear resampling has no anti-alias filter; high-quality downsampling is not claimed.
- `VideoAudioClock`: separate source media time and monotonic device time. Repeated video frames do not restart the clock. Source changes/loop resets create new audio cursor generations. Synchronization uses the existing renderer's actual output events, not switch-request time.
- `:audio-bridge`: Java AudioRecord hook, default MICROPHONE, debug-lab scoped mode control, bounded asynchronous JSONL diagnostics. No AudioTrack/speaker playback or microphone re-recording workaround.
- `HookEntry`: composes audio with existing CameraFrameProvider events (`egl_created`, `preload_start`, `output_present`, `egl_released`). Existing StableFrameProvider, EglOutput and Camera2HookPrototype source hashes are unchanged (`video-source-integrity.json`). No second video decoder or new UI.
- Test helper `AUDIO_SELF_TEST` exercises the same audio decoder/PCM implementation without speaker playback. Its output is an isolated decoder gate, not a TikTok recording gate.

### Established ring-buffer defect and correction

Initial device self-test returned 87,686 missing frames out of 96,000 (~91.34%). Logs showed each decoder write retaining only its newest 2,048-frame block. `PcmRing.write()` incorrectly advanced the retained `first` index to the current block on every contiguous write, discarding earlier blocks. Removing that advancement retains prior contiguous PCM until actual consumption/overflow. A targeted two-block continuity regression test was added.

After correction, the device self-test had 4,320 missing frames (~90 ms, 4.5%) and waveform correlation 0.996134 to A. Decoder format negotiation, HE-AAC preroll and seeking are observable around startup. Remaining capture underruns coincide with the first read seeking from an initially prepared ~250 ms window to current video time (~2.4 s), and with a source-PTS loop reset requiring seek/flush. These events explain unavailable PCM at those boundaries; exact contribution of codec priming versus scheduling is not isolated. No unproven smooth-loop or preload fix was deployed.

### Other observed errors

`runtime-analysis.json` retains original `AudioRecord.stop()` IllegalStateException events after an uninitialized/released record was stopped again. They were observed by after-hooks; they are not evidence of a fatal process crash. A caught `EGL_BAD_SURFACE` (12301) occurred in the earlier probe PID as the user changed recording/editing lifecycle. The existing camera renderer was not rewritten. Successful reads and foreground output continued in the final PID 31961.

## Installed artifacts and preservation

Both audio updates used the existing lab signer and inherited all 60 installed splits via `pm install-create -r -p com.ss.android.ugc.trill`, replacing base only. No uninstall or clear-data command. `firstInstallTime` remained `2026-10-23 22:41:09`; final update time `2026-10-24 09:34:38`. User recording/save screens remained accessible.

| Artifact | SHA-256 |
| --- | --- |
| Pinned loader v1.2/487 debug | `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153` |
| Final embedded module | `4e59099e01efe6837773d78ff244554ebacb3edc70c2b9f66534e3c2bcf0bb22` |
| Final patched lab base | `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b` |
| MICROPHONE saved clip | `785e911c9cc8d59a230a5a0df790aaa1f1acae196da76a39dd259c8032f490e6` |
| VIDEO saved clip | `87404699bba3d9baa3475274abb4d95b015c992fb4354d804823abef1d5bf29a` |
| MUTED saved clip | `7c78dc6bcf08f26afc0440ccc285cafa2061547b01b2288150278d8e77d3643f` |

Signer certificate SHA-256 `8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b`. Loader configuration `sigBypassLevel=0`, no integrity/security hooks. See `ring-fixed-update-static.json`, signing/badging and install logs. Final `audio_mode` acknowledgment is **MICROPHONE**, PID 31961, ns `9423281260738077`; no rollback was performed.

## Timing and test boundary

Original execution accounted **299.428 seconds**, including human recording/waiting and cleanup attempt; build/static analysis excluded. The host budget guard prevented the final collection command before it reached ADB. At that point MUTED was still selected, and saved VIDEO/MUTED files were not yet on the host.

The user then explicitly authorized **up to 30 additional seconds solely for saved-file collection and MICROPHONE restoration**. Those collection scripts took **2.640 seconds cumulatively**, including one failed MediaStore command caused by missing shell quoting. That failure attempted no successful write; the quoted read query then succeeded. No new recording, switch test or playback test occurred in the extra allowance. Total accounted device execution/collection: **302.068 seconds**. Manual recording completed, MICROPHONE restoration acknowledged, no further ADB planned.

The harness now reserves 35 seconds for collection before the hard deadline. That harness change is static-only and was not used to retrospectively alter measured time.

## Evidence and reproduction

- `final-audio.jsonl`, `final-bridge.jsonl`, `mic-complete-audio.jsonl`, `runtime-analysis.json`: API stacks, rate/format, lifecycle, PCM writes/reads, underruns, source resets and EGL output events.
- `recorded-video-audio-analysis.json`, `recorded-muted-audio-analysis.json`, `microphone-probe-audio-analysis.json`: actual saved-output waveform evidence and hashes.
- `recorded-sync-analysis.json`: independent recorded-file content alignment; three samples, not continuous jitter coverage.
- Original saved MP4s and decoded PCM reside in `.tools/phase10/`; no source media replacement. Account-related logcat/package snapshots are retained locally; do not publish raw private logs.
- Offline analysis: `analyze_phase10_audio.py` (NumPy + ffmpeg), `analyze_phase10_sync.py`, `analyze_phase10_logs.py`. PCM contract/ring tests: `:audio-engine:testDebugUnitTest`.

## Remaining blockers / next implementation step

1. Address measured startup/loop underruns with audio preparation aligned to the current video media position and a prepared next loop. Keep the same engine; demonstrate availability before deploying a change.
2. After a newly authorized short verification window, record A→B→A and establish soundtrack source changes, session continuity and sync directly in that saved file. Current source-change clock wiring is unverified for real A/B recording.
3. Resolve the user's silent-playback observation by checking playback routing/volume or replaying the actual saved file; do not infer a failed soundtrack from preview listening alone.
4. Preserve existing unresolved >100 ms video cadence and lifecycle limitations. No LIVE audio compatibility, all-consumer compatibility, sustained drift bound or production readiness is claimed.
