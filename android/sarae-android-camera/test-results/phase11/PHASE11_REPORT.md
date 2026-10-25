# Phase 11 — Audio Continuity & A/B Switching

## Result: PARTIAL_PASS

Actual TikTok saved recording confirms **A→B→A with the matching MP4 soundtracks**, using one Camera2/EGL session throughout capture. Startup PCM availability improved; loop gaps were reduced. The test does **not** establish gap-free audio or exact sample-level switching. A source-B preparation timeout and repeated hard audio-clock corrections remain concrete blockers.

Branch `feature/tools-android`; Samsung SM-G996U, Android 14/API 34, non-root; TikTok lab `com.ss.android.ugc.trill` 47.4.3; pinned LSPatch 1.2/487. User manually recorded/stopped/saved. No LIVE or post operation was performed. TikTok APK and all installed splits were preserved; no install/uninstall/clear-data command.

## Acceptance gates

| Criterion | Result | Measured evidence |
| --- | --- | --- |
| Recorded A→B→A / matching soundtracks | **PASS** | Saved file `2026-10-24-100446478.mp4`; decoded pixels identify A 0–25.500 s, B 25.500–30.767 s, A 30.767–48.967 s. Each segment contains its expected soundtrack. |
| No audible wrong source after switch | **PARTIAL / not certified** | No sustained outgoing-source match in fully post-boundary 40 ms waveform windows. Incoming soundtrack correlations ~0.982 and ~0.980. Sub-frame transients and subjective audibility are not ruled out; no listening-only success claim. |
| Startup silence reduced | **PASS** | First capture read had no missing PCM. Phase 10 missing startup ~201.361 ms → 0 ms. Saved-file leading exact-zero PCM 149.583 ms → 0 ms at 48 kHz analysis. These are distinct measurements. |
| Loop silence reduced | **PASS for reduction; FAIL for zero-gap continuity** | Three A loops during capture: missing 0 / 433 / 588 frames = 0 / 9.819 / 13.333 ms at 44.1 kHz. Phase 10 loop ~83.016 ms. Heads ready 143.017 / 173.764 / 176.839 ms before video loop commits; no `audio_loop_not_ready` event. |
| Source B first PCM ready before video commit | **FAIL** | One 750 ms preparation timeout. Two reads each missed 959 frames (~21.746 ms), total ~43.492 ms. Actual source B soundtrack is subsequently present. |
| Source generation / commit ordering | **PASS in observed switches** | Audio generation changed on actual video output commits, not requests. First substituted new-source reads completed 28.333 / 30.097 ms after respective video commits. |
| New AudioRecord capture failure | **PASS in this recording** | One capture start, positive Java ByteBuffer reads, successful recorded file, no `audio_read_error` or `audio_bridge_error`. Duplicate stop on an uninitialized AudioRecord remains after capture, as in Phase 10. |
| Camera2 / EGL / output Surface stable | **PASS during active capture** | Original Surface `52110588`; one SessionConfiguration redirect and one EGL producer established before recording. During capture: two requests/two commits, no session creation, Camera.close, EGL release or pipeline error; PID 7651 stable. |
| MICROPHONE and MUTED regressions | **PASS reused Phase 10; not re-recorded** | Existing recorded MIC and all-zero MUTED evidence reused. PCM writer unchanged, all six writer contract tests PASS. Mode branches remain pass-through / zero PCM. This is not fresh on-device regression coverage of those modes. |
| Java/native fatal crash observation | **Limited PASS** | Bounded crash buffer empty; no fatal entry in collected target diagnostics. No endurance claim. |
| Existing video gap <100 ms | **FAIL remains** | 1,472 output presentations during capture; max gap 132.804 ms, zero output PTS regressions. Threshold retained. Video/EGL engine code unchanged. |

## Actual recorded evidence

Local file: `.tools/phase11/2026-10-24-100446478.mp4`.

SHA-256: `0bc93bea3571526d85af2c8f35a4394d60fb85a65b4e63167cb356f5954094a6`.

| Pixel segment | Expected audio | Direct 2-second waveform probe | Additional 1-second matching probes |
| --- | --- | --- | --- |
| A, 0–25.500 s | A | A 0.999385; B magnitude 0.114283 | A 0.698–0.999862 |
| B, 25.500–30.767 s | B | B 0.981076; A magnitude 0.098349 | B 0.976160–0.985527 |
| A, 30.767–48.967 s | A | A 0.486183; B magnitude 0.119688 | A 0.462076–0.998362 |

Video subject-frame correlation stays >=0.98066 across all three segments. The lower A waveform scores in some windows are retained, not discarded: the recording contains waveform discontinuities and the logs contain hard cursor corrections. Source identity is supported by multiple independent probes and transition excerpts; uninterrupted waveform fidelity is not claimed.

### Transition timing: distinguish source phase from audible identity

| Transition | First incoming pixels | Estimated incoming audio origin vs video source origin | Sender request → first new video output | First new-source read after video commit |
| --- | --- | --- | --- | --- |
| A→B | 25.500 s | −6.833 ms | 940.038 ms | 28.333 ms |
| B→A | 30.767 s | −60.083 ms | 237.322 ms | 30.097 ms |

Origin estimates compare incoming reference audio 0.1–0.7 s against subject-frame content and have at least ~33 ms video uncertainty. Negative means audio source phase is ahead of the corresponding video source phase; it does not establish the exact first audible incoming sample. Fully post-boundary 40 ms windows do not show a sustained outgoing soundtrack match. Incoming B stabilizes to ~0.98 correlation in windows centered ~70 ms after its pixel boundary; incoming A similarly reaches ~0.98 around +90 ms. Those windows include AAC/DSP transition effects, source head issues and clock corrections. An exact zero wrong-source/sample-perfect-switch guarantee remains unverified.

`analysis.json` preserves all probe offsets/correlations and 40 ms boundary windows. No sender log is substituted for actual file waveform/pixel evidence.

## Changes and root causes

### Startup: preparation used to follow the first capture read

Phase 10 left the decoder near source position zero while preview video advanced. First AudioRecord read requested ~2.4 s, forcing seek/flush and catch-up, causing ~201 ms missing PCM. Phase 11 updates the existing decoder target from every actual video output, including preview before capture. The ring discards old history as preview progresses, so a bounded queue cannot fill and stall while AudioRecord is idle. Capture reads no longer initiate normal preparation.

### Loop: decoder used to seek when the video loop already happened

The same MediaExtractor/MediaCodec decoder now drains EOS ahead of audible playback, keeps the current PCM tail in one bounded bank and prepares the next loop head in a second bank. At the video PTS reset, the prepared bank is promoted without clearing it. The unavoidable file seek/codec flush occurs during preparation, before the audible boundary. No additional decoder implementation was introduced.

`PcmLoopWindow` permits only current + next banks, each 500 ms. At most two source decks remain live. PCM storage is bounded (about 706 KB at tested 44.1 kHz stereo float; <=3.072 MB at allowed 192 kHz stereo). Remaining small tail gaps are not hidden: four reads missed 2,939 frames total (~66.644 ms), including the two B-head reads. The A tail gaps still require distinguishing encoded source tail/padding from block-boundary scheduling.

### Switching: prepare before forwarding, commit on renderer output

HookEntry delays forwarding a video switch request until the audio head is ready, with a logged 750 ms timeout. This occurs outside the video engine. The existing StableFrameProvider continues rendering the old source while preparation runs. Canceled requests and closed providers cannot commit late callbacks. Audio clock key/generation still changes only on actual renderer output.

### Established remaining B-head defect

Source B has AAC `encoder-delay=2112`, `encoder-padding=5`. MediaCodec's first output is PTS −1451 us, **955 frames**, then PTS 21768 us, **1024 frames**. At 44.1 kHz, the ring sees an approximately 67-frame forward gap. Existing `PcmRing.write()` resets the retained range for discontinuities >2 frames, dropping valid initial PCM. It then begins at frame 959, so readiness requiring the head at zero never succeeds. The timeout and missing 959-frame reads agree with this state.

That narrows the blocker to retention/validity across a trimmed AAC first block; it is not evidence that B has no audio or that the capture API changed. A general PTS/gap fix was **not** improvised after the sole recording. Next work should preserve valid PCM around small timestamp gaps while explicitly tracking unavailable samples, then verify B again. Do not simply relax the readiness condition or declare missing PCM ready.

### Remaining clock discontinuities

Thirty `audio_clock_resync` events were logged, including expected source/loop generation resets and additional ordinary corrections around the existing 40 ms drift boundary. Several corrections alternate forward/backward around one AudioRecord block, especially in the final A segment. The implementation performs hard cursor jumps, which can skip/repeat PCM without incrementing a buffer-underrun counter. The observed correlation drops are consistent with those discontinuities; no exclusive scheduling cause was isolated. A PLL/slew strategy needs a separate measured implementation. The drift threshold was not changed to force PASS.

## Module deployment without reinstalling TikTok

The pinned loader uses the embedded asset CRC to select an extracted module cache path and reuses an existing file. This was verified in [LocalApplicationService.java at v1.2](https://github.com/JingMatrix/LSPatch/blob/v1.2/patch-loader/src/main/java/org/lsposed/lspatch/service/LocalApplicationService.java), the pinned loader's DEX and the debuggable lab cache. Integrated-module hot reload is unsupported; this experiment restarted the app process instead.

Only Sarae's extracted module cache was replaced via `run-as`, with an original cache backup. APK PackageManager installation was not invoked. No loader validation, signature or app-integrity hook was changed.

- Cache: `cache/lspatch/co.sarae.camera.poc/183496786.apk`.
- Original cached module hash: `4e59099e01efe6837773d78ff244554ebacb3edc70c2b9f66534e3c2bcf0bb22`.
- Tested Phase 11 cache module hash: `2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0`.
- Same debug signer certificate: `8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b`.
- TikTok base hash before **and after**: `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`.
- Existing installed base still embeds **Phase 10**. Cache eviction/re-extraction will restore that embedded version. This is a lab deployment, not a durable module update mechanism.
- `audio_bridge_attached.pipelineVersion=phase11` and new loop/readiness events confirm which cached code actually ran.

An initial Windows stdin transfer stopped at byte 488 (first Ctrl-Z byte). Its hash mismatch prevented activation. `adb push` plus same-UID copy transferred the complete 1,022,128-byte module and passed SHA-256 verification. That diagnostic attempt is included in the device budget. No account file or TikTok base was changed by the failed transfer.

If a later rollback is required, stop TikTok only after recording is finished, copy `183496786.apk.phase10` back over the same module cache path with `run-as`, then relaunch. Do not uninstall or clear data. No rollback was performed here; final audio mode is **MICROPHONE** (ack ns 9424748021543090).

## Verification scope and timing

- **166.422 s** accounted device execution, including preflight, transfer diagnostics, process restart, human waiting/recording and final file/log collection. State is in `state.json`; final console initially double-counted its own 4.156 s command stage when printing a closed timer (170.578 s). The closed-state display calculation was corrected; both figures remain below 300 s.
- Two source switches, one saved recording. Cleanup began with well over the required 30 s remaining. All ADB calls had hard 3–10 s host deadlines; no endurance or extra recording round.
- Actual saved container duration reported by MediaStore: **49.017 s**, longer than requested 20–40 s. Decoded audio 48.971 s / analyzed video 48.967 s. The target duration was not met; actual evidence is retained.
- 21/21 PCM/ring/loop tests PASS; no unrelated consumer build. New tests verify retained current tail, next-head promotion, bounded progress without capture reads and no old-bank reuse for absent epochs.
- Existing StableFrameProvider, EglOutput and Camera2HookPrototype source hashes are unchanged. MICROPHONE/MUTED Phase 10 recorded results are reused, not represented as new recordings.
- `stop()` on an uninitialized AudioRecord was logged after capture ended, as in Phase 10. No new negative read result/capture failure was logged. Camera lifecycle after the human stopped recording is separate from the stable active switch window.

## Evidence and exact next blockers

`final-audio.jsonl`, `final-bridge.jsonl`, `analysis.json`, `loop-readiness.json`, `switch-latency.json`, `base-before.txt`, `base-after.txt`, `crash-buffer.txt`, `fatal-loader-logcat.txt`, `build-tests.txt`, `preflight.json` and `adb.jsonl` preserve the measurements. Original MP4, module APK, reference loader source and frame-match arrays remain locally under `.tools/phase11/`.

1. Correct trimmed-AAC PCM retention/readiness for B; do not hide the resulting gaps with a larger timeout or fake ready flag.
2. Replace hard cursor oscillation with a measured audio clock correction policy, preserving source commit ordering and bounded memory.
3. Revalidate sample-level transitions, B preparation and those corrections in a newly authorized short recording. Current evidence verifies source identity, not fully seamless audio.
4. Preserve unresolved video cadence >100 ms; no video pipeline rewrite or production/LIVE compatibility claim.
