# Native Audio Bridge feasibility decision

2026-10-25 · `feature/tools-android` · Samsung SM-G996U / Android 14 / non-root · TikTok lab 47.4.3

## Decision

**NO — a TikTok LIVE native PCM bridge is not feasible for deployment in the currently verified configuration.** This is an evidence and safety decision, not a proof that native audio interception is impossible on Android. The installed TikTok module remains Phase 18 load-only; no target PCM observer or replacer is installed. A separate **physical USB audio input** is a conditional, supported-interface alternative, subject to device routing and TikTok recording verification.

No LIVE, device test, TikTok APK/module change, or new recorder/decoder/hook was performed for this decision.

## Evidence boundaries, Phases 12–20

| Evidence | Established | Does not establish |
|---|---|---|
| Phase 11 saved TikTok recording | Java `AudioRecord.read(ByteBuffer,int)` replacement produced matching A→B→A MP4 soundtrack | LIVE uses that Java read path |
| [Phase 12 real LIVE](../phase12/PHASE12_REPORT.md) | Receiver and sender video froze; receiver heard mic, not verified MP4 soundtrack. AudioFlinger reported a 48 kHz/stereo/S16 MIC track. No new Java bridge read/substitution occurred during LIVE. There were **zero** A/B switches during LIVE. | Which native client API and buffer contract created that track; an EGL freeze root cause |
| [Phases 13–14](../phase14/PHASE14_REPORT.md) | Camera2 ownership/request fixes passed in actual TikTok preview; static `liblyrax` OpenSL recorder evidence exists | The fixes have repaired LIVE video; OpenSL was the selected LIVE recorder |
| [Phase 15](../phase15/PHASE15_REPORT.md) | Existing Sarae PCM Engine and owned OpenSL recorder generated verified VIDEO/MIC/MUTED encoded output | TikTok uses the harness recorder's two 480-frame buffers, format, callback, or encoder handoff |
| [Phase 16](../phase16/PHASE16_REPORT.md) | Pinned `liblyrax.so` contains a static OpenSL queue callback registration candidate (RVA `0x6be020`) | Candidate executes during LIVE or exposes PCM pointer/length in its callback arguments |
| Phases 17–18 | JNI observer loaded in TikTok; controlled owned callback forwarding passed. Phase 18 TikTok module remains load-only. | Observer reached a target recorder; native LIVE backend was identified |
| [Phases 19–20](PHASE20_REPORT.md) | Pinned Vector native API/Dobby passed owned interception tests: 1,118/1,118 original forwards, 990 capture callbacks, byte-identical original PCM, clean STOP/DESTROY and encoded MIC output. Exact 12-byte prologue/continuation and one B-branch relocation were tested. | A liblyrax trampoline or target lifetime is safe; BL at byte 12 was relocated; LIVE soundtrack was injected |

## Linker namespace and library lifetime

Phase 20 resolved and checked the **owned** mapped ELF, Build ID, executable region, entry fingerprint and load bias. Yet the observer module's `dlopen(..., RTLD_NOLOAD)` returned `-43` with “wasn't loaded and RTLD_NOLOAD prevented it.” The owning JNI library could obtain its own strong reference and explicitly pass it to the observer; this cooperative handoff enabled the owned test. These facts support a loading-context / linker-namespace visibility mismatch, **without proving the exact linker rule**. Android documents that linker namespaces can isolate shared libraries, and `ANDROID_DLEXT_USE_NAMESPACE` is internal rather than an app NDK API. [AOSP linker namespaces](https://source.android.com/docs/core/architecture/vndk/linker-namespace), [NDK dynamic linker](https://developer.android.com/ndk/reference/group/libdl).

TikTok's unmodified `liblyrax` supplies no corresponding cooperative lifetime handoff. An ELF mapping, borrowed loader handle, resolved address, or successful JNI load of Sarae's own library is not proof that the foreign library remains loaded through hook installation, callback forwarding and removal. Bypassing the failed guard would turn a known safe refusal into an unverified target hook. The target's selected LIVE backend, callback invocation, client PCM size/format/ownership, and capture-to-encoder handoff also remain unknown. Phase 20's deferred worker notification does not prove attachment before native capture begins. Those are **independent blockers**, not merely missing test repetitions.

## PhantomMic and GlassMic comparison

The pinned source review is in [LIVE_AUDIO_PREPARATION.md](LIVE_AUDIO_PREPARATION.md); no code was copied.

| Project | Library access and recorder lifecycle in inspected code | Applicability here |
|---|---|---|
| [PhantomMic](https://github.com/Mino260806/PhantomMic/blob/768cb32db3eab234083bdc35ad40f9bed833fcc8/app/src/main/cpp/main.cpp) | Uses injected `native_init` hook/unhook and chooses private native `AudioRecord` symbols/libraries by SDK. Hooks `set`, `obtainBuffer`, `stop`; obtains original buffer then replaces content using assumed private buffer fields. | Does not provide a verified cross-namespace strong reference to TikTok `liblyrax`, nor evidence that LIVE takes native AudioRecord rather than OpenSL/AAudio. Its root/LSPosed-oriented README treats non-root LSPatch as untested. Private field offsets and signatures cannot be adopted as Android 14 contracts. |
| [GlassMic](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_opensl.cpp) | Hooks `slCreateEngine`, follows OpenSL engine/recorder interfaces and shared vtables through registration, enqueue and callback; also hooks [native AudioRecord read](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_audiorecord.cpp) and [AAudio paths](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_aaudio.cpp). Tracks recorder/FIFO lifetime and preserves original callback/userdata. | Capturing a recorder from creation can avoid guessing a callback RVA, but its **global** API/vtable hooks, private object assumptions and root/LSPosed environment are not validated under Sarae's non-root LSPatch/TikTok process. It does not turn the unknown LIVE-selected backend into an observation. |

These implementations show possible interception designs. Neither establishes a supported Android API for one ordinary app to supply another app's microphone PCM, nor a validated TikTok LIVE client contract.

## Supported-interface alternatives

| Mechanism | Assessment in current Android/TikTok setup |
|---|---|
| `AudioPlaybackCapture` / `MediaProjection` | Captures **eligible playback from other apps into the capturing app**; it does not inject samples into TikTok's mic input. Capture is gated by playback usage, policy, profile and user consent. [Android playback capture](https://developer.android.com/media/platform/av-capture). |
| `AudioRecord.setPreferredDevice` / `AudioManager` routing | TikTok must choose or accept a connected input; Sarae cannot call `setPreferredDevice` on TikTok's private recorder via a supported cross-app API. Android lists physical external microphones/interfaces as input sources and can report connected inputs. [Android audio input sharing](https://developer.android.com/media/platform/sharing-audio-input), [AudioManager.getDevices](https://developer.android.com/reference/android/media/AudioManager#getDevices(int)). |
| **Class-compliant USB audio input, externally fed from the approved MP4 soundtrack** | **CONDITIONAL**. Android supports USB audio input in host mode; this avoids a native TikTok hook and routes audio as a physical microphone/interface. Whether this Samsung build selects that input for TikTok LIVE, channel mapping, level, drift and viewer output are unverified. [AOSP USB digital audio](https://source.android.com/docs/core/audio/usb). |
| TikTok desktop LIVE Studio / LIVE Manager | Official desktop workflow can select audio/video sources and use practice mode, but it is a **different broadcasting architecture**, not a fix for the Android TikTok lab app. [TikTok LIVE Manager](https://seller-us.tiktok.com/university/essay?knowledge_id=3360165320984366). |

## One verifiable next direction

**Test external USB audio input without LIVE, only if a class-compliant interface is available.** Feed the approved MP4 soundtrack from the host to the interface's line input at the correct electrical level; connect the interface as a USB microphone to the Samsung. This is a physical input path, not speaker-to-mic re-recording and not a software virtual-mic claim. Keep TikTok APK/module/account unchanged.

The decisive short test is a saved, unpublished TikTok recording with a distinct audio marker and the host speakers silent. Record connected-input type and active recording route where observable; compare the saved file's waveform to the source and check that ambient mic speech does not replace it. **GO for considering one later user-controlled LIVE verification only if** the target recording really uses the external input, the video preview remains stable, the chosen media is appropriate, and sender/viewer evidence collection is prepared. A recording PASS still does not prove LIVE routing or viewer audio. If TikTok ignores the external input, report **NO** for this approach; no native hook escalation follows automatically.

No device test is justified until the interface and a distinct source are available. No additional native-audio phase is proposed without a decisive new integration mechanism or observed target capture contract.
