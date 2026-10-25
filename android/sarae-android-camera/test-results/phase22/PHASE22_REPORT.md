# Phase 22 — Native audio profiling on the owned OpenSL recorder

2026-10-25, `feature/tools-android`. **PASS for observation of the Sarae-owned active recorder.** This does **not** identify or validate TikTok's LIVE audio backend. No TikTok LIVE, TikTok APK/module change, new recorder, or new native hook was involved.

## Valid measurement

Existing `co.sarae.camera.nativeaudiotest` ran its Phase 15 suite; profiling overlapped the first, 10-second **MICROPHONE** case. Recorder state was `RECORDING` from device elapsedRealtimeNs **9507928801636282** to **9507938831304559**. The profiler window, `/proc/uptime` **9507929.46–9507936.02 s**, lies wholly inside that interval. The MIC case completed **994** OpenSL buffer callbacks; the suite emitted `suite_complete`, no `suite_error`, and saved a valid **9.984 s AAC, 48 kHz, stereo** `MICROPHONE.mp4`.

Device command:

```text
adb shell simpleperf record --app co.sarae.camera.nativeaudiotest -f 499 -e task-clock -g --duration 6 -o /data/local/tmp/sarae-phase22-perf.data
```

It recorded for **6.04931 s**, with **955 samples, 0 lost**. The host call-graph command uses the unstripped owned build for symbol resolution:

```text
simpleperf report -i perf.data --tids 29719 -g --full-callgraph -n --percent-limit 0 --symdir native-audio-adapter/build/intermediates/cxx/Debug/4f3f3f59/obj
```

**56 samples** were on the owned process's `AudioRecord` thread, TID **29719**. The symbolized call graph includes a **6-sample (10.71% of that thread)** path through the project's callback:

```text
android::AudioRecord::AudioRecordThread::threadLoop()
  → android::AudioRecord::processAudioBuffer()
    → android::AudioRecordCallback::onMoreData(...)
      → audioRecorder_handleMoreData_lockRecord(...)
        → (anonymous namespace)::Recorder::callback(SLAndroidSimpleBufferQueueItf_ const* const*, void*)
          → Ring<Packet,128>::push(...) / IBufferQueue_Enqueue(...)
```

This is capture-specific evidence, rather than unrelated ART, MediaCodec or binder activity. The same `AudioRecord` thread also had samples in `android::ClientProxy::obtainBuffer(...)`; other threads in the 955-sample total included the app worker, `MediaCodec_loop`, binder and ART. System-library symbols resolved on device; the local unstripped `libsarae_native_audio.so` resolved the owned callback. Kernel symbols remained restricted, and were not needed for the user-space call path. Sampling cannot reveal the callback's PCM ownership or guarantee observing every invocation.

## Recording continuity and observed cost

| MIC callbacks | Before profile | During profile | After profile |
| --- | ---: | ---: | ---: |
| Count | 61 | 656 | 277 |
| Median inter-callback gap | 9.987 ms | 10.300 ms | 9.958 ms |
| P99 gap | 12.095 ms | 12.239 ms | 12.173 ms |
| Maximum gap | 12.100 ms | 12.518 ms | 12.220 ms |
| Median callback work | 7.031 µs | 8.750 µs | 8.125 µs |
| Missing frames / enqueue failures | 0 / 0 | 0 / 0 | 0 / 0 |

The profiler command took **6.562 s wall time**, including post-processing, and produced a **1,607,945-byte** perf file. The callback stream and encoded output completed without interruption. The during/after cadence differences are observations from one run, **not a causal estimate of profiler CPU overhead**. No extra baseline run or higher sampling rate was used.

## Initial attempts and limits

Two setup attempts were preserved rather than counted as successful capture measurements. In `phase22-020818`, the display slept; the Activity hit `onStop` about 0.1 s after entering `RECORDING`, and profiling produced **12 unrelated ART/kernel samples**. In `phase22-021013`, Android resumed that old Activity (`LaunchState: HOT`) without a new `onCreate`; profiling produced **0 samples** and no new recording. The final launcher used `am start -S` **only for the owned harness** to create a fresh test instance. Its screen-awake setting was temporarily set for the test and restored to the prior value `0`. The exact LSPatch TikTok cache SHA remained `66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120`; TikTok PID stayed **24699**. The temporary device perf file was removed after pulling it. No TikTok activity was started or stopped.

Automated device runs were **12.828 + 54.000 + 34.922 = 101.750 s**; ancillary bounded ADB checks kept cumulative active device work well below five minutes. There was one valid active-capture profiling run and no endurance test.

## Future observation-only LIVE diagnostic plan — requires explicit approval

**Profiler feasibility: YES for a debuggable, active Sarae-owned recorder; CONDITIONAL for TikTok LIVE.** Phase 21 verified TikTok lab is debuggable and `simpleperf --app` is permitted, but its background run had zero samples. The actual TikTok LIVE native capture entry, sample visibility and symbol availability remain unknown. The Phase 20 target hook binding/lifetime blocker is unchanged. No MP4 soundtrack replacement in LIVE is active.

Before requesting one user-controlled LIVE diagnostic, keep TikTok's Phase 18 module and account intact; verify exact hashes, foreground preview, source media, independent viewer and available storage. Prestage only `simpleperf --app com.ss.android.ugc.trill -f 499 -e task-clock -g --duration 6`, bounded process/log collection and a cleanup command. Do not deploy a liblyrax hook or PCM replacement. In the separately approved session, the user alone starts/ends LIVE; collect one short profile while capture is active and correlate it with AudioFlinger, Camera2/EGL continuity and independent viewer audio/video. Do not restart or modify TikTok while broadcasting. If the capture-specific stack is absent, mark the backend **UNKNOWN** and stop; do not infer absence of native capture from zero callback samples. Receiver audio remains the authority for any eventual soundtrack claim.

Evidence: [analysis.json](analysis.json), [symbolized AudioRecord call graph](audiorecord-symbolized-callgraph.txt), [flat AudioRecord samples](audiorecord-flat.txt), `test-results/phase22/state.json`, ignored `.tools/phase22/phase22-021144/perf.data` (SHA256 `4dc9782220d44e1ed87f9e9d415774bf608746a5d4330d9b8ee7a89de3934e01`) and harness events/MP4. Replay script: [phase22_profile.py](../../scripts/phase22_profile.py); offline analysis: [analyze_phase22.py](../../scripts/analyze_phase22.py).
