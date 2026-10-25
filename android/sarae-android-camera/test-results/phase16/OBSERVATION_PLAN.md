# Future target-native observation — design only, not deployed

## Current decision

**NO-GO for PCM replacement and for requesting a new LIVE now.** Phase 16 does
not authorize broadcasting. This document prepares an observation-only strategy
for a separately user-authorized future session. The metadata collector is
implemented; target native instrumentation is not implemented or loaded.

## Narrow static candidate

Exact ELF SHA256:
`9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`.
Build ID: `800d53bec62ddf0a1774842f5a16e1693ab7f0de`.

- Registration call-site RVA **0x6bdab4**; x0 is loaded from `[x19+0xd8]`.
- x1 materializes callback RVA **0x6be020**, x2 is the x19 context.
- Outlined thunk RVA **0xdde074** loads vtable slot **0x18**, then branches.
- The error branch references the known OpenSL recorder RegisterCallback
  assertion. Slot 0x18 alone is insufficient: other OpenSL interface tables can
  use that offset for different methods.
- Standard Android simple-queue callback signature is `(queue, context)`;
  **a completed PCM pointer/length is not an argument**. Buffer discovery needs
  actual queue ownership and enqueue/completion correlation.

These are static candidates for this exact build, not a confirmed LIVE path.
RVA is not an absolute runtime address. Resolve ELF load bias/ASLR and verify
executable mappings and byte fingerprints before any future instrumentation.
Nearest exported labels in disassembly do not identify these stripped functions.

## Before requesting any future LIVE

1. Establish a supported, tested native observation loading mechanism for the
   pinned LSPatch build. The current Java Xposed bridge and successful OpenSL
   recorder do **not** prove native interception/loading support. Do not assume
   that adding the Phase 15 `.so` to a Java module attaches to another recorder.
2. Any observation module update needs separate review and a safe pre-broadcast
   deployment/rollback procedure preserving account data and Phase 14 fixes.
   Do not replace APKs or deploy the observer during broadcasting.
3. Validate pass-through observer mechanics using the **existing Phase 15
   recorder**, without constructing another recorder implementation. Preserve
   one original call per callback, arguments/context, return behavior, stop/
   release and queue order. Verify fixed memory and real-time overhead. This
   validation has not been performed in Phase 16.
4. Recheck exact target ELF identity. The observer must default OFF and require
   the lab package/main process, pinned ELF hash/build ID, instruction fingerprint
   and an explicit observation configuration. No generic interception of all
   AudioRecord/AAudio/OpenSL objects; no shared/global-vtable rewriting.
5. Establish capture-vs-playback ownership for the selected queue via actual
   recorder creation / SL_IID_RECORD and interface-return relationships. If
   evidence cannot be collected safely, leave that field UNKNOWN. Never infer
   client block size from the HAL's 960-frame period.
6. Only after safe loading/pass-through verification, prepare a single future
   observation sequence and obtain explicit user authorization. This plan is
   not a request to start LIVE and supplies no Start/End LIVE automation.

## Observation contract

Initial scope is **only the identified liblyrax registration/callback candidate**.
Record registration return code, caller RVA, original function address, queue,
context and recorder correlation. A future forwarding callback must invoke the
original exactly once with the original queue/context; it must not fill, zero,
resample, consume, play, copy or dereference unknown PCM memory.

Record callback counts and thread identities, entry/exit CLOCK_BOOTTIME,
original callback duration, capture start/stop and registration changes. Use
lock-free atomic counters or preallocated per-producer telemetry storage; do
not assume the controlled recorder's single producer applies to the target.
No allocation, JNI, JSON/string formatting, stack unwinding, disk IO, decoder,
encoder or blocking logger in the callback. Worker flushes bounded metadata
and records both event-emission and handler-receipt timestamps and drops.

If safe, separately established queue-specific observation is available, record
**metadata only** for successful enqueues: exact queue, address, byte size and
sequence. Correlate ownership until completion and re-enqueue, buffer reuse,
clear/stop/release, callback ordering and reentrancy. Do not globally hook
Enqueue or cast the target context to the Phase 15 Recorder struct. If buffer
identity or format is not established, PCM replacement remains disabled.

Minimum future event schema:

```json
{
  "kind": "registration|capture_state|callback_enter|callback_exit|enqueue_metadata",
  "clock": "CLOCK_BOOTTIME",
  "emissionNs": 0,
  "receiptNs": 0,
  "pid": 0,
  "tid": 0,
  "buildId": "pinned",
  "recorderIdentity": null,
  "queueIdentity": null,
  "contextIdentity": null,
  "callbackRva": null,
  "registrationResult": null,
  "bufferIdentity": null,
  "bytes": null,
  "pcmRate": null,
  "pcmChannels": null,
  "pcmEncoding": null,
  "sequence": 0,
  "telemetryDropped": 0,
  "pcmModified": false
}
```

Unknown fields stay null; do not populate format from AudioFlinger as though it
were the client contract. No raw microphone or account data is needed.

## Passive host collection already implemented

`scripts/phase16_audio_observe.py` performs only read-only ADB operations with
hard subprocess deadlines. Default is package/module/PID inventory. Optional
`--snapshot-existing-process` also collects bounded PID logcat, AudioFlinger,
maps where allowed and existing Java-bridge metadata. It never launches an app,
attaches a debugger, sends a signal, clears logs/data, reloads a module or
controls recording/broadcasting. Output labels must be unique.

During a future separately authorized session it can support a short snapshot:

```powershell
python scripts/phase16_audio_observe.py --label future-audio-observation --snapshot-existing-process
```

This collector alone **cannot identify callback registration or PCM ownership**.
It cannot replace the missing native observer. Android non-root restrictions
may block maps/stack profiling; report UNAVAILABLE rather than bypass them.
Do not use debuggerd, ptrace breakpoints, process suspension, force-stop or
module reload while broadcasting.

## Future single observation sequence

User starts broadcasting manually only after the prerequisites above and new
authorization. Keep audio in original MICROPHONE pass-through; no A/B switches
or injection are needed to identify capture. Correlate selected registration,
callback activity, recorder state and AudioFlinger PID/session over a short
window. Keep active diagnostic work bounded, preserve the session, and let the
user end it manually. Never request a repeat session to compensate for missing
telemetry. Zero hits at the candidate is not proof that another backend is
absent; record the limitation and stop, rather than installing broad hooks.

## Evidence needed before the smallest PCM adapter

GO for implementing a target-bound adapter requires confirmed runtime backend,
the actual recorder/queue/callback/context, original callback ordering,
successful enqueue/completion ownership, client PCM format and exact byte
contract, stop/clear/release/restart behavior and a proven path into the target
encoder. Then reuse MediaAudioEngine/VideoAudioClock, bounded PCM storage and
Phase 15 adapter techniques. Bind to renderer source commits, retain MIC
pass-through, VIDEO PCM and MUTED zeros, and keep decoding outside callbacks.

Do **not** call Phase 15 `NativeRecorder.create()` inside TikTok: that would
create a separate recorder and would not replace TikTok's captured PCM. Its
480-frame blocks and FIFO storage layout are not the unknown target contract.

Actual soundtrack delivery must subsequently be verified in target encoded
output; LIVE receiver PASS still requires receiver evidence. Neither a static
callback address, successful registration, counters nor decoded PCM is enough.
