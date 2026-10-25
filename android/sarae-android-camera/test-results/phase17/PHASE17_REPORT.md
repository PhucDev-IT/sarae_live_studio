# Phase 17 — Native Observation Infrastructure

**PASS: JNI loading and owned-recorder callback observation under LSPatch 1.2/487.**

**TikTok-native interception / actual LIVE callback remain unverified and NO-GO.** The loading test used the existing Phase 15 harness patched with the same pinned loader. TikTok itself was not patched, reinstalled, restarted, cleared or used for LIVE. Its Phase 14 module/account were preserved.

## Gates

| Gate | Result | Evidence |
|---|---|---|
| Native library load through LSPatch module | **PASS in owned lab process** | Module APK → owned private code cache → System.load → JNI_OnLoad → valid C API; exact packaged/loaded/mapped SO hash matches |
| Initialization and thread identity | **PASS** | PID **32225**, module attach TID 32225, native load/worker TID **32340**; callback TID **32422**; no callback thread change |
| Controlled pass-through forwarding | **PASS** | **994 entries → 994 original calls → 994 recording buffers**, contiguous sequence; original function, queue and forwarded context match registration |
| No recorded-content modification | **PASS** | BASELINE and OBSERVED each have byte-identical original-mic/delivered PCM; their AAC/MP4 outputs match corresponding mic waveforms 0.996359 / **0.996559** |
| Queue/lifecycle preservation | **PASS in short test** | Both original FIFO buffer identities preserved; zero enqueue failures, capture drops, telemetry drops, queue mismatch, concurrent or late callback |
| STOP / DESTROY cleanup | **PASS** | Counts stable over separate **200 ms** post-STOP and post-DESTROY windows; in-flight=0; relay retired then freed; allocated/active relays=0 |
| Error reporting | **PASS for controlled lifecycle rejection** | JNI reports expected shutdown refusal while a relay is allocated; cleanup succeeds after release. Missing/corrupt SO load failure was not deliberately induced |
| Current installed liblyrax identity | **PASS: identity only** | Fresh read-only ARM64 split pull matches SHA256, Build ID and all **8** Phase 16 instruction words |
| Load/observe inside TikTok process | **NOT TESTED** | Current TikTok module left unchanged; no target observer installed |
| Actual selected LIVE backend / PCM contract | **UNKNOWN / NO-GO** | Loading an owned SO and forwarding an owned callback do not establish foreign recorder access |

## Implementation and loading mechanism

- Added `:native-observer`: small ARM64 **C11** library with JNI_OnLoad, explicit C API, forwarding relay, bounded metadata and lifecycle counters. No audio object/decoder, native symbol interception or PCM access in this component.
- Added `:native-observer-module`: minimal legacy Xposed module, allowlisted **only** to debuggable `co.sarae.camera.nativeaudiotest` main process. It hooks the project's dedicated `observationApi` method; no TikTok or system audio hook.
- Existing `:native-audio-adapter` gains optional callback registration through the explicit C API. `createObserved` permits only original MICROPHONE mode; standard Phase 15 modes retain their original path. No new recorder/decoder implementation.
- Existing `RecorderActivity` gains a diagnostic run flag for BASELINE and OBSERVED using the same recorder and AAC writer. No Camera2/EGL/Video Engine or PCM decoder changes; runtime decoder-creation count **0** in this run.
- Loader supplies module path `cache/lspatch/co.sarae.camera.observationmodule/163144652.apk`. Actual class loader is `VectorModuleClassLoader` with module ARM64 APK library directory. This test deliberately uses an explicit, hash-verified `System.load` path; it does not claim automatic System.loadLibrary or native_init interception support.
- Observer SO is **absent from the unpatched host APK** and present in the embedded module. The package's recorder library accepts a versioned, size-checked API pointer explicitly returned from that module, demonstrating execution across the controlled module/host boundary without symbol lookup.
- C library depends on libc/libm/libdl, without another libc++ runtime or OpenSL library creation calls. Build uses JDK 21, SDK 34, NDK 28.2.13676358, CMake 3.22.1, ARM64. Only new observation modules, existing native harness and required audio dependencies were built.

Actual native load path ends in:
`code_cache/sarae-phase17/f37ac8354e5bd50b51634b3a508d616841c88d9b88c28b0be80548eb4104b269/libsarae_native_observer.so`.

Java reports `/data/user/0/...`, whereas `/proc/PID/maps` reports the `/data/data/...` user-0 alias; offline analysis normalizes this alias and retains both paths. The same file has an executable mapping, JNI PID/TID results and matching device hash. No additional test was run to address the pathname difference.

## Real-time behavior and measurements

Observer registration uses a relay context; the original callback still receives its **original** context and the received queue exactly once. The original callback alone owns PCM snapshots and re-enqueue. No global hook, recorder search, guessed context cast or shared-vtable modification.

Observer callback has no decoder, file IO, JNI, heap allocation, formatting, logger, mutex or spin-wait. Metadata storage is **256 × 80 = 20,480 bytes** plus fixed counters. A full ring loses telemetry only; a concurrent producer skips telemetry publication and still forwards the original call. Both conditions are counted. Existing Phase 15 capture storage remains bounded and separate.

| Measurement | BASELINE | OBSERVED |
|---|---:|---:|
| Requested recording window | 10 s | 10 s |
| Completed original recorder blocks | 992 | 994 |
| PCM duration | 9.920 s | 9.940 s |
| Encoded MP4 decoded duration | 9.962667 s | 9.984000 s |
| Maximum original callback interval | 12.396 ms | **14.889 ms** |
| Original-mic → encoded waveform correlation | 0.996359 | **0.996559** |
| Measured AAC alignment lag | 42.667 ms | 42.667 ms |
| PCM mismatch / native capture drops / enqueue failures | 0 / 0 / 0 | 0 / 0 / 0 |

The two recordings contain different ambient input, so their PCM is not compared against each other. Each delivered file is compared to its own original mic snapshot. No soundtrack replacement is enabled. MP4s are the existing harness's **audio-only** AAC/MP4 output; this is not a new A/V synchronization test.

For 994 observed callbacks:

| Instrumented timing | Median | p99 | Maximum |
|---|---:|---:|---:|
| Original callback inclusive duration | 14.010 μs | 22.737 μs | 55.469 μs |
| Additional observer work | **2.291 μs** | **3.809 μs** | **39.010 μs** |
| Observer section including original callback | 16.354 μs | 26.316 μs | 57.396 μs |
| Event entry → worker receipt | 1.148 ms | 2.635 ms | 12.465 ms |

Additional work is measured within each wrapper invocation as entry-to-last-timestamp minus original-call duration. It includes telemetry copy and clock/thread measurements, but excludes final ring-publication release-store, flag-clear, in-flight decrement and return. These are observed wall times including scheduling, **not a hard real-time upper bound**. Baseline/observed runs do not establish equal scheduler conditions or production performance.

After STOP and after destroying the original OpenSL recorder, entries/forwarded counts remain 994, in-flight=0 and late=0 over the two 200 ms windows. Recorder Destroy synchronizes dispatch before relay retirement. Metadata remains temporarily available for verification, then is freed. One intentional native shutdown rejection increments the error counter; after release, shutdown succeeds. Cleanup does **not** dlclose a JNI library still owned by its class loader: it remains mapped until process exit. No observed Java/native fatal error in bounded harness log/crash buffer; no endurance certification.

## Target identity and remaining integration work

Freshly copied **installed** TikTok ARM64 split contains liblyrax with:

- SHA256 **`9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`**.
- Build ID **`800d53bec62ddf0a1774842f5a16e1693ab7f0de`**.
- All 8 Phase 16 instruction words unchanged, including registration call site **0x6bdab4**, callback candidate **0x6be020**, outlined thunk **0xdde074**.

**Established:** a project-owned JNI library can load and execute from an embedded legacy module under this pinned LSPatch lab configuration; an explicitly supplied owned callback can be observed without PCM changes in the measured test.

**Still not established:** loading this component inside TikTok's own class-loader/linker environment; a target-specific interception/trampoline mechanism; runtime invocation of the static candidate; actual recorder/queue/context, client PCM size/format, callback ordering/reentrancy, ownership through enqueue/completion and capture-to-encoder handoff. The static code/ELF match is not selected-backend evidence.

The API pointer in this test is supplied cooperatively by the owned recorder. JNI loading alone cannot discover or intercept liblyrax's recorder. The adapter cannot be pointed at an RVA or unknown context. Its test buffer size/FIFO must not be assumed for TikTok.

**Next step, without LIVE:** prepare a separately reviewed target-scoped **observation-only** binding/loader plan using this verified component, with exact ELF/load-bias/fingerprint guards, original callback preservation and a safe rollback procedure. Validate any new interception mechanism before target deployment; do not silently turn this callback-forwarding API into a global native hook. No target deployment or LIVE request is justified by Phase 17 alone. Actual LIVE callback selection remains UNKNOWN, soundtrack/native PCM integration NO-GO.

## Device budget and preservation

**29.688 seconds total device work**: initial conservative PID guard 0.906 s, separately journaled read-only audio checks 0.266 s, then 28.516 s for guarded install/run/collection/cleanup. Build, patching and offline waveform/ELF analysis excluded. First guard found background TikTok PID 25240 and stopped before install; HOME plus empty current recording configurations / no input thread were then verified before running the independent recorder. Historical AudioService logs were not mistaken for current recording.

- TikTok PID **25240 before/after**, untouched. No force-stop, launch, reinstall, clear-data, cache/module update, account action, LIVE or post.
- Cached Phase 14 module before/after **`53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`**.
- Base APK before/after **`4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`**.
- Installed/updated **only** the owned native-audio test harness. It returned HOME after recording/observer cleanup. No further device work after collection.
- Previous owned harness APK backed up as `.tools/phase17/rollback-own-harness.apk`, SHA256 **`512a6d2a97b80931b4d084b9dd2952a0585b44df3af7b5fc4081286519749167`**, matching the verified Phase 15 artifact. It was not restored automatically. Optional future rollback affects only this owned test package.
- **151 prior evidence files** verified unchanged: Phase 12 104, Phase 15 37, Phase 16 10. Raw Phase 15 recordings remain preserved.

## Artifacts and reproduction

| Artifact | SHA256 |
|---|---|
| Pinned loader JAR | `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153` |
| Observation module APK | `dcf6896f424f807781691349743f4a4cf8fe3c6cca323ea4ca5c52805aefcdec` |
| Observer ARM64 SO | `f37ac8354e5bd50b51634b3a508d616841c88d9b88c28b0be80548eb4104b269` |
| Patched owned harness | `1461df63f9e391d826be2e87c002463e2aef8a35f5be13efba399ea292b34f36` |
| BASELINE.mp4 | `72d9d3519f96fa442cea7adf8ad82cfdb5df8e86d8404cb56613654a6b90dcce` |
| OBSERVED.mp4 | `11a13fe84658a49618711cbcea231e0ae8df703e159372e4910c7ce1d3c2b061` |

LSPatch config: 1.2/487, `sigBypassLevel=0`, no manager/injectDex. No signature/integrity workaround. `prepare_phase17.py` verifies locally generated nested-APK content hashes and signing; the pinned loader's intentional overlapping origin ZIP entry is handled only for the exact known-size/source-hash entry.

Source/API contract: `native-observer/README.md`, `observer_api.h`, `observer.c`, `ObservationEntry.kt`. Scripts: `prepare_phase17.py`, `phase17_device.py`, `analyze_phase17.py`. Build/static/Python compile checks PASS; no fresh unrelated Camera2 or Phase 15 mode regressions.

Diagnostics: `analysis.json`, `events.jsonl`, `module-events.jsonl`, `device-state.json`, `adb.jsonl`, `artifacts.json`, ELF/Build ID outputs, encoded ffprobe JSON and source/evidence hashes. Encoded/raw PCM and maps/logcat remain in ignored `.tools/phase17/run-101156/` and `.tools/phase17/`; preserve these files deliberately when moving the checkout.
