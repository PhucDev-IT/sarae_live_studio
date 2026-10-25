# Phase 23 — TikTok LIVE audio diagnosis

## Preflight — PASS

2026-10-25, Samsung SM-G996U / Android 14 / ARM64. The device was in the TikTok LIVE camera preview with source A visible upright; the screenshot showed the **Phát LIVE** button still available. Camera Bridge logs showed Camera2 capture results and fresh `output_present` frames from `A.mp4` at approximately 30 FPS. No A/B switch was issued.

| Identity/readiness check | Result |
| --- | --- |
| TikTok | `com.ss.android.ugc.trill` 47.4.3, PID 24699, debuggable |
| Installed base APK SHA256 | `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b` |
| Installed Phase 18 LSPatch module SHA256 | `66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120` |
| Mapped load-only observer SHA256 | `e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881` |
| Mapped `liblyrax.so` SHA256 | `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de` (matches the Phase 16 candidate binary) |
| simpleperf | Android binary present; package is debuggable, `run-as` works; Phase 21 `--app` call succeeded. One-second background profile's zero samples are not treated as a backend result. |
| A/B sources | Both present and readable in TikTok app storage |
| Free device storage | 37,789,212 KiB (about 36 GiB) |
| Viewer | User confirmed an independent viewer was recording screen and audio. Receiver observation is still pending. |
| Account/package/module | No changes; no LIVE started |

The native observer is load-only and reports no target callback interception. `liblyrax` being mapped in preview identifies the exact static candidate ELF but does not prove it is used for LIVE recording.

## Prepared single-session collection

After the user manually confirms that LIVE is active, run exactly one six-second profile:

```text
adb shell simpleperf record --app com.ss.android.ugc.trill -f 499 -e task-clock -g --duration 6 -o /data/local/tmp/sarae-phase23-perf.data
```

The prepared collector saves the perf file and bounded process/crash logs, AudioFlinger, AudioService, CameraService, current process mappings, and tails of the existing Phase 18/6/11 JSONL files. It has per-command timeouts and a 250-second total guard. It sends no UI actions and never restarts or modifies TikTok. A zero-sample profile will not be repeated.

## One LIVE profile — captured

The user confirmed the single LIVE session was active. One profile ran for **6.02564 s**, collecting **4,852 samples with 0 lost** at 499 Hz. The saved `perf.data` is 6,201,603 bytes, SHA256 `b6fa7294c1f567644127d95e8d00924ac7add29ce6c7821794fdfc197a966c20`. No A/B switch, UI action, restart or module change occurred. TikTok remained PID 24699. Active device diagnostics took **15.266 s**, below the five-minute limit.

### Native audio backend — OpenSL ES CONFIRMED active

The profile includes 194 samples on TikTok's `AudioRecord` thread TID 32523. Its sampled call graph follows:

```text
android::AudioRecord::AudioRecordThread::threadLoop()
  → android::AudioRecord::processAudioBuffer()
    → android::AudioRecordCallback::onMoreData(...)
      → libwilhelm.so: audioRecorder_handleMoreData_lockRecord(...)
        → liblyrax.so+0x6b9b08 → further liblyrax frames
```

`audioRecorder_handleMoreData_lockRecord` is the Android OpenSL ES simple-buffer-queue recorder callback path. The live stack entering that callback and TikTok `liblyrax` confirms OpenSL ES is active in the capture path during this session. A separate `rx_audio_enc` thread (TID 32524) had 122 samples in `liblyrax`'s native audio encode path. AudioFlinger independently reported an active TikTok recording: record ID **53071**, session **57153**, UID **11878**, PID **24699**, source **MIC**, 2-channel **48 kHz PCM16**, not silenced.

The static candidate at `liblyrax` RVA `0x6be020` was **not directly sampled**; this profile does not establish that exact callback's arguments or buffer ownership. The runtime `liblyrax.so` SHA matched Phase 16's candidate ELF, but the ELF has no `.symtab`; its private frames remain offsets. System `libwilhelm`/`libaudioclient` symbols resolved. AAudio symbols were not observed; a six-second profile cannot exclude another inactive or unsampled path. The confirmed result is an active OpenSL ES route, not proof that it is the only path.

The Phase 11 JSONL tail contained 240 `pcm_write` events for decoded A soundtrack PCM in Sarae's engine. It contained no Java `audio_capture_start`/`audio_read` events. This shows the decoder/ring is producing A PCM while TikTok's capture path is native OpenSL; it does not show that those PCM samples entered TikTok's recorder. The Phase 18 observer still installed no native hook and accessed no target buffers.

### Sender camera/EGL observations

The bounded bridge tail contains 61 `output_present` events spanning 1.9999 s, all source A. Maximum observed gap was **42.675 ms** (p99 **40.707 ms**); two Camera2 start/result pairs used device identity 119102128 and session generation **2**. No close/release event appeared in that tail. These observations support continuous sender-side output around collection; because the file tail was read after the profile, it is not a precisely profile-aligned six-second frame-gap measurement and does not prove remote delivery.

No Java fatal or native fatal signal appeared in the bounded process/crash logs. This is only the short collection window. The user reports the independent viewer heard **microphone audio only**, without the MP4 soundtrack. Treat receiver audio as **MIC (user-reported; viewer clip pending)**. The user has not yet confirmed whether the viewer saw motion or correct orientation, so viewer-side video remains **UNVERIFIED** pending the clip.

## Remaining audio gap and next step

The remaining PCM integration contract is the exact callback registration/dispatch used by the observed live OpenSL recorder, including callback identity, queue/context, PCM pointer/length, buffer ownership and when the queue permits replacement. AudioFlinger supplies the negotiated rate/channels/format but not that user-buffer contract. The existing Phase 20 foreign-library pin/lifetime blocker still prevents safe target interception. Do not infer PCM ownership from these samples.

**Recommended next implementation step:** use this confirmed OpenSL stack and the exact pinned ELF to identify the live registration target and its buffer contract, then resolve the Phase 20 foreign-library lifetime guard for that single callback before preparing any observer. Keep PCM replacement out until an observation-only target binding can prove the handoff safely.

The viewer recording is still needed to verify receiver motion/orientation and independently confirm the reported mic-only audio. Do not keep the broadcast open for more profiling; the single requested profile is complete.

Preflight artifact: `preflight-latest.json`; live capture: `capture-latest.json`; collection script: [phase23_live_diagnostics.py](../../scripts/phase23_live_diagnostics.py). Preview evidence: ignored `.tools/phase23-preview.png`. Perf data and raw snapshots are under ignored `.tools/phase23/live-20261025T022842Z/`; call graphs are `live-callgraph.txt`, `audio-record-thread.txt`, and `rx-audio-encoder-thread.txt`.
