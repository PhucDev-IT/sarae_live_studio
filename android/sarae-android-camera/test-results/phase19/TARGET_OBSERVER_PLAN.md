# Target-scoped observation plan — not deployed

## Status

Native API2, owned-function trampoline/reentrancy and short callback lifecycle
passed in Phase19. TikTok still runs its untouched Phase18 cache module.
**NO-GO for liblyrax interception or a new LIVE request now.** Loading an owned
function does not prove relocation/ABI safety for the foreign callback prologue.

## Pinned identity gates

Reuse the installed-split verifier and original artifacts, not another broad scan:

- ELF SHA256 `9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`.
- Build ID `800d53bec62ddf0a1774842f5a16e1693ab7f0de`.
- Registration RVA `0x6bdab4`; callback candidate `0x6be020`; thunk `0xdde074`.
- Exact eight instruction words in `../phase18/target-identity.json`, originally
  established in `../phase16/lyrax-targeted.json`. Whole-ELF hash protects the
  remaining candidate bytes. Source comparison is static evidence only.

Any mismatch must refuse instrumentation. Hashes/Build IDs of this owned probe or
the loader must never substitute for liblyrax identity checks.

## Loading detection and address resolution

1. If a later separately reviewed module uses the validated framework load
   notifications, acknowledge that the framework observes process dlopen globally.
   Sarae's callback must match exact basename **liblyrax.so**, publish only bounded
   metadata and return. Never hook OpenSL/AAudio/AudioRecord globally.
2. Do not hash files, dlsym, JNI, log, install hooks or call dlopen inside the
   notification: the pinned core invokes it under a registry mutex after the
   original dlopen. Store the handle plus a generation and wake a control worker.
3. Handle already-loaded liblyrax using **one** worker-side dl_iterate_phdr
   enumeration at initialization. No repeated maps/symbol scanning or polling.
   Stop as UNVERIFIED if it is absent and no verified load event arrives.
4. Resolve the exact ELF object using dlpi_name, PT_LOAD segments, backing path /
   file identity and the callback-supplied handle where available. dlpi_addr is
   the ELF load bias; candidate address is `bias + RVA`. Validate that address
   lies in an executable PT_LOAD and actual mapping with matching in-memory
   instruction bytes before considering interception.
5. An APK-contained SO mapping's file offset includes its ZIP member offset;
   do not assume the first maps address or `maps.start - maps.offset` is ELF bias.
   Reject ambiguity, duplicate ELF instances, unsupported mapping or namespace
   mismatch. A handle/address must not be retained across library unload/reload.

This resolution procedure is a design, not implemented or tested against a
mapped liblyrax. Phase18 did not observe that ELF mapped in its preview snapshots.

## Remaining implementation / safety gates

- Review the callback's exact native calling convention, complete overwritten
  instructions and continuation. The registration BL callsite is **not** a
  function entry and must not be passed blindly to DobbyHook. A shared outlined
  vtable thunk must not become a broad interception workaround.
- Independently validate the actual required ARM64 relocation patterns on owned
  code before deployment. Phase19's tested function entry has ordinary stack
  prologue instructions; it does not cover arbitrary PC-relative instructions,
  PAC/BTI, target callback return ABI or all register preservation cases.
- Specify pass-through of original arguments/return exactly once. Initial target
  metadata must be limited to timestamps, pointer identities and counters; no
  unknown context cast, PCM buffer dereference, Enqueue change or replacement.
- Serialize install before capture or use a separately validated safe attachment
  condition. Test real underlying hook-engine error/partial-install handling
  before calling that failure path validated; Phase19 covered sentinel/busy guards.
- Keep the native observer mapped for the process lifetime while registered;
  public NativeAPIEntries has no notification-unregister entry. Do not hot-unhook
  an active target recorder based on a guessed lifecycle. A stop-sampling flag
  must not free state or skip the original callback.
- Review the complete binary/module change and exact rollback artifact while
  TikTok is inactive before any future update. No account/data reset is needed.

## Evidence still required

Runtime candidate selection, callback registration success, actual queue/context
and recorder lifecycle, selected LIVE backend and eventual capture-to-encoder
handoff remain UNKNOWN. The owned probe's PCM format/buffer size must not be
assumed for TikTok. An inactive preview cannot prove LIVE interception. No LIVE
is requested or authorized by this plan.
