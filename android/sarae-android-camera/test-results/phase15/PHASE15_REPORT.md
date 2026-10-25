# Phase 15 — Native Audio Bridge PoC

**Controlled PoC: PASS. TikTok native bridge readiness: NO-GO.** Actual LIVE PCM entry remains **UNKNOWN**. No LIVE was started, requested, or repeated. This phase does not establish TikTok native audio compatibility or resolve its historical LIVE freeze.

## Results

| Gate | Result | Evidence |
|---|---|---|
| Controlled OpenSL ES recorder | **PASS** | Three independent create → RECORDING → STOPPED → destroy lifecycles; 3,580 completed buffers; valid encoded outputs; zero enqueue failures/capture-ring drops |
| Native PCM replacement | **PASS, owned recorder only** | VIDEO soundtrack correlation 0.999884 against delivered PCM; original mic correlation only 0.005762; MIC snapshots exactly equal; MUTED all zero |
| Actual encoded output | **PASS** | Decode actual AAC/MP4 files: VIDEO matches source **0.999591**, MIC matches original captured mic **0.994437**, MUTED decodes to exact zeros |
| Loop continuity | **PASS in this short fixture** | Two source loop transitions (epochs 0→1→2), zero decoder missing frames and zero native replacement underruns |
| Output audio PTS | **PASS** | All three MP4s have 562 AAC packets with strictly increasing PTS; max step 21.334 ms |
| TikTok LIVE backend | **SUSPECTED OpenSL ES; not confirmed** | Reused Phase 12–14 evidence; multiple native routes remain possible |
| TikTok LIVE PCM entry / callback contract | **UNKNOWN** | No captured runtime callback registration, selected recorder object or application PCM contract |
| TikTok native bridge deployment / LIVE output | **NOT TESTED / NO-GO** | No native adapter deployed into TikTok; no broadcast |

Results refer to a quiet ambient microphone sample, not a prompted speech recording. Encoded-output waveform matching includes the measured AAC alignment delay. It is not a subjective listening or sample-perfect A/V claim.

## Implementation and scope

- `:native-audio-adapter`: independent Android library, ARM64 C++/JNI, owns its OpenSL recorder and its Android simple buffer queue. No symbol interception or third-party object discovery.
- `:native-audio-test`: independent package `co.sarae.camera.nativeaudiotest`; depends only on the adapter and existing `:audio-engine`. The activity is a test launcher/status display.
- Reuses `MediaAudioEngine`, `MediaExtractor`, `MediaCodec`, `PcmBufferWriter` and bounded loop banks. No second decoder implementation. A worker AAC encoder writes **audio-only MP4** files; there is no new video engine or video track in this harness.
- `PcmRing` has one evidence-backed correction described below. Existing Camera2, EGL, Video Engine, Java Audio Bridge and installed TikTok module were not changed.
- Host tools: `scripts/phase15_device.py`, `scripts/analyze_phase15.py`. Bounded commands, persistent cumulative budget, fresh run identifiers, automated encoded waveform/PCM/PTS analysis.

Build PASS: JDK 21, SDK 34, Gradle 9.6 / AGP 9.4.1, NDK **28.2.13676358**, CMake **3.22.1**, `arm64-v8a`, offline. Only `:native-audio-test:assembleDebug` and required audio dependencies were built. Focused **PcmRingTest 13/13 PASS**, including new quantized-PTS retention and real-discontinuity reset cases; see `PcmRingTest.xml`.

## Controlled ownership and callback contract

Format is **48,000 Hz, stereo, signed PCM16 little-endian**. Two application-allocated OpenSL buffers, **480 frames / 960 samples / 1,920 bytes each**, are initially enqueued. Callback registration and initial enqueues must succeed before start.

1. OpenSL owns a queued buffer until its FIFO recording completion callback. Android's simple-queue callback provides queue/context, not a completed-buffer pointer; this implementation maintains the documented FIFO order of its own two buffers.
2. Callback copies original mic PCM into a local packet, substitutes decoded PCM for VIDEO or zeros for MUTED, and copies the delivered variant. MICROPHONE is unchanged.
3. It re-enqueues that same recorder buffer. Downstream processing uses a separate snapshot, never recorder memory after re-enqueue.
4. Packet publication is through a bounded SPSC ring; encoder, disk IO and JSONL are on the worker. PCM decoding/conversion/prefill is on a separate feeder.

Callback performs no allocation, JNI, synchronous logging, file IO, decoder/encoder call, mutex or wait. Its required `Enqueue` API call remains subject to platform implementation scheduling. Atomics used here are compile-time lock-free on ARM64. Callback timing reported below measures entry through re-enqueue, before publication/tail atomics; it is not a worst-case real-time certification.

Native replacement capacity **32 blocks / 320 ms**, maintained at **8 blocks / 80 ms prefill**; completed-packet capacity **128 blocks / 1.28 s**. Full output ring drops the new packet and counts it; it does not overwrite a reader-owned slot. Empty replacement ring outputs zeros and counts missing frames. Memory is fixed (native PCM/packet arrays ~563 KB plus small metadata; existing decoder uses two bounded 500 ms banks). Mode is immutable per recorder; concurrent mode changes were not tested.

Recorder state observed as 3 (RECORDING), then 1 (STOPPED), callback count stable after stop, then destroy. Feeder is stopped/joined before native storage deletion. Each callback records sequence, chosen FIFO slot/address, timestamp, missing frames, queue depth and enqueue result. Both slot identities alternate consistently over every observed buffer. Pointer values may be signed because the device uses tagged ARM64 addresses; full bit identity is retained.

## Evidence-backed correction discovered during this PoC

The initial VIDEO MP4 did contain soundtrack fragments, but failed full waveform matching (**0.312982**). The engine feeder missed **411,646 frames among blocks actually captured**; the native adapter itself had no empty-input-ring event because missing decoded samples had already been queued as zeros.

Original AAC packet PTS deviate **−16 / −32 samples** from the cumulative 1,024-sample decode clock. Example: packet PTS 234.667 → 255.667 ms advances 21.000 ms although decoded AAC contains 21.333 ms of samples. The stream time base is **1/48000**, so this is packet-timeline rounding already in the media, not proof of a 1/1000 container time base. See `source-pts.json` and initial `pcm_write` rows: buffered frames collapse from 12,288 to 1,024 when the difference exceeds the old two-sample tolerance.

`PcmRing.write` now snaps a new packet to the contiguous decoded end only when discrepancy is **≤1 ms** (48 frames at 48 kHz). Larger discontinuities still clear the window. This avoids discarding valid predecoded history for the observed bounded rounding. The fixture and AAC payload were **unchanged** between original and corrected tests; no re-encoding or source substitution was used to obtain PASS.

After correction: zero engine missing frames, soundtrack→PCM correlation **0.999884**, soundtrack→encoded MP4 **0.999591**. This is verified in the independent harness. **This source change is not deployed in TikTok's Phase 14 cache.** The policy deliberately treats sub-millisecond discrepancies as rounding; true intentional discontinuities of that size are not distinguished. Other timelines/resolutions/rates and long-term clock drift remain unverified.

Two instrumentation problems were also corrected: native CLOCK_MONOTONIC differed from Java elapsedRealtime; both now use **CLOCK_BOOTTIME**. Collection originally saw a stale DONE marker and stopped the first correction attempt prematurely; that attempt is not PASS evidence. Unique output directories/run IDs prevent this ambiguity. Original/aborted files and state are preserved separately.

## Measurements — final run `run-082638`, PID 22666

Three recording windows requested 12 seconds each. Hardware startup means delivered PCM spans 11.93–11.94 seconds; AAC priming/padding extends decoded output to 11.989333 seconds. These are different durations.

| Measurement | MICROPHONE | VIDEO | MUTED |
|---|---:|---:|---:|
| Completed blocks | 1,194 | 1,193 | 1,193 |
| Maximum callback interval | 12.409 ms | 12.388 ms | 12.469 ms |
| Maximum callback work before handoff | 0.068 ms | 0.207 ms | 0.115 ms |
| Callback→worker receipt p99 | 2.151 ms | 2.428 ms | 2.262 ms |
| Callback→worker receipt maximum | 2.376 ms | **48.387 ms** | 16.374 ms |
| Start command→first callback | 67.613 ms | 79.484 ms | 74.358 ms |
| Native missing frames / capture drops / enqueue failures | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Exact-zero leading delivered PCM | 19.875 ms | **0 ms** | entire output |
| Exact-zero leading decoded MP4 | 44.042 ms | **39.625 ms** | entire output |

Measured AAC waveform lag is **2,048 frames / 42.667 ms** in MIC and VIDEO outputs. This is harness encode/container delay; no A/V synchronization with actual video was tested. VIDEO still has natural quiet source passages (longest −60 dBFS / 10 ms-window span 130 ms); zero missing-frame counters do not mean the soundtrack itself has no silence.

The 48.387 ms worker scheduling outlier was absorbed by the bounded capture queue; sequence continuity and encoded PTS remained intact. It was not a callback gap. No observed Java/native fatal error in the bounded PID log and crash buffer. Three `MPEG4Writer: Stop() called but track is not started or stopped` messages occur during release after explicit stop; finalized MP4s decode, EOS/release complete, and no exception is recorded. Do not turn this short observation into a stability guarantee.

## TikTok compatibility gap — prior evidence reused

| Property | Controlled PoC | Actual TikTok evidence / remaining gap |
|---|---|---|
| Recorder API | **CONFIRMED OpenSL ES**, created by this app | LIVE OpenSL **SUSPECTED**; native AudioRecord/AAudio not excluded |
| Capture entry | Known `RegisterCallback` and owned queue | Actual selected callback address/context/recorder **UNKNOWN** |
| PCM format | Confirmed app contract 48 kHz stereo S16 | Phase 12 AudioFlinger/HAL 48 kHz stereo S16; client queue layout still unknown |
| Buffer size / ownership | Two 480-frame blocks, own FIFO snapshots | HAL 960 frames and track 2,880 frames are **not** evidence of a client OpenSL buffer contract |
| Capture completion / encoder feed | Actual owned callback feeds local AAC encoder, output verified | No observed target PCM handoff to LIVE encoder |
| Recording mode | Own OpenSL recorder | Phase 11 actual TikTok saved recording used Java `AudioRecord.read(ByteBuffer,int)` at 44.1 kHz stereo; that different route already passed |
| Non-broadcast modes | Harness capture works | Phase 13/14 target preview and LIVE-preparation preview had **0 active RECORD thread**; cannot observe inactive native capture |

Phase 14 `liblyrax.so` SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`, Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`. Recorder imports/strings include `slCreateEngine`, `SL_IID_RECORD`, `SL_IID_ANDROIDSIMPLEBUFFERQUEUE`, and `RegisterCallback(...SimpleBufferQueueCallback,this)`. They establish a candidate implementation, not runtime backend selection. JNI `nativeSetRecordingCallback` is not automatically a PCM capture entry.

No repeated static scans or new TikTok mode tests were performed without new evidence. The non-LIVE paths already observed cannot establish this native LIVE contract, so that investigation stops here. See [Phase 13](../phase13/PHASE13_REPORT.md) and [Phase 14](../phase14/PHASE14_REPORT.md). Queue ownership assumptions follow the [Android OpenSL ES recorder/buffer-queue documentation](https://developer.android.com/ndk/guides/audio/opensl/opensl-for-android); they are not inferred from HAL buffer size.

### Remaining NO-GO blockers

Need actual target recorder/backend selection, registration address/context, PCM format/size and queue ownership/lifecycle, plus a verified target-local encoder handoff before a narrow native adapter can be deployed. Future target-specific implementation must scope to that recorder object, retain its original callback/context and pacing, and reuse existing PCM Engine. No global native hooks were implemented.

Actual LIVE encoder audio delivery, source A/B synchronization, microphone replacement at the receiver, and video-freeze resolution cannot be validated from this owned recorder test. No further broadcast is requested in this phase. Native PoC success is only one building block, not a GO for another LIVE or Sarae Live Studio integration.

## Preservation, timing and files

**Total active device verification: 89.047 seconds** = initial run 42.469 + prematurely stopped correction attempt 4.594 + final corrected run 41.984, including installation of the independent harness, waiting, log collection and cleanup. No Camera2 regression, endurance, or LIVE test. Builds/static/offline waveform analysis excluded. No more device testing after final cleanup.

Only the independent harness was installed/updated. Returned to HOME after its recorder stopped. TikTok was not running before/after, and was not launched, restarted, force-stopped, patched, installed, cleared or reloaded. Account/configuration/media untouched. Before/after:

- Cached Phase 14 module SHA256 **`53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`**.
- Target base APK SHA256 **`4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`**.
- **104 original Phase 12 evidence files hash-verified unchanged**.

Final encoded files are in `.tools/phase15/runs/run-082638/`:

| File | SHA256 |
|---|---|
| `VIDEO.mp4` | `f19e337b16a4e8aa72e5afc65a88563121283363efd86975ce093dd09f27563c` |
| `MICROPHONE.mp4` | `38e3764c88f30e20e2975d09864ec3056b3f56beec61c905f03bf9ed6eaf2758` |
| `MUTED.mp4` | `930f54331c233602aa2f886d2e2ee3ca7a6a007bc7955ccf1d9c2adb8cc171d7` |

Input fixture: first four seconds of the existing user video B `5baed9ab52bf4730a3db889c3707dd12.mp4`, stream copy without changing AAC content; fixture SHA256 `905e2337b1933ed2460508d68411cd1f113ac11e6f11d364d12524a22be3ce2b`. Input audio duration 4.010 s; controlled loop period 4.010 s. Coded tail padding is not treated as new media content. Harness APK SHA256 `512a6d2a97b80931b4d084b9dd2952a0585b44df3af7b5fc4081286519749167`.

Audit: `analysis.json`, `events.jsonl` (5,408 rows), `*-ffprobe.json`, `*-packets.json`, `PcmRingTest.xml`, `adb.jsonl`, `device-state.json`, source/target hashes, `source-sha256.json`. Raw original/delivered PCM, first-run artifacts, cancelled attempt and bounded platform diagnostics remain in ignored `.tools/phase15/`; no sensitive microphone binary is added to Git. Build logs are retained there. These local files must be copied deliberately if moving the checkout.
