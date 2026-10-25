# Phase 20 — Target Native Hook Safety & Binding

2026-10-24, `feature/tools-android`. **PARTIAL: owned safety tests PASS; target deployment NO-GO.**

No liblyrax hook, unknown PCM access, TikTok recording, LIVE or target module update occurred. This report also supplies the safety evidence for the subsequent “Prepare Real LIVE Audio Verification” request; see **LIVE_AUDIO_PREPARATION.md**.

## Results

| Gate | Result / exact scope |
|---|---|
| Candidate callback ABI | **STATICALLY SUPPORTED:** two pointer arguments, void callback; selected runtime backend/invocation **NOT TESTED** |
| Exact first12-byte prologue / trampoline continuation | **PASS on owned code** with x19/x20 canaries and nested calls |
| Original PC-relative branch relocation | **PASS on owned B-at-entry function**, 32 correct results |
| BL relocation at candidate byte12 | **NOT EXERCISED:** observed engine patch is 12 bytes, so that BL executes at its original address |
| ELF/hash/Build ID/fingerprint/map resolution | **PASS in owned process with explicit cooperative owner lifetime** |
| Direct module-side library pin | **FAIL:** RTLD_NOLOAD could not obtain the already mapped host ELF from the observer's loading context |
| Wrong hash / Build ID / fingerprint | **PASS:** rejects -31 / -32 / -33 before hooking |
| Ambiguous instances | **Shared count policy tested with synthetic count2 → -34**; a real duplicate ELF load was not induced |
| Target absent | **PASS clean no-hook (-40) in owned process**; does not establish TikTok's LIVE activity |
| Real hook-engine rejection | **PASS:** actual Dobby null and duplicate-hook errors return -1 without changing backup/installed entry |
| Callback/PCM/output preservation | **PASS in owned recorder:** 990 capture callbacks, original PCM unchanged, saved MP4 analyzed |
| Actual TikTok target binding / audio / receiver | **NOT TESTED / NO-GO** |

## Callback assessment

Reused pinned Phase18 liblyrax, not another broad scan:

- SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`.
- Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`.
- Callback RVA **0x6be020**, registration callsite **0x6bdab4**. Neither the mid-function callsite nor shared thunk was hooked.

The registration code passes queue in x0, callback pointer in x1 and context in x2. The candidate consumes context in x1, saves/restores x19/x20/LR and has no deliberate return value. This is consistent with an OpenSL two-pointer void callback. It is a static calling-contract assessment, not proof that LIVE selects this recorder.

Entry:

```asm
0x6be020  str x20, [sp, #-32]!
0x6be024  stp x19, x30, [sp, #16]
0x6be028  add x19, x1, #0x44
0x6be02c  bl 0xddf13c
```

The helper moves x19 to x0, context/x1 to x20, then tail-calls mutex lock. Exit unlocks, restores x19/LR and tail-branches to a helper restoring x20/SP before ret. These helpers use a deliberate outlined convention; they are not standalone hook targets. There is no PAC/BTI instruction in this entry/exit path examined. This does not assert the whole ELF is PAC/BTI-free. Later ADRP/conditional branches lie beyond the tested 12-byte overwrite.

Owned assembly reproduces the exact first three words and BL position with its own helper/metadata body. It does no unknown context dereference or locking. Canary tests verify x19/x20 preservation, stack/LR continuation and original queue/context values. A second owned B-at-entry function verifies PC-relative branch relocation and unchanged integer return.

Runtime patch bytes are **ADRP x17; ADD x17; BR x17**, overwriting 12 bytes. Trampoline copies the three original instructions then jumps to **entry+12**, where the original BL remains. This was confirmed from saved before/patched/trampoline code bytes, not inferred from FPS. **Do not call this a BL-relocation PASS.** A larger patch plan covering that BL, or other prologues/PAC/BTI, needs an additional owned relocation test or a verified refusal policy before target use.

## Guarded resolver and actual blocker

Implemented worker-side resolution in `NativeBinding.kt` / `phase20_binding.c`:

1. Exact-basename notifications only for the owned library and liblyrax; atomic handle/generation metadata only, no IO/JNI/symbol resolution/hook under the framework loader lock.
2. One bounded dl_iterate_phdr enumeration at initialization; repeat only on a new matching handle generation, not polling. This run used **one** enumeration.
3. Require exactly one object, bounded program-header/note count, expected Build ID, readable executable PT_LOAD and real executable maps range. Match backing file inode/device with maps.
4. Compute **dlpi_addr + RVA**. Do not subtract a ZIP-containing maps offset to invent ELF bias. The test resolved the owned APK-contained ELF at bias **0x73eca46000**, RVA **0x21270**, address **0x73eca67270**.
5. Worker hashes actual SO bytes, including ZIP-member extraction for APK-contained libraries; checks file identity before/after reading. Java target profile pins hash/Build ID/RVA/entry bytes. Native resolver checks live instruction bytes and refuses ambiguous/unexpected mappings.
6. Require a valid library lifetime before install. A borrowed notification handle alone is insufficient proof that another owner cannot unload the library.

Initial attempt stopped at -43 **before installing any hook**. One bounded diagnostic follow-up captured:

> dlopen failed: library “…/origin/2478970255.apk!/lib/arm64-v8a/libsarae_native_audio.so” wasn't loaded and RTLD_NOLOAD prevented it

The ELF is mapped and ELF/map/fingerprint checks already passed. The module-side dlopen lookup cannot reacquire it, whereas the same owned library's JNI call can obtain its own reference. This supports a loading-context/linker-namespace visibility mismatch; it does not prove which private linker namespace lookup rule caused it.

For **owned tests only**, the harness's own JNI acquires the strong reference, explicitly hands it to the observer, holds it through capture/unhook and releases it afterward. The resolver requires that handle to match the exact observed owned handle. This cooperative owner contract is not available from unmodified liblyrax and is **not a TikTok binding fix**. The target resolver/profile is source preparation only; target installation is not exposed by these test controls. No namespace bypass was implemented.

The foreign library's namespace/lifetime, actual load event and safe pre-capture attachment point remain unresolved. Therefore target binding stays **NO-GO**, even though owned mechanics can be exercised.

## Engine failures, callbacks and output

Pinned Vector's Dobby gitlink is **05a09ac6807a6bb1726350e40ea4b127c1c79809**. Reviewed its [DobbyHook implementation](https://github.com/JingMatrix/Dobby/blob/05a09ac6807a6bb1726350e40ea4b127c1c79809/source/InterceptRouting/Routing/FunctionInlineHook/FunctionInlineHook.cc) before safely invoking null/duplicate rejection.

- Real `hook(NULL, …)` → **-1**, backup sentinel unchanged; no address patched.
- Real duplicate hook of the already hooked owned function → **-1**, backup sentinel and patched entry unchanged; current original trampoline remains valid.
- These are engine calls, unlike Phase19's controller sentinel. They do not cover every allocation/mprotect/partial-install fault.

**1,118 replacement entries → 1,118 original forwards:** 128 nested control entries plus **990 recorder callback entries**. Peak nested in-flight=4; zero telemetry drops/errors; queue/context values match the original recorder. An unhooked baseline and post-unhook test each execute 128 original calls. Canaries/arguments/void continuation remain correct.

STOP and DESTROY each retain the existing 200ms observation windows. Cooperative callback counters and hook counters remain stable, no late callback/in-flight operation. Both hooks unhook once and restore the first32 original bytes. The extra owner reference is released; cooperative relay is freed. The framework's load notification and its library remain process-lifetime registrations, not dangling/unloaded callbacks.

Owned capture callback interval: median **8.483594 ms**, p99 **12.180517 ms**, maximum **13.979792 ms**. Instrumented wrapper additional section: median **9.167 µs**, p99 **16.544540 µs**, maximum **57.812 µs**. This excludes final publication/decrement and entry/return branches; not total hook overhead or a realtime bound.

Saved OBSERVED.mp4: **9.900 s original/delivered PCM**, byte-identical; decoded AAC duration **9.962667 s**, correlation to its own mic snapshot **0.997083**, measured lag **42.667 ms**. No soundtrack replacement/decoder was active. This proves preservation in the owned harness, not TikTok audio injection or A/V sync. No unexpected fatal Java/native error in bounded logs.

## Video gap

Phase19's offline localization is reused: output gap **176.257396 ms**, active latch **138.032239 ms**, decoder worker still emitting, no switch. No new camera test, EGL edit or threshold change.

Additional low-overhead measurements should split wall time **and thread CPU time** around updateTexImage, getTransformMatrix, timestamp read and source-latched event separately. Large wall/low CPU suggests waiting or descheduling; it does not distinguish them alone. Correlate the existing Sarae.latch trace with available scheduler/BufferQueue/GPU/fence trace events for the same PID/TID and clock domain. Availability/permissions must be checked; lack of events is not proof of no fence wait. Use bounded records drained off-thread, no synchronous file logger, stack unwinding or glFinish in the renderer. This is a measurement plan, not deployed engine code.

## Budget and preservation

**26.875 seconds cumulative device work:** first clean rejection/collection **7.704 s**, one evidence-driven diagnostic run including the 10-second recording/cleanup **19.171 s**. First failed evidence and artifacts remain preserved (`run-123228`, initial-*); successful owned evidence is `run-123717`. No further test round was run.

TikTok PID **8226 before/after**, cache module SHA
`66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120`,
base SHA `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`, unchanged. A fresh read-only cache copy is saved as `.tools/phase20/tiktok-phase18-rollback.apk` with that exact module hash. No uninstall, clear-data, restart/force-stop or cache write to TikTok; no account activity.

Owned Phase19 harness rollback: `.tools/phase20/rollback-phase19-harness.apk`, SHA
`3fd249790f094b075c3bc48cbff678f0f394dee4685787769783fe81e4e7250d`.
Second-run backup of first Phase20 candidate is separate. Restore only the owned package under a future idle-state check/new budget if desired. Never install this harness APK over TikTok. No rollback was required in this execution.

Current owned module SHA `feed1c6e252e088988e3ab4de4b1b03f10e78c10f81c379776ece0903780f3d8`;
patched harness SHA `f3df58eb87d31ab10e4496b9b5fb39827c09fecbfca27a8f1e2585917ed7be71`;
validation SO SHA `23a1b07fe04b2fddae99ebc12764cb85df879e29e6798b0f692b9b45f772e215`.
Original cooperative observer SO remains byte-identical to Phase18. Native API2 and pinned loader unchanged. Camera2/EGL/PCM Engine sources and installed TikTok module preserved.

Detailed evidence: analysis.json, events.jsonl, runtime-excerpts.json, target/owned profiles, bounded disassembly, code.bin, source/ELF/build records, adb/device state and hash manifests. Raw APKs, encoded/raw audio, maps/logcat and source-reference snapshots are in ignored `.tools/phase20`. Original Phase12–19 evidence verified unchanged.

## Precise next action

Resolve and independently validate **non-cooperative** target lifetime acquisition/namespace visibility and a safe attachment point before recorder callbacks start. Cover or explicitly refuse any overwrite beyond the validated 12-byte prologue. Then validate exact target binding while inactive, with reviewed rollback artifacts. Do not use global audio hooks, mid-function callsites, unknown context layouts or another LIVE as a workaround. Actual liblyrax invocation and PCM ownership remain unknown; LIVE is not requested.
