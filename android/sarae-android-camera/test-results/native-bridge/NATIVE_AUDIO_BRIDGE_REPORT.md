# Native Audio Input Bridge — owned OpenSL verification

## Decision

- **AUDIO_CORE_PASS** in Sarae's owned OpenSL ES recorder. VIDEO, MICROPHONE, MUTED and encoded A→B→A output were checked on SM-G996U / API 34.
- **BLOCKED** for TikTok attachment. No supported ordinary-app Android interface was found that supplies PCM as another application's microphone. The Phase 20 linker namespace/lifetime failure still prevents safely binding an in-process observer to TikTok's foreign native recorder. No target callback/buffer ownership contract has been verified. This change was not deployed to TikTok.
- **TIKTOK_LOCAL_AUDIO_PASS: not tested** with this native bridge. Earlier Java `AudioRecord` recording success is a separate path.
- **TIKTOK_LIVE_AUDIO_UNVERIFIED**. Phase 23 independently heard the microphone rather than the MP4 soundtrack; this implementation does not change that result.

## Implementation

`native-audio-adapter/src/main/cpp/native_audio_bridge.{h,cpp}` accepts a validated capture format (rate, channels, encoding and maximum frames), an owner-returned completed buffer and pre-converted PCM blocks tagged by source generation. It has a 32-block SPSC queue, explicit `Unsupported` for foreign OpenSL, native AudioRecord, AAudio and system input, and explicit `InvalidContract`. The callback uses bounded copies/atomics only: no allocation, JNI, decoder, file I/O, logging or waits. On VIDEO underrun it writes silence and counts missing frames. A stale-generation block is discarded rather than played after a switch. MICROPHONE leaves the returned buffer intact; MUTED fills format-correct silence.

The existing Phase 15 recorder supplies the completed buffer, invokes the bridge before re-enqueue, and keeps the original queue and callback lifecycle. The existing `MediaAudioEngine`, `VideoAudioClock` and `PcmBufferWriter` provide decoded, converted PCM. The test harness commits audio generations at A→B→A source transitions. No Camera2, EGL, video or TikTok module code changed.

Android's documented OpenSL recorder model supports the owner enqueuing empty buffers and reading a buffer after its completion callback. It does not grant a separate app ownership of TikTok's queue or buffer. Sources: [Android OpenSL ES extensions](https://developer.android.com/ndk/guides/audio/opensl/android-extensions), [OpenSL ES programming notes](https://developer.android.com/ndk/guides/audio/opensl/opensl-prog-notes), [audio input sharing](https://developer.android.com/media/platform/sharing-audio-input), [AAudio input streams](https://developer.android.com/ndk/guides/audio/aaudio/aaudio.html). The absence of a general cross-app PCM injection interface is an inference from the documented APIs and current privileges, not a claim that every privileged or vendor mechanism is impossible.

## Device and encoded-output evidence

The bounded script `scripts/native_bridge_device.py` touched only `co.sarae.camera.nativeaudiotest`. It installed its APK, recorded 10 seconds each of MIC/VIDEO/MUTED and two 12-second A→B→A clips, then returned HOME. The initial A→B→A launch did not create a new Activity after the mode suite; it produced no capture and was corrected by starting only the owned app with `am start -S`. Cumulative active device work recorded by the script: **108.204 s**, plus a brief read-only crash-buffer snapshot; under 5 minutes. TikTok base SHA256 was unchanged (`80277ad5eaf6eb5d0dcf39537b03274ce417bab8259d661003a6a1fc1ff0f6dd`); its PID remained 16654. No Sarae harness hit appeared in the captured crash buffer.

| Gate | Measured result |
| --- | --- |
| Build | `:native-audio-test:assembleDebug --offline` PASS |
| MIC | Delivered raw PCM byte-identical to captured mic; 992/992 callbacks; encoded output RMS 0.00263 |
| MUTED | 0 nonzero raw samples; encoded RMS 0; 992/992 callbacks |
| VIDEO | Encoded windows at 1.0 and 2.5 s correlate 0.9997/0.9998 with source A; 991/991 callback delivery |
| A→B→A | Commits near 3.979 and 7.907 s after RECORDING. Encoded long-fixture windows classify A/A/A, B/B/B, A/A/A; matching-source correlation 0.9916–0.9998 |
| OpenSL continuity | Long A→B→A: 1193 callbacks, 0 packet drops, 0 enqueue failures, 0 native queue underruns, 14 stale-generation blocks discarded; maximum measured callback work 77.45 µs in the short-fixture run |
| PCM availability | Long A→B→A engine reported 24,222 missing frames (0.505 s total), concentrated at startup and end; source switching did not create a separate reported engine underflow. The bridge's zero native underruns means the engine supplied silence as PCM blocks. |
| Loop | Short 4-second fixture VIDEO produced 3.98 s of encoded silence after the first loop, despite continuing callbacks. This is a PCM Engine/timeline availability failure, **not** a loop PASS. |

The long-fixture output has about 0.31 s initial silence and 0.23 s tail silence; the 0.2 s quiet span inside B matches the B source itself. Output-source offset in the correlation windows is about 42 ms at initial A and approximately 0–20 ms after each commit. This is audio-source alignment in an owned harness, **not actual camera-video A/V sync**.

Detailed JSON, event logs, encoded MP4s and raw PCM are retained under ignored `.tools/native-bridge/`. `scripts/analyze_native_bridge.py` reproduces the correlation and silence measurements. The media and raw evidence are deliberately excluded from Git.

## Remaining integration contract

The owned recorder's callback provides a known 48 kHz/stereo/S16LE, 480-frame buffer and retains the queue object. TikTok's LIVE recorder uses OpenSL according to Phase 23 native samples, but its recorder instance, callback registration, negotiated format, buffer extent/ownership and capture-to-encoder handoff are not established. Phase 20 could not safely pin `liblyrax` from the observer's linker namespace. The static candidate RVA 0x6be020 remains unconfirmed at runtime. The core therefore rejects the foreign-recorder backend rather than silently passing through the microphone in a purported VIDEO mode.

**Next checkable action:** obtain a cooperative, supported recorder-buffer handoff from a target that owns the OpenSL recorder, or an explicitly supported Android/system virtual-input interface with the needed privileges. Validate its buffer/format/lifecycle contract with the existing bridge before any TikTok deployment. Without such an attachment mechanism, further PCM decoder work does not solve LIVE audio input.
