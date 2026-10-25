# Phase 18 — TikTok Native Audio Observer

Date: 2026-10-24. Branch: `feature/tools-android`.

**Native loading in the actual TikTok main process: PASS. Target callback interception: UNVERIFIED / NO-GO.**

This execution deployed a load-and-metadata component, not a liblyrax callback interceptor. No PCM replacement or real LIVE was performed. TikTok installation and account data were preserved.

## Results

| Gate | Result | Evidence / scope |
|---|---|---|
| Native observer loaded in TikTok | **PASS** | Main PID **8226**, JNI load/worker TID **8315**, packaged/device SO hash match, executable mapping, JNI API and thread identity validated |
| Target-scoped interception feasibility | **UNVERIFIED** | Pinned loader has a possible native hook API, but a target trampoline/registration binding has not been independently validated or deployed |
| Actual callback registration observed | **NO — not instrumented** | Registration and callback counts are deliberately `null`; actual activity remains **UNKNOWN**, not proven inactive |
| Camera Bridge regression | **PASS for short preview/lifecycle** | One SessionConfiguration session, successful request remapping/submission, moving upright MP4 in two actual TikTok screenshots, capture results, clean HOME close |
| Existing <100 ms cadence target | **FAIL in this observation** | Maximum **176.257396 ms**, one interval ≥100 ms; threshold unchanged |
| Unexpected Java/native fatal errors | None observed | Bounded PID log and empty crash buffer; no endurance claim |
| Readiness for LIVE callback observation / native PCM injection | **NO-GO** | Target interception and selected audio backend/ownership are not established |

## Deployment and rollback

The first read-only guard found another foreground app and stopped. A later check found Keyguard and stopped. After the user returned HOME/unlocked, HOME, no active camera clients, no AudioFlinger recording thread and no current recording configurations were checked before stopping idle TikTok.

A **fresh device copy** of the Phase 14 cached module was saved and hashed before any replacement. Only `cache/lspatch/co.sarae.camera.poc/183496786.apk` was updated through a verified temporary file and atomic rename. Its same-directory `.phase18-rollback` backup is preserved. No APK installation, uninstall, clear-data, account action or broadcasting action occurred.

| Artifact | SHA256 |
|---|---|
| Original Phase 14 module / exact rollback | `53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351` |
| Deployed Phase 18 module | `66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120` |
| Packaged and loaded ARM64 observer SO | `e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881` |
| Installed TikTok base, unchanged before/after | `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b` |
| Pinned LSPatch 1.2/487 debug JAR | `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153` |

Local exact rollback: `.tools/phase18/rollback-module.apk`; second preserved copy: `.tools/phase18/expected-rollback-module.apk`. See **ROLLBACK.md** for idle-state requirements and restoration procedure. No rollback was needed. Phase 18 module remains in cache; cache eviction can restore the originally embedded Phase 11 module, so future runs must check hashes again.

A/B media hashes and `sarae-render.json` match before/after. Configuration stays FIT, automatic camera orientation, no rotation override, no mirroring. Installed base/splits were not patched or replaced. Final camera clients are empty and the device was returned HOME.

## Native load evidence

`HookEntry` retains the debug-lab/main-process allowlist and existing Camera2/audio composition. It obtains the module APK path through the standard legacy initialization callback. A worker after Application.attach extracts only the packaged observer into its private, hash-addressed code cache, checks bytes and uses `System.load`.

Observed module loader is **VectorModuleClassLoader**; host is **PathClassLoader** with the original/split APK paths. The load succeeded in that actual namespace configuration. Native dependency/ABI details are in `observer-elf.txt`; candidate signing verification passed.

Observed Java path:

`/data/user/0/com.ss.android.ugc.trill/code_cache/sarae-native-observer/e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881/libsarae_native_observer.so`

The same file appears under the `/data/data/` user-0 alias in `/proc/self/maps`, including executable range **730c597000–730c59a000 r-xp**. Device file SHA matches the packaged SO. JNI returns Sarae API magic/version **1**, PID 8226 and TID 8315. This Sarae API is distinct from the framework native hook API discussed below.

Four metadata snapshots at approximately 0/5/15/30 seconds and `observation_worker_complete` were collected. Allocated/active relays, retirements, failures and shutdown counters remain zero. The worker finishes; no recorder/relay was created and the JNI library intentionally stays mapped until process exit. No forced dlclose.

The observer's Phase 17 callback implementation is an unchanged byte prefix. The sole added C entry is read-only JNI diagnostics calling the existing `global_stats`. File IO, JNI and JSON logging occur on the worker, outside any recording callback.

## Target identity and interception blocker

Freshly pulled installed ARM64 split contains liblyrax with:

- SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`.
- Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`.
- All **8** Phase 16 registration/thunk instruction words match. Whole-library identity also protects the callback candidate bytes.
- Static registration call site **0x6bdab4**, callback candidate **0x6be020**, outlined thunk **0xdde074** remain static candidates only.

**liblyrax was absent from the observed main-process maps**, both the timed native snapshots and later host snapshots. This does not establish what another process or LIVE mode loads. No runtime load bias/candidate address, registration success, queue/context identity or callback contract was obtained.

Source review found a possible framework mechanism: pinned LSPatch commit `0dc50f42503711b14f5e2bb217c2bdd6321ce5be` loads legacy module native entry names from `assets/native_init`; its core gitlink `e00c5c5038bbcc14e0b382ab893301d6993e6989` exposes NativeAPIEntries version **2**, backed by Dobby. Registering that mechanism also enables process-wide dlopen instrumentation. Primary references:

- [Pinned ModuleLoader](https://github.com/JingMatrix/LSPatch/blob/0dc50f42503711b14f5e2bb217c2bdd6321ce5be/share/android/src/main/java/org/lsposed/lspatch/util/ModuleLoader.java)
- [Pinned native API contract](https://github.com/JingMatrix/Vector/blob/e00c5c5038bbcc14e0b382ab893301d6993e6989/native/include/core/native_api.h)
- [Pinned implementation](https://github.com/JingMatrix/Vector/blob/e00c5c5038bbcc14e0b382ab893301d6993e6989/native/src/core/native_api.cpp)

This source establishes a possible implementation route, **not tested availability or trampoline correctness in the installed binary**. The Phase 18 APK has no native_init asset/export or Dobby hook call. No new native interception mechanism was deployed. Phase 17 cooperative forwarding requires the owned recorder to supply its original callback explicitly; it cannot discover or bind a foreign recorder from an RVA.

Consequently registration activity is **UNKNOWN**. Neither missing events nor an unloaded library are a fabricated callback-observation PASS. Investigation stops here rather than adding speculative global audio hooks or dereferencing unknown PCM.

## Preview and diagnostics

One existing TikTok CAMERA/60s preview was opened; recording and LIVE buttons were untouched. Camera **1**, sensor orientation **270**, front-facing; original Surface **138636764**, dummy **17626595**, CameraDevice **100797408**, session generation **1**. Two request submissions returned `error=none`; prebuilt Surface remapping preserved the configured mapping. Last sampled framework capture result reports count **870**, frame **869**. This is framework callback evidence, not an encoder or receiver result.

Renderer produced **891 outputs** over **29.855634 s**, measured **29.8101 FPS**, zero non-increasing output PTS, p99 output event gap **37.8610 ms**, maximum **176.257396 ms**. These are renderer event emission intervals. The stall's cause was not established or modified in this phase. Camera regression PASS covers functional preview and close only; cadence remains a separate failure.

`preview.png` and `preview-later.png` show upright footage in the actual TikTok preview with visibly different poses/frames. Existing AudioRecordBridge attaches in **MICROPHONE** mode, `nativePathsHooked=false`. Existing audio engine prepares soundtrack PCM on video commits; the observation component creates no decoder or recorder and changes no audio mode. Native/encoded audio compatibility was not retested. The fresh module includes the already verified Phase 15 PCM-ring tolerance fix from current source, rather than asserting all audio classes are byte-identical to the old Phase 14 APK.

No fatal Java/native error appears in collected bounded logs/crash buffer. A separate `libxquic.so` missing-library diagnostic appears in TikTok's log; no observer load failure or demonstrated causal link was observed. No integrity/security bypass was added.

## Budget, changes and evidence

**64.686 seconds total device verification**, including failed read-only guards, inventory, deployment, preview and cleanup. Deployment-to-finish elapsed wall time is conservatively counted, including host/tool pauses. Builds/static analysis/user waiting before deployment are excluded. No further device round was run.

Changed components: app observer dependency, HookEntry initialization, new TargetNativeObservation/NativeLibraryLoader, read-only NativeObservation diagnostics JNI, and Phase 18 host scripts. Camera2/EGL/Video Engine and PCM decoder sources were not edited for Phase 18; no second recorder/decoder was introduced. Prior owned test/module tentative edits were reverted before target deployment. Build and candidate/static analysis PASS; Phase 17 callback-forwarding results are reused, not represented as target interception.

Prior manifests checked **285 entries** across Phases 12/14/15/16/17. Only the intended Phase 14 HookEntry source entry differs; no unexpected source/evidence changes. Original Phase 12 evidence remains intact.

Machine evidence: `analysis.json`, `runtime-excerpts.json`, `candidate.json`, `target-identity.json`, `loader-source-review.json`, `adb.jsonl`, `device-state.json`, final native/bridge/audio JSONL, signing/ELF outputs and hash manifests. Raw screenshots, complete maps/logcat, pulled split and APKs are in ignored `.tools/phase18`; preserve that directory with the report.

## Exact next step / GO–NO-GO

**GO:** this narrowly scoped private-code-cache JNI loading method works in the actual TikTok lab main process.

**NO-GO for requesting LIVE observation or native PCM injection.** First independently validate the pinned framework native API and a narrowly scoped binding against an owned function/callback using the existing Phase 17 recorder: original arguments/return/call count, ARM64 relocation and trampoline behavior, reentrancy, STOP/DESTROY and safe unhook/late-callback behavior. Review the dlopen listener scope and failure/rollback behavior before target deployment. No new recorder is required.

Only after that validation may a target-specific observation plan calculate verified liblyrax load bias, fingerprint executable addresses and record registration metadata without touching PCM. The selected LIVE backend, client buffer contract and encoder handoff remain UNKNOWN. No new LIVE is requested by this report.
