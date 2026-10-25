# Phase 19 — LSPatch Native Hook Validation

2026-10-24; branch `feature/tools-android`; Samsung SM-G996U/API34/ARM64/non-root.

**Pinned native API and owned-function interception: PASS. Target observer deployment: NO-GO pending a verified target binding.** No LIVE, liblyrax interception or PCM replacement occurred. TikTok stayed PID **8226**, with its Phase18 module/base unchanged.

## Gates

| Gate | Result | Evidence |
|---|---|---|
| Native API availability | **PASS in owned harness** | assets/native_init recognized, native_init called once, version **2**, non-null hook/unhook pointers in executable framework mapping |
| Owned-function interception | **PASS** | One hook install; **1,122** replacement entries and **1,122** original trampoline calls, exact arguments/results |
| Trampoline / reentrancy | **PASS for tested ARM64 prologue** | 32 depth3 recursive calls → **128** entries, peak nested in-flight **4**; no result mismatch |
| Callback lifecycle / original audio | **PASS in short owned run** | **994** recorder callbacks/forwards/probe calls, contiguous sequence/FIFO, no duplicates/drops/late callbacks, PCM unchanged, saved AAC/MP4 verified |
| Unhook / cleanup | **PASS for serialized control** | After STOP/DESTROY, one successful unhook, first16 original entry bytes restored, 128 original calls afterward and zero further replacement entries |
| Safe rejection paths | **PASS for tested guards only** | Invalid-candidate sentinel rejects before framework call; active unhook refuses with -16 while hook remains installed |
| Underlying hook-engine failure handling | **UNVERIFIED** | No deliberately induced Dobby installation/partial-install failure; sentinel is not such a test |
| Target observer readiness | **NO-GO for deployment / LIVE request** | No target callback ABI/prologue binding or lifecycle validated; **GO for preparing that separately reviewed implementation** |

## Scope and implementation

Reused the existing Phase15 OpenSL recorder, Phase17 cooperative callback observer and existing AAC writer. No second recorder, audio decoder or Video Engine was added. The PCM engine, Camera2/EGL sources and TikTok controller/module were not rebuilt or deployed.

A separate **libsarae_hook_validation.so** in the existing native-observer build receives the pinned framework API through the owned-harness module's **assets/native_init**. `ObservationEntry` remains allowlisted to the debuggable `co.sarae.camera.nativeaudiotest` main process. The original observer SO is byte-identical to Phase18.

The load notification matches only **libsarae_native_audio.so**, records its handle and returns. It does no dlsym, nested dlopen, IO, allocation, JNI or hook installation under the framework registry/loader lock. Symbol resolution and installation run later on the test worker and accept only the owned exported function and owned harness pathname.

Exactly one function is inline-hooked: **sarae_owned_probe_v19(queue, context, sequence, depth)**. It is a metadata/return-value probe called from the existing recorder callback; it never dereferences queue/context or PCM. The recorder callback itself retains its Phase17 cooperative wrapper, PCM snapshots and Enqueue behavior. This is **not** direct inline interception of an OpenSL callback, registration callsite or liblyrax function.

The replacement forwards original arguments once through the supplied trampoline and returns the unmodified original result. Recursive control tests deliberately re-enter the patched function through an indirect call. Its ordinary stack prologue exercises the installed ARM64 trampoline, not all possible PC-relative/PAC/BTI prologues.

The callback adds fixed counters and a bounded **256×88-byte** metadata ring, with one non-blocking atomic-flag publication attempt. No callback JNI, file IO, formatting, logger, decoder, allocation, mutex or spin-wait. Metadata loss cannot suppress the original invocation; no telemetry loss was observed. The worker records native timestamps and receipt times separately.

## Pinned binary compatibility

LSPatch JAR SHA256 remains:
`5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153`.

Patch configuration: 1.2/487 debug, core7306/canary-3106, core commit
`e00c5c5038bbcc14e0b382ab893301d6993e6989`, sigBypassLevel=0, no manager/injectDex. No signature/integrity bypass.

ARM64 loader payload SHA256:
`dec308c80aa22f6001e4798bb7507055ef955181c52a82771bc02c77140753d8`.

That loader payload is byte-identical across the freshly backed-up previously installed Phase17 owned harness, this Phase19 patched harness, and the preserved TikTok base whose SHA matches the installed base before/after. Runtime native_init/version2, hook/unhook and Dobby debug events establish actual availability in the owned process. Native API initialization/hook use **inside TikTok** was not tested by this phase.

Owned PID **10848**, worker TID **10914**, recorder callback TID **11169**. Original target **0x73833c5030** is executable inside the owned original APK; original trampoline is executable in an anonymous allocation. Framework hook/unhook pointers map to the patched owned base APK. Full maps/pointers and initialization metadata are retained in analysis.json/runtime-excerpts.json.

Primary pinned contract references: [native API layout](https://github.com/JingMatrix/Vector/blob/e00c5c5038bbcc14e0b382ab893301d6993e6989/native/include/core/native_api.h), [implementation](https://github.com/JingMatrix/Vector/blob/e00c5c5038bbcc14e0b382ab893301d6993e6989/native/src/core/native_api.cpp), [module entry asset loading](https://github.com/JingMatrix/LSPatch/blob/0dc50f42503711b14f5e2bb217c2bdd6321ce5be/share/android/src/main/java/org/lsposed/lspatch/util/ModuleLoader.java). Public field declarations were adapted; no foreign recorder implementation copied.

## Callback, output and timing measurements

Two recordings, requested 10 seconds each: BASELINE and OBSERVED. No soundtrack replacement or decoder was active. Each recording is compared with its **own** original mic data, not with the different ambient input of the other recording.

| Measurement | BASELINE | OBSERVED |
|---|---:|---:|
| Completed callback blocks | 992 | **994** |
| Delivered PCM duration | 9.920 s | 9.940 s |
| Decoded saved AAC/MP4 duration | 9.962667 s | 9.984000 s |
| Original mic / delivered PCM | Byte-identical | **Byte-identical** |
| Saved audio / original mic correlation | 0.996278 | **0.998246** |
| AAC alignment lag | 42.667 ms | 42.667 ms |
| Maximum original callback interval | 12.332083 ms | **12.230886 ms** |
| Callback work median / p99 / max | 9.479 / 15.634 / 19.323 µs | **24.427 / 40.424 / 97.656 µs** |

Observed native probe-entry cadence: median **9.769948 ms**, p99 **12.157848 ms**, max **12.231979 ms**. There are 994 capture-thread probe entries plus 128 control-recursion entries. Original function invocation count increases by exactly **1,122**, independent of replacement counters; all computed return values match. No callback telemetry loss, queue mismatch, enqueue failure, PCM mismatch or late invocation was observed.

Instrumented native wrapper additional work: median **9.115 µs**, p99 **15.577640 µs**, max **27.136 µs**. Original-trampoline inclusive section: median **1.459 µs**, p99 **3.594 µs**, max **3.958 µs**.

**Timing limits:** wrapper measurement starts after entry counters and ends before final publication/decrement/return; it excludes entry/return branch cost and excludes work attributed to the original trampoline. It is not total native-hook overhead or a realtime bound. The full callback timings include the probe and current instrumentation; separate runs do not control scheduler conditions, so their difference is not an isolated per-call hook benchmark. Debug builds, 20 seconds of capture and 994 callback samples do not certify production timing.

After STOP and DESTROY, the Phase17 callback counters remain stable across separate **200 ms** windows; the native hook counters likewise match after STOP and after DESTROY, with in-flight=0. Cooperative relay is retired/freed and shutdown succeeds. Then unhook restores the entry; 128 post-unhook original calls retain correct results and do not invoke the replacement.

The framework API exposes no notification-unregister operation. Its dlopen notification registry and library remain valid/mapped for the owned process lifetime; no unsafe dlclose or dangling callback was attempted. This is distinct from successfully removing the owned function hook and freeing recorder/relay resources. No unexpected Java/native fatal error in bounded log/crash evidence.

## Phase18 176 ms gap: evidence, not an EGL fix

Existing logs only; no camera retest. Detailed timeline: **phase18-gap-analysis.json / phase18-gap-timeline.json**.

| Interval | Measured |
|---|---:|
| Output event emission gap | **176.257396 ms** |
| Renderer post-swap gap field | **176.251042 ms** |
| Tick-start spacing | 33.733333 ms |
| Reported tick lateness | 0 ms |
| Active latch scope | **138.032239 ms** |
| Draw | 0.456146 ms |
| eglSwapBuffers | 4.942656 ms |
| Tick start → presentation | 143.453334 ms |
| Previous output logging scope | 0.297292 ms |

**CONFIRMED location:** most wall time occurs inside the active **latch()** scope. Five decoder frame-rendered events and six decoder ticks were emitted during this slow renderer tick; maximum decoder-worker duration was 5.013594 ms. Their renderer-handler receipts were delayed by up to **142.857917 ms**. Treating receipt time as decode time would incorrectly attribute the stall to decoding. There was no source switch in this window.

The observer metadata worker completed **16.210074 seconds before** the slow tick began; its startup does not overlap this event. The trace does not establish a counterfactual bound on all observer overhead.

**UNKNOWN root cause within that scope:** latch includes updateTexImage, transform/timestamp access and source-latched event emission. GPU/fence/driver waiting, logging inside that scope and thread descheduling cannot be separated with the available sub-operation/off-CPU evidence. A timely tick start does not exclude descheduling inside latch. The 100 ms target still fails. **No EGL/decoder/Video Engine correction was justified or implemented.** A future narrow trace should distinguish these sub-operations and scheduler/fence waits, rather than changing thresholds or rewriting the renderer.

## Preservation, budget and artifacts

**28.079 seconds cumulative device work**, including the initial locked-screen guard and final collection/HOME. Build, patching, waveform analysis and this offline timeline analysis excluded. No additional device test round or camera regression run. No LIVE, post, account action or TikTok restart/force-stop/install/cache write.

TikTok before/after: PID **8226**; base SHA256
`4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`;
Phase18 module SHA256
`66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120`.

Only the owned harness was updated in place. Fresh previous APK backup:
`.tools/phase19/rollback-own-harness.apk`, SHA256
`1461df63f9e391d826be2e87c002463e2aef8a35f5be13efba399ea292b34f36`.
No rollback was required. Future optional rollback affects only this owned package,
requires HOME/no competing recording and a new explicit verification budget; never
install that artifact over TikTok. No reset/clear-data is needed.

| New artifact | SHA256 |
|---|---|
| Owned observation module | `214fc1ab10611fa1eaa597768facba0f0305d28c78deeeff0fc3543f8a160bc9` |
| Patched owned harness | `3fd249790f094b075c3bc48cbff678f0f394dee4685787769783fe81e4e7250d` |
| Native hook validation SO | `8d71cdda8590ffdc44d5a1e440b44cd4f56c210d0fc43fe752470d325b870077` |
| Unchanged Phase18 cooperative observer SO | `e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881` |

Build/static/Python checks pass. Original Phase12–18 evidence and Phase18 raw artifacts verified unchanged; expected Phase19 harness/module source changes are separately hashed. Raw PCM, BASELINE.mp4, OBSERVED.mp4, maps/logcat, APKs and build outputs are in ignored **.tools/phase19/run-112401/** and **.tools/phase19/**. Preserve these with the report. Machine results: analysis.json, controlled-audio-analysis.json, runtime-excerpts.json, native-hook-events.jsonl, device-state.json, adb.jsonl and hash manifests.

## Next action

Use **TARGET_OBSERVER_PLAN.md** to prepare a target-scoped binding: exact liblyrax identity, event-driven load detection, worker-side ELF load-bias/executable fingerprint checks, independently verified required prologue relocation and pass-through callback contract. Review real engine-failure recovery and notification lifetime before deployment. Do not hook a mid-function BL or shared vtable thunk as a workaround.

Actual liblyrax registration, selected LIVE backend, callback buffer ownership and encoder handoff remain UNKNOWN. Phase19 establishes the pinned interception infrastructure on owned code; it does not establish TikTok native audio or LIVE compatibility. No further LIVE is requested.
