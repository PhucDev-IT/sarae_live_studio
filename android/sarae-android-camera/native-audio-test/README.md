# Phase 15 controlled native recorder

Independent package: `co.sarae.camera.nativeaudiotest`. It does not load LSPatch,
look up TikTok objects, hook symbols, or operate the camera. Install only this
test APK. Do not embed this adapter into TikTok.

Build: JDK 21, SDK 34, NDK 28.2.13676358, CMake 3.22.1, ARM64:

```powershell
./gradlew.bat :native-audio-test:assembleDebug --offline
```

`scripts/phase15_device.py` is the bounded lab runner with a persistent device
budget and fresh per-run output directories. It requires HOME before starting,
checks the pinned TikTok module/base hashes, grants RECORD_AUDIO only to the
test app, supplies `source.mp4`, and records MICROPHONE, VIDEO, MUTED in order.
There are no controls for broadcasting or posting. Existing finished runs are
not automatically repeated.

For a manual run, push an MP4 with audio to this app's external-files
`source.mp4`, grant microphone permission, and launch `RecorderActivity` with
`seconds` (10–20, default 12) and a unique `runId` extra. Keep it foreground.
Leaving foreground cancels capture; cancelled outputs must not be marked PASS.
Results are in external-files `phase15/<runId>/`: AAC audio-only MP4s, original
microphone and delivered S16 files, JSONL, and DONE/ERROR markers. MP4s purposely
have no video track; this test does not create a second video engine or test A/V.

## Buffer contract

`native-audio-adapter` owns a single OpenSL engine/recorder, two pre-enqueued
480-frame stereo S16 buffers at 48 kHz (1,920 bytes each). Completion returns the
oldest queued buffer to the application according to Android's FIFO contract.
The adapter snapshots original microphone PCM, selects pass-through, decoded
PCM, or zeros, snapshots the delivered PCM, and re-enqueues the same buffer.
No consumer uses recording memory after re-enqueue. The downstream encoder
receives a separate fixed-size snapshot through an SPSC ring.

The callback has no allocation, JNI call, logger, file access, decoder or encoder
operation, mutex, or wait. Its sole OpenSL call re-enqueues the completed buffer.
Atomic counters are lock-free on the tested ARM64 ABI. Input ring: 32 blocks;
completed-packet ring: 128 blocks. Full capture ring drops the new snapshot and
increments a counter; it never overwrites a consumer-owned slot. Empty input
produces silence and increments missing frames. Both conditions must be reported.

MediaAudioEngine is reused unchanged apart from the separately tested bounded
PTS-rounding correction in PcmRing. Its two bounded PCM banks perform conversion
and loop preparation. A non-real-time feeder uses a controlled block timeline
and maintains an 80 ms native prefill. This timeline is not TikTok's media clock.
AAC encoding, muxing, logging, and packet draining happen on a worker. Callback
and Java event timestamps share CLOCK_BOOTTIME / elapsedRealtimeNanos.

Each native recorder is started, stopped, drained and destroyed. The worker
stops/joins its feeder before deleting native storage. Audio mode is immutable
for each recorder instance; in-flight mode switching and TikTok native adapter
registration are outside this PoC.

## Output verification

```powershell
python scripts/analyze_phase15.py
```

Requires NumPy and ffmpeg/ffprobe at the paths configured in the script. It
decodes actual MP4 audio, aligns measured AAC delay, correlates soundtrack and
microphone waveforms, checks exact MUTED zeros, sequence continuity, FIFO buffer
identities, callback intervals, enqueue failures, drops, and engine/native
underruns. Saved microphone in this test was quiet ambient input, not a prompted
speech test. TikTok's real LIVE PCM entry remains unknown regardless of results.
