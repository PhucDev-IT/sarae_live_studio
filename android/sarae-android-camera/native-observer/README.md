# Owned-callback native observation component

**Phase 19 adds a separate `libsarae_hook_validation.so` test component and
`assets/native_init` in the owned-harness module.** The original
`libsarae_native_observer.so` remains byte-identical to Phase 18. Its cooperative
callback API described below is distinct from Vector's inline hook API.
See [Phase 19 contract and scope](PHASE19_HOOK_VALIDATION.md). The current
historical Phase 17 device script is not a runner for the Phase 19 state.

Phase20 extends only that owned validation component with ELF/map identity guards,
an exact first12-byte callback-pattern test and real null/duplicate engine errors.
Direct observer RTLD_NOLOAD pinning failed; owned tests use an explicit owner-held
reference. This is not a foreign-library binding fix. Target interception remains
disabled/undeployed. See `test-results/phase20/PHASE20_REPORT.md` and
`test-results/phase20/LIVE_AUDIO_PREPARATION.md` for the current NO-GO gates.

This ARM64 C/JNI library observes a callback explicitly supplied by its owner.
It has no symbol scanner, inline hook, vtable patch, recorder, PCM decoder,
PCM reader/writer, or TikTok allowlist. Loading it does not attach to liblyrax.

## Loading verified in Phase 17

`:native-observer-module` is a small legacy Xposed module. It allows only the
debuggable `co.sarae.camera.nativeaudiotest` main process and hooks its dedicated
`RecorderActivity.observationApi()` lab method after Application.attach.
It takes its ARM64 `.so` from the LSPatch-provided module APK path, verifies the
content hash, writes a read-only copy to that process's private code cache,
then calls `System.load` and the JNI `apiAddress()` method on the test worker.
Native `JNI_OnLoad` records PID, TID and CLOCK_BOOTTIME. This was verified under
JingMatrix LSPatch 1.2/487 in the owned harness, not inside TikTok.

The library is C11 and does not need a second libc++ runtime. C ABI version,
magic, structure and packet sizes are checked by the existing recorder JNI
bridge before accepting the explicitly supplied API pointer. Pointers/tokens
are valid only in this process and for their specified lifetimes; never accept
arbitrary addresses or persist tokens across process restarts.

## Forwarding and ownership

The Phase 15 recorder optionally creates an observer relay before registration.
It supplies its original callback, original context and actual queue. OpenSL
registers the observer forwarding function with the relay as registration
context. For each active invocation, the observer calls the original function
exactly once with the received queue and the **original** context. Original
PCM access and Enqueue stay entirely inside the original recorder callback.
The observed entry point refuses VIDEO/MUTED: it is MIC pass-through only.

Only fixed telemetry is added: timestamps, thread ID, function/context/queue
identities, sequence and flags. A 256×80-byte ring uses lock-free atomics. A full
ring drops telemetry, not original callbacks. If another callback is concurrent,
a non-waiting atomic-flag test skips that telemetry publication and still
forwards the original call; reentrancy/drops are counted. No allocation, JNI,
file IO, formatting, logging, decoding, encoding, mutex or spin-wait occurs in
the observer callback. `gettid` and `clock_gettime` run there. The original
callback retains its existing recording/queue behavior.

The worker drains packets through JNI and writes JSONL. Entry/exit/source-call
and worker-receipt timestamps use CLOCK_BOOTTIME. Measured overhead includes
telemetry packet copy through the last timestamp, excluding final publication
release-store, flag-clear, in-flight decrement and function return. It is an
instrumented-section measurement, not a hard real-time worst-case bound.

## Teardown

Stop the existing recorder, synchronize its callbacks and destroy its OpenSL
object before retiring the relay. Retain relay metadata for the post-DESTROY
check, then free it on the worker. A late invocation of a retired relay is
counted and refuses to call the now-invalid original context; none was observed.
Calls after relay memory is freed are outside the API contract. Never retire
or free a relay while its native object can still dispatch callbacks.

`shutdown` refuses while any relay is allocated. The controlled test verifies
that rejection before recording, then frees all relays and verifies successful
cleanup. Cleanup releases observer resources; it deliberately does not dlclose
a JNI library still owned by its class loader.

## Reproduction and limitations

```powershell
./gradlew.bat :native-audio-test:assembleDebug :native-observer-module:assembleDebug --offline
python scripts/prepare_phase17.py
python scripts/phase17_device.py
python scripts/analyze_phase17.py
```

The device runner has a persistent budget and refuses unreviewed repeats. It
installs only the owned harness, checks idle microphone ownership, records a
10-second baseline and a 10-second observed case with the existing recorder,
then returns HOME. TikTok module/APKs/account are preserved. The special idle
guard resume exists only for the documented pre-deployment check; it is not a
general test retry mechanism. Build logs and artifacts are in ignored `.tools`.

Target-specific native interception, target JNI loading, liblyrax callback
registration/PCM ownership and LIVE behavior remain unverified. The static
liblyrax candidate is not registered with this observer. No LIVE is started or
requested by these tools. See `test-results/phase17/PHASE17_REPORT.md`.
