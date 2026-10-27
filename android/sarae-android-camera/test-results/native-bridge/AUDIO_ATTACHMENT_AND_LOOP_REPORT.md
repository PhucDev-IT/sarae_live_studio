# Audio attachment feasibility and four-second loop correction

2026-10-27 · `feature/tools-android` · SM-G996U / Android 14 · No LIVE or TikTok change

## Decision

| Gate | Result |
| --- | --- |
| Four-second source, three encoded loop boundaries | **PASS with a residual 7–9 ms PCM deficit at each boundary** |
| Encoded output timestamps | **PASS:** 748 audio packets, zero non-increasing PTS |
| Encoded content from each loop | **PASS:** correlation 0.9895–0.9947 against source A across four audible cycles |
| Generic non-cooperative OpenSL attachment | **FAIL to establish:** no supported mechanism to discover/own another component's recorder queue, callback or completed buffer |
| Isolated non-cooperative interception | **NOT RUN:** no safe proposed attachment mechanism passed the precondition; repeating owned-hook tests would not satisfy this gate |
| TikTok native audio deployment | **NO-GO**; current installed target also differs from the previous lab build |

## Root cause and correction

The four-second audio-only test fixture is a stream-copied MP4 with an edit list. `ffprobe` reports container/audio-stream duration **4.021 s**. On this Samsung build, `MediaExtractor` reports `durationUs=8020166` and the decoder emits PCM PTS beginning at **−4,000,000 µs**. The owned harness used that audio track duration to fabricate video PTS. It therefore did not tell `VideoAudioClock` to loop until **8.020 s**. `MediaAudioEngine` had already decoded/prepared the next-loop PCM head at about 3.83 s, while the old bank exhausted at about 4.02 s. This explains the previous **3.98 s encoded silence**. The evidence is the original `bridge-modes` JSONL: `audio_eos`, `audio_next_loop_prepare(epoch=1)`, `audio_pcm_ready(epoch=1)`, then `audio_video_loop(epoch=1)` only near 8.02 s.

The production audio engine already takes loop/source timing from the actual video renderer. The defect was the *synthetic video timeline in the audio-only harness*, so changing decoder, PCM ring or EGL would have addressed the wrong component. `RecorderActivity.kt` now accepts an explicit synthetic video-loop duration for the owned harness and logs both track and video durations. `native_bridge_device.py` derives that duration from `ffprobe`'s container timeline rather than `MediaExtractor`'s edit-list-adjusted track value. The default harness behavior remains available for earlier tests. No Audio Engine or video source file changed.

In the corrected 16-second owned recording, three `audio_video_loop` events promoted three separately prepared PCM banks; there were zero `audio_loop_not_ready` events and zero stale-generation blocks. Encoded A correlation at output 0.8/4.8/8.8/12.8 s was **0.9927 / 0.9931 / 0.9933 / 0.9935**; the matching windows 2.0/6.0/10.0/14.0 s were **0.9923 / 0.9926 / 0.9925 / 0.9927**. The longest near-silent span in the encoded result was **0.20 s at startup**. There was no multi-second gap at any loop. Engine missing PCM at the three loop boundary blocks was **441 / 391 / 343 frames at 48 kHz** (9.19 / 8.15 / 7.15 ms). Total missing frames were 8,855, mostly startup. The recorder produced 1,591 completed blocks with zero enqueue failure or packet drop. These residual deficits remain reported, not relabeled as zero-gap PASS.

`scripts/analyze_native_bridge.py` reproduces source correlation, encoded silence runs, loop-event counts and AAC packet-PTS checks. Raw evidence is preserved under ignored `.tools/native-bridge/bridge-loop-corrected/bridge-loop-corrected/`; machine summary is `.tools/native-bridge/analysis.json`. The targeted owned-device run added **22.735 s** to the prior harness script's cumulative counter; read-only identity checks added only a few seconds. No other recording or camera regression was run in this task.

## OpenSL attachment contract

The documented Android simple-buffer-queue recorder protocol is owner-driven: the owner creates the recorder, obtains the queue interface, registers a callback, enqueues empty `(pointer, byte length)` buffers, receives a completion callback, reads the completed buffer, then may re-enqueue it. The callback itself receives queue and context, **not** the PCM pointer/length. A different component cannot use those public interfaces to enumerate an existing recorder, inspect its registered callback or acquire its completed buffer. [Android OpenSL extensions](https://developer.android.com/ndk/guides/audio/opensl/android-extensions), [Android OpenSL example](https://android.googlesource.com/platform/system/media/+/bd2fc031926582ee8b0df40673c66dfa4cc45a61/opensles/tests/examples/slesTestRecBuffQueue.cpp).

For a non-cooperative owner, a hypothetical in-process interposer would have to be present **before** creation/registration, identify the recorder and its queue, record every successful `Enqueue(pointer,length)` in FIFO order, wrap and forward the original callback exactly once, modify only the returned buffer before the owner consumes it, then safely unwind STOP/Clear/Destroy and library unload. OpenSL has no documented interface to install such an interposer or recover already registered state. Hooking global `slCreateEngine`, implementation vtables, `RegisterCallback` or `Enqueue` would be a new native interception mechanism, not an OpenSL feature. No such mechanism has been validated in the isolated non-cooperative scenario, so it was **not** installed in the Sarae harness or TikTok.

Phase 19–20 proved Dobby/trampoline behavior on an **owned function**, with cooperative lifetime. They did not hook the standard OpenSL API. Phase 20's observer-side `dlopen(..., RTLD_NOLOAD)` could not pin an already mapped owned JNI library across its loading context (`-43`); the owner had to hand over a strong reference. A public `libOpenSLES` handle would not by itself pin a foreign callback library such as `liblyrax` or establish recorder-object and buffer lifetime. A mapped ELF is not ownership. [Phase 20 evidence](../phase20/PHASE20_REPORT.md), [Android linker namespaces](https://source.android.com/docs/core/architecture/vndk/linker-namespace).

The current `NativeAudioBridge` therefore reports **Ready** only for an owner-provided completed buffer with a validated format and reports **Unsupported** for foreign OpenSL, native AudioRecord, AAudio and system input. The earlier owned harness logged capability vector `[1,0,0,0,0]`. AudioRecord routing controls apply to the calling recorder, while playback capture copies eligible output into the capturing app; neither provides cross-app microphone injection. [AudioRecord routing](https://developer.android.com/reference/android/media/AudioRecord#setPreferredDevice(android.media.AudioDeviceInfo)), [playback capture](https://developer.android.com/media/platform/av-capture). This is a conclusion about the documented ordinary-app interfaces and verified lab environment, not proof that privileged vendor integration is impossible.

## TikTok APK identity correction

The hashes are for **different artifacts**, both TikTok 47.4.3/470403:

| SHA256 | Identity and evidence |
| --- | --- |
| `80277ad5eaf6eb5d0dcf39537b03274ce417bab8259d661003a6a1fc1ff0f6dd` | Original, unpatched base archived in [Phase 6](../phase6/REAL_TIKTOK_REPORT.md). This is also the base **currently installed** on 2026-10-27. |
| `4cab9b7751c35601594d9b12fc94d8db3ad5a485a9161c522fee118f900b` | Patched LSPatch lab base produced in Phase 10 and verified through Phase 23; see [Phase 20](../phase20/PHASE20_REPORT.md) and [Phase 23](../phase23/PHASE23_REPORT.md). |

The current package has **51 APK paths**, `firstInstallTime=2026-10-27 10:56:03`, `lastUpdateTime=2026-10-27 13:45:14`, and `run-as` returns `package not debuggable`. The earlier lab snapshot had **61 APK paths**, a different first-install time and a readable LSPatch cache. This is consistent with a replacement/reinstallation of the original signed package; the exact intervening operation and data state are **unknown**. The previous owned-harness report only proved `80277…` stayed unchanged during *that* run; it did not prove the Phase 23 lab remained installed. No install, uninstall, data clear or module update was performed here for TikTok.

## Portability and next action

The owned bridge core has no Samsung/PID/function-offset dependency; its format is negotiated by the owner. This harness test is one Android 14 implementation, and the test script's fixture timing must not be mistaken for a production media clock. OpenSL callback scheduling, buffer size, object representation, linker namespaces and whether an app uses OpenSL/AAudio/native AudioRecord vary across Android versions and apps. An unverified global hook cannot be called portable.

**Single next action:** decide whether to move the broadcast endpoint to a supported desktop input workflow (TikTok LIVE Studio/LIVE Manager Practice Mode) and test Sarae audio as a selectable PC audio source there. TikTok documents desktop selection of audio sources and Practice Mode; this would be an architecture change, not a fix to Android TikTok's native recorder. [TikTok LIVE Manager](https://seller-us.tiktok.com/university/essay?knowledge_id=1195537245292331). Under the current non-root, no-hardware Android constraints there is no validated attachment to deploy, and the current TikTok installation is not the LSPatch lab build.
