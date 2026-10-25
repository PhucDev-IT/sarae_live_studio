# Phase 21 — TikTok LIVE audio diagnosis readiness

2026-10-25, branch `feature/tools-android`. **Decision: NO-GO for a real LIVE diagnostic.** No LIVE, TikTok launch/restart, APK/module update, capture hook, microphone recording, or account action was performed.

## Deployed identity and preservation

Read-only ADB checks on SM-G996U (Android 14 lab device) found `com.ss.android.ugc.trill` 47.4.3, `DEBUGGABLE`, PID **24699** while Android Settings was foreground. The installed base APK SHA256 is `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`. The installed LSPatch cache module SHA256 is `66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120`, matching the preserved Phase 18 rollback APK at `.tools/phase20/tiktok-phase18-rollback.apk`. The extracted `libsarae_native_observer.so` SHA256 is `e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881`; its executable mapping was present in the current PID's `/proc/24699/maps`. These checks establish current binary identity and native load, not callback interception. The current observer is the Phase 18 load-only component; the Phase 14 Camera2 fixes remain packaged. The rollback artifact was checked but not deployed.

## Reused evidence and remaining native blocker

| Gate | Evidence-based status |
| --- | --- |
| Controlled OpenSL recording, PCM replacement and saved encoded output | **PASS in Sarae-owned harness** (Phase 15); no TikTok LIVE implication. |
| Native observer load and exactly-once forwarding | **PASS in owned harness** (Phases 17, 19–20); Phase 18 load-only observer maps in TikTok. |
| Dobby API, first 12-byte ARM64 prologue/trampoline, rejection behavior | **PASS on owned code** (Phases 19–20); longer patch plans and target callback execution unverified. |
| Exact target identity | Static liblyrax SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`, Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`, candidate callback RVA `0x6be020`; **candidate only**, not a proven LIVE backend. |
| Safe foreign-library lifetime and binding | **FAIL/unresolved**: Phase 20 observer-context `RTLD_NOLOAD` returned `-43` for an already mapped owned ELF. A cooperative owner handle solved only the owned case. The exact private linker rule was not proven; TikTok offers no validated cooperative handle. |
| Attach before first target callback and preserve in-flight execution | **UNVERIFIED**. Deferred library-load notification does not guarantee a pre-capture attachment point. |
| TikTok LIVE native API, callback, PCM ownership and format | **UNKNOWN**. Phase 12 LIVE received mic audio; the Phase 11 Java `AudioRecord.read(ByteBuffer,int)` recording hook did not establish LIVE capture. AudioFlinger 48 kHz stereo S16 MIC data does not identify the client API. |

The existing Phase 18 TikTok process was not modified. No broad OpenSL, AAudio or native AudioRecord hook was installed. An owned callback result cannot make an unknown third-party callback safe to hook.

## New, non-invasive profiling check

Android's built-in `/system/bin/simpleperf` and `run-as com.ss.android.ugc.trill` are available. A direct PID profiling attempt was denied opening `cpu-cycles` (`Permission denied`). The documented debug-app route, `simpleperf record --app com.ss.android.ugc.trill -e task-clock -g --duration 2`, completed in **2.02253 s** without root, but recorded **0 samples** while TikTok was in the background. Background inactivity is a plausible explanation, not a proven cause. This validates command availability, **not native call-stack capture or any LIVE audio backend**. The temporary `/data/local/tmp/sarae-phase21-perf.data` was removed after recording its result. No microphone PCM was saved. Total active ADB verification was under one minute, far below the five-minute budget; the only timed profiler window was 2.02253 s.

A positive future call-stack sample could distinguish an active native route. Zero samples cannot rule out OpenSL, AAudio or native AudioRecord, nor can sampled stacks alone establish PCM buffer ownership or callback registration. Profiling is an observation candidate rather than a safe substitute for the failed target binding guard.

## GO/NO-GO and next action

**NO-GO.** The currently deployed component cannot observe the candidate callback, and the Phase 20 foreign-library lifetime/pre-capture attachment guards remain unresolved. This phase implemented and deployed **no code changes**; the installed TikTok and account data remain intact. Actual LIVE soundtrack replacement is neither active nor verified. Camera2 preview results do not certify LIVE video or audio.

**One concrete next action:** validate the `simpleperf --app ... -g` path against the **already existing Sarae-owned OpenSL harness while its known callback is active**, requiring a positive native call-stack sample and bounded overhead before considering this as an observation-only option for a separately authorized LIVE diagnostic. If that fails, do not use a live session to discover profiler behavior. No LIVE is requested now.

Prior evidence: [Phase 20 report](../phase20/PHASE20_REPORT.md), [LIVE preparation gates](../phase20/LIVE_AUDIO_PREPARATION.md), [Phase 18 report](../phase18/PHASE18_REPORT.md), [Phase 12 LIVE report](../phase12/PHASE12_REPORT.md).
