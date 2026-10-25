# Phase 24 — TikTok LIVE OpenSL audio path

**Decision: native PCM bridge is NO-GO in the current non-root LSPatch setup.** Phase 23 confirms TikTok's active LIVE audio capture enters the OpenSL ES recorder callback path and then `liblyrax`; the receiver still got microphone audio and no MP4 soundtrack. The target callback/buffer contract and a safe target binding are not established. No LIVE, device test, APK/module change, or audio code change was made for Phase 24.

## Receiver evidence

The user-provided viewer recording is present locally (489,033,353 bytes). `ffprobe` reports 266.33 s, HEVC 1290×2796 at average 18.04 fps, plus AAC stereo 44.1 kHz. This confirms the recording contains an audio stream, but codec metadata cannot identify its sound source. The viewer's direct report is that video was moving and the audio was noisy microphone only, without MP4 music. Therefore:

| Gate | Result | Evidence scope |
|---|---|---|
| Receiver video motion | **PASS (user-observed)** | User confirmed motion; this clip's video track is present. No independent orientation assessment was made. |
| Receiver MP4 soundtrack | **FAIL (user-observed)** | User heard mic noise and no MP4 soundtrack. |
| Sender PCM decode | **PASS, unrelated to delivery** | Phase 23 engine log has 240 `pcm_write` events for source A. |
| TikTok capture replacement | **FAIL** | AudioFlinger reports active MIC capture, and receiver heard mic only. |

The 266 s viewer recording metadata alone does not prove motion or classify its encoded sound. Receiver observations above are explicitly attributed to the user.

## Confirmed LIVE call chain

Phase 23 profile `perf.data` (SHA256 `b6fa7294c1f567644127d95e8d00924ac7add29ce6c7821794fdfc197a966c20`) collected 4,852 samples, zero lost, over 6.02564 s. In the TikTok process, TID 32523 (`AudioRecord`) had 194 samples and this stack:

```text
android::AudioRecord::AudioRecordThread::threadLoop()
  → android::AudioRecord::processAudioBuffer()
    → android::AudioRecordCallback::onMoreData(...)
      → libwilhelm.so: audioRecorder_handleMoreData_lockRecord(...)
        → liblyrax.so+0x6b9b08 → +0x6b1278 / +0x6a8f3c / +0x6b08a0 …
```

The named `libwilhelm` function is the OpenSL ES simple-buffer-queue recorder callback path. Together with execution into `liblyrax`, this confirms an active OpenSL ES capture route during the profiled LIVE. It does not prove OpenSL is the only route. A separate `rx_audio_enc` TID 32524 had 122 samples in `liblyrax`'s audio encode path. This establishes capture and encoding work in the same process, but does not show which client buffer contents reached the encoder.

AudioFlinger independently reports record ID 53071, session 57153, PID 24699, source MIC, 2-channel 48 kHz PCM16, not silenced. These are the platform capture format, **not** the OpenSL queue's client buffer length, capacity, ownership or lifetime.

## Candidate `0x6be020`

The mapped `liblyrax.so` matched Phase 16's pinned SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de` and Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`.

Static Phase 16 disassembly at `0x6bdab4` calls an outlined virtual dispatch after setting arguments to queue (`x0`), callback address `0x6be020` (`x1`) and context (`x2`). The thunk uses vtable slot `0x18`; this was assessed as consistent with OpenSL simple-buffer-queue `RegisterCallback`, and `0x6be020` has a two-pointer callback-shaped entry. This remains a static registration/callback candidate.

**The Phase 23 profile does not contain a sample at `liblyrax+0x6be020` or the registration callsite `+0x6bdab4`.** Its sampled frames at `+0x6b9b08`, `+0x6b1278`, `+0x6a8f3c`, and `+0x6b08a0` prove execution in other `liblyrax` code under the observed OpenSL callback, not execution of that specific candidate. The ELF has no private `.symtab`; these offsets cannot be assigned reliable source-level names from this evidence. Candidate status: **UNVERIFIED at runtime**.

## PCM handoff still missing

Still unknown:

- which registered callback/context instance is active and how it maps to the profiled `liblyrax` frames;
- where the application-owned PCM storage is allocated and how many buffers are queued;
- client buffer pointer, byte length/capacity, PCM layout at callback time, queue order, and when ownership returns;
- how capture data is copied, transformed or mixed before the `rx_audio_enc` path;
- whether the encoder consumes that same buffer or a downstream copy.

The OpenSL callback itself receives queue and context, not a PCM pointer/length as direct callback arguments. No context cast or buffer-size guess is justified. AudioFlinger’s 48 kHz/stereo/PCM16 cannot fill that gap. The receiver result demonstrates that the Sarae MP4 PCM writes did not replace the mic path in this session; it does not identify exactly which internal copy/handoff rejected or bypassed it.

## Reusable implementation and deployment blocker

Reuse unchanged if a safe capture handoff is ever established: `MediaAudioEngine`, bounded PCM ring, `VideoAudioClock`, format conversion, source-commit coordination, and VIDEO/MIC/MUTED policy. The Phase 15 OpenSL adapter/observer is valid for a recorder owned by Sarae only. The Java `AudioRecord.read(ByteBuffer,int)` bridge does not cover this observed native LIVE callback route.

Phase 20 validated hook/trampoline behavior only with cooperative ownership in the Sarae harness. In TikTok, module-side `dlopen(...RTLD_NOLOAD)` failed with `-43` (“wasn't loaded and RTLD_NOLOAD prevented it”) for the mapped foreign ELF. The mapping and fingerprint were readable, but the observer could not acquire a strong library reference through its loading context. A handle, mapping, or deferred load notification does not ensure `liblyrax` remains loaded through hook install, callback forwarding and removal. The owned harness solved this only because its owning JNI code cooperatively passed a strong reference; unmodified TikTok offers no such handoff. This supports a namespace/visibility mismatch but does not establish the exact linker rule.

Other independent gates remain: no verified non-disruptive target attachment before capture starts, no runtime proof of candidate registration, no target buffer contract, and no observation of the capture-to-encoder buffer transfer. Therefore no safe target hook or PCM replacement can be implemented from current evidence. Existing Sarae observer remains load-only; installed TikTok module and Camera2 fixes remain unchanged.

## One next step

**Use a class-compliant USB audio input as the single supported-interface experiment, if one is available:** feed the approved MP4 soundtrack at line level to that interface, connect it to the SM-G996U, then make one short unpublished TikTok local recording with the host speakers silent. Confirm TikTok's selected input and compare the saved recording's audio to the source before considering any future LIVE verification. This uses Android's physical audio-input route and avoids an unsupported cross-process native hook. It is conditional: TikTok may ignore or process that input differently. If no interface is available, there is no evidence-backed native bridge change to make; keep the current TikTok/module untouched.

## Evidence files

- Phase 23 report: `test-results/phase23/PHASE23_REPORT.md`
- Audio capture call graph: `test-results/phase23/audio-record-thread.txt`
- Encoder call graph: `test-results/phase23/rx-audio-encoder-thread.txt`
- Full profile call graph: `test-results/phase23/live-callgraph.txt`
- Profile, maps, audio service and bounded logs: `.tools/phase23/live-20261025T022842Z/`
- Static callback candidate and pinned ELF identity: `test-results/phase16/PHASE16_REPORT.md`, `test-results/phase16/lyrax-targeted.json`
- Owned-hook/lifetime failure evidence: `test-results/phase20/PHASE20_REPORT.md`, `test-results/phase20/target-profile.json`, `test-results/phase20/registration-disassembly.txt`

**Final status:** OpenSL ES active capture **CONFIRMED**; encoder thread activity **CONFIRMED**; callback candidate `0x6be020` runtime execution **UNVERIFIED**; PCM pointer/length/ownership and capture-to-encoder handoff **UNKNOWN**; receiver motion **PASS by user report**; receiver MP4 soundtrack **FAIL by user report**; native TikTok PCM bridge readiness **NO-GO**.
