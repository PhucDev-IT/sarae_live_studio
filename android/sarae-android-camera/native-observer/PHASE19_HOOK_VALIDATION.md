# Phase 19 native hook validation

Owned harness only. Do not add its native_init asset to the TikTok controller
module or deploy the harness module into TikTok.

`libsarae_hook_validation.so` is separate from the Phase 17/18 cooperative
observer. It defines only the public NativeAPIEntries layout from pinned core
`e00c5c5038bbcc14e0b382ab893301d6993e6989`: uint32 version, hook function pointer,
unhook function pointer (natural ARM64 alignment). Version must equal **2**.
The entry returns an on-library-loaded callback; registration is through the
owned module's `assets/native_init`. It does not implement its own Dobby or
linker hook engine.

The library notification matches only exact basename `libsarae_native_audio.so`
and saves the supplied handle. It performs no symbol lookup, hook, IO, JNI,
allocation or nested dlopen under the framework registry/loader lock.

On the worker, install resolves only `sarae_owned_probe_v19` from that owned
handle, verifies dladdr identifies the owned harness, saves original entry
bytes and invokes the framework hook. The probe has signature:

```c
uint64_t sarae_owned_probe_v19(uintptr_t queue, uintptr_t context,
                             uint64_t sequence, uint64_t depth);
```

It reads no pointer argument and returns a deterministic value. The existing
OpenSL recorder calls it once per completed callback when the Phase19 flag is
enabled, before copying/enqueuing its original PCM. The inline hook calls the
original trampoline once, returns the original result and records metadata.
The original OpenSL callback itself is still registered/forwarded through the
existing Phase17 cooperative observer; this is not a direct inline-hook test
of an OpenSL callback or liblyrax function.

32 control calls with recursion depth3 exercise 128 native entries and nested
trampoline calls through the patched owned function. They use numeric sentinel
arguments that are never dereferenced. They do not run concurrently with capture.

The wrapper performs no heap allocation, JNI, file IO, logger, decoder or blocking
lock. Its metadata ring is 256×88 bytes plus fixed counters. Atomic flag acquisition
is a single non-blocking attempt. Overflow drops metadata rather than skipping
the original function. Worker JNI drains the ring and logs emission/receipt times.
All timings use CLOCK_BOOTTIME. The instrumented extra-time metric ends before
publication/decrement and begins after entry counters; it excludes entry/return
branches and is not total native hook overhead or a realtime bound.

Control/stat array (21×uint64): API version, native_init count, pointers present,
owned load count, installed, last status, replacement entries, original forwards,
in-flight, peak in-flight, telemetry drops, engine errors, recording guard,
guard rejections, original trampoline, target address, hook pointer, unhook pointer,
framework hook calls, framework unhook calls, first16 original bytes restored.

Control commands: 0 stats; 1 install; 2 invalid-candidate sentinel rejection test;
3 unhook; 4 enable recording guard; 5 clear recording guard after recorder destroy.
Install/unhook reject recording or nonzero in-flight with -16 before framework
calls. Version/handle absence rejects with -20. Missing/foreign owned symbols
reject with -22. Command2 is a sentinel failure-path exercise, not a deliberately
induced Dobby failure. Underlying engine installation failure was not forced.

Unhook is attempted only after STOP and DESTROY have synchronized callbacks and
two 200ms observation windows have shown no late cooperative callbacks. The
recording guard remains set until recorder destruction. Then unhook restores the
entry and a post-unhook self-test confirms original behavior without replacement
entries. Control operations are serialized, not a concurrent hot-unhook design.

Framework native load notifications have no public unregister operation in this
ABI. The library remains mapped while the owned process lives. Never dlclose it
or release callback state underneath the framework. Original hook resources and
cooperative relays are cleaned; the loader notification registration persists.

Reproduce only in a new explicit device budget, not by resetting an old result:
build `:native-observer-module:assembleDebug :native-audio-test:assembleDebug`,
`prepare_phase19.py`, guarded `phase19_device.py`, then offline
`analyze_phase19_audio.py`, `analyze_phase19_gap.py`, `analyze_phase19.py`.
The runner permits only an in-place install of the owned harness, never TikTok.
