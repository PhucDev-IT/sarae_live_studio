# Real LIVE audio preparation — NO-GO

2026-10-24. **MP4 soundtrack in actual TikTok LIVE is not fixed or verified.** No LIVE was started or requested. This preparation shares Phase20's **26.875-second** cumulative device budget; there is no separate hidden test budget.

## Selected approach

**Select a target-scoped native capture observer first; defer PCM replacement until the actual capture path and buffer ownership are established.** Reuse Sarae's existing Phase15 PCM Engine/ring, VideoAudioClock/source commits, Phase17 cooperative callback telemetry and Phase19 Vector native API. Do not adopt another recorder/decoder or install a general virtual microphone module.

Phase12 native LIVE behavior and Phase11 Java AudioRecord recording behavior differ. AudioFlinger configuration is not proof of the client entry point or client buffer contract. The static liblyrax OpenSL callback candidate remains a candidate. Replacing arbitrary native buffers now would guess the backend/format and ownership.

## Reviewed source comparison

Sources are pinned snapshots, not claims of TikTok compatibility. Third-party implementation code was **not imported** into Sarae. Review copies stay in ignored `.tools/phase20/references`.

| Source | Confirmed mechanism in inspected source | Useful lesson / limit for Sarae |
|---|---|---|
| PhantomMic `Mino260806/PhantomMic`, commit **768cb32db3eab234083bdc35ad40f9bed833fcc8** | native_init supplies hook/unhook; hooks native AudioRecord obtainBuffer, set and stop; SDK symbol variants select libmedia/libaudioclient; overwrites returned buffer using assumed Buffer fields | Native Java-read hooks alone can miss callback capture. Preserve original results/lifecycle. Private C++ signatures/layouts must be verified on this Android14 build; symbol presence is insufficient. No direct AAudio/OpenSL API interception in this inspected entry |
| GlassMic `lm060719/io.mo.glassmic`, commit **ff5da740b10cd772d3e635d9a2d42acda944733c** | native AudioRecord::read via shadowhook; OpenSL slCreateEngine hook and shared-vtable CreateAudioRecorder/GetInterface/RegisterCallback/Enqueue tracking; AAudio read and builder data-callback wrapping | Distinguish blocking reads/callbacks, preserve original short-read/errors and callback userdata, track recorder FIFO/format and avoid double replacement. Its broad hooks/shared vtables, callback mutex/deque and Android16 object-layout format inference are not validated Sarae Android14 mechanisms |

PhantomMic's README says non-root LSPatch was theoretical/untested; it is not Samsung/API34/TikTok evidence. GlassMic's documented environment requires root/LSPosed/libxposed, not this non-root lab configuration. Its GPLv3 license and PhantomMic's absence of a top-level license in the inspected tree further argue against copying implementation. Those are source facts, not reasons to replace the working Sarae architecture.

Primary references:

- [PhantomMic native entry](https://github.com/Mino260806/PhantomMic/blob/768cb32db3eab234083bdc35ad40f9bed833fcc8/app/src/main/cpp/main.cpp), [ABI/symbol variants](https://github.com/Mino260806/PhantomMic/blob/768cb32db3eab234083bdc35ad40f9bed833fcc8/app/src/main/cpp/phantom/hook_compat.h), [requirements](https://github.com/Mino260806/PhantomMic/blob/768cb32db3eab234083bdc35ad40f9bed833fcc8/README.md).
- [GlassMic OpenSL path](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_opensl.cpp), [native AudioRecord](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_audiorecord.cpp), [AAudio path](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/xposed/src/main/cpp/glass_aaudio.cpp), [requirements/license](https://github.com/lm060719/io.mo.glassmic/blob/ff5da740b10cd772d3e635d9a2d42acda944733c/README.md).

## Preparation gates

| Gate | Status |
|---|---|
| Existing decoder/ring/native API/owned observer | **PASS**, prior evidence reused |
| Matching first12-byte owned prologue, continuation, reentrancy, callee-saved canaries | **PASS**, Phase20 |
| PC-relative B relocation / real Dobby null and duplicate failures | **PASS**, Phase20 |
| Original PCM/callback lifecycle and saved owned MP4 | **PASS**, Phase20; not TikTok output |
| ELF hash/Build ID/entry/map guards, missing-target refusal | **PASS for tested owned cases**; actual duplicate mapping not induced |
| Foreign liblyrax reference/lifetime and safe namespace lookup | **FAIL / unresolved**; direct module pin failed; cooperative owned fix cannot be assumed for TikTok |
| All possible target patch lengths / BL relocation | **UNVERIFIED beyond observed 12 bytes** |
| Actual selected LIVE recorder / client PCM contract | **UNKNOWN** |
| Safe installation before capture starts, no concurrent target patch | **UNVERIFIED** |
| Current deployed TikTok observer | **Phase18 load-only**; no new native interception deployed |
| Receiver hears MP4 soundtrack | **NOT TESTED** |
| Decision to request LIVE now | **NO-GO** |

## Minimum remaining implementation

1. Establish a supported non-cooperative library-lifetime/binding mechanism across the actual host/module loading contexts, with exact identity checks. Validate it on owned code without cooperative recorder handles. Do not bypass the failed guard or treat a mapped address as an owned reference.
2. Validate or reject larger patch plans including the BL at byte12. Keep the actual callback ABI/prologue and continuation pinned. Define refusal/rollback behavior for unsupported mapping, error or partial install.
3. Prove a safe **pre-capture** attachment point. A library-load notification deferred to a worker does not guarantee the recorder has not already begun calling the function. Do not race a multi-instruction patch against unknown native capture threads during LIVE.
4. Review/build a minimal observation-only target module, keep existing Camera2/PCM composition unchanged, and activate only while TikTok is inactive. Verify loaded hashes, no pending deployment/build, pass-through and cleanup before any invitation to broadcast. Restore only the cached module if needed, under idle guards; never clear the account.
5. If the actual backend remains unknowable without LIVE, the first authorized session must be **observation-only**. Correlate candidate hits/lifecycle with AudioFlinger and receiver output. Do not promise MP4 soundtrack in that diagnostic session. Only after a confirmed client PCM entry/format/ownership contract can the existing PCM Engine be connected in a separately verified adapter.

This phase did not implement blind native AudioRecord/AAudio/OpenSL hooks or a second Media Engine to conceal the missing integration point.

## Safe deployment and evidence package

Current TikTok PID **8226** and Phase18 cache module SHA256
`66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120`
were preserved before/after. Fresh exact rollback copy:
`.tools/phase20/tiktok-phase18-rollback.apk`. Full base/module hashes and owned
rollback artifacts are in PHASE20_REPORT.md. No target update was performed.

Future observer logs should include loaded module/SO/ELF hashes and Build IDs,
PID/TID, load event emission and worker receipt, generation, load bias/executable
ranges, guarded binding/refusal reason, hook engine results and original forwarding
counts. Callback metadata must remain bounded/nonblocking; no PCM dumps or unknown
context casts. Correlate with Camera2 sessions/Surface identities, EGL/source
commits and bounded crash diagnostics. Keep sender events separate from actual
receiver video/audio evidence.

## User actions — now and only after future GO

**Now:** no LIVE action is requested. Keep the current TikTok lab installation/account; no reinstall, consent/login step or new recording is needed. The failure is a preparation blocker, not something the user can fix by restarting LIVE.

After the gates above have passed and the operator explicitly confirms GO:

1. User selects approved A/B media and has an independent viewer ready to record screen **with stream audio**. Operator verifies sources, pipeline/module hashes, preview orientation and logging before any broadcast. If the session is diagnostic-only, user must be told soundtrack replacement is not enabled.
2. User alone presses Start LIVE and handles confirmations, then reports actual active state. Operator observes A/capture lifecycle first. No module reload, install, TikTok restart, force-stop or pipeline restart during the session.
3. Use at most the predefined A→B→A sequence through an already verified non-disruptive runtime control. If video freezes, capture fails or audio is wrong, stop further mutations and retain safe observations. The user may end immediately; no second session/restart is requested to improve results.
4. Viewer records motion/orientation, source changes and the actual audio heard, allowing normal streaming delay. A sender preview, decoder PCM or hook counters alone never pass receiver audio. Compare receiver audio against the approved source tracks offline; measure drift separately, without promising sample-perfect sync.
5. User alone ends LIVE. Operator confirms non-broadcasting where observable and collects evidence; no automatic shutdown at the budget limit. Reserve at least 30 seconds for diagnostics/cleanup within **5 minutes active device work**, preferably 2–3 minutes overall actions. No additional rounds automatically.

For an eventual soundtrack verification: **AUDIO PASS requires the independent receiver to hear the actual MP4 soundtrack and matching source changes**, not the mic or a speaker re-recording workaround. If only sender evidence exists, receiver remains UNVERIFIED. One short success would not certify long-term LIVE stability.

## Conclusion

**Preparation is PARTIAL; real LIVE audio test remains NO-GO.** The remaining work is target binding/lifetime and safe capture attachment, followed by actual backend/buffer evidence. The existing PCM Engine is reusable, but is not yet connected to a verified TikTok LIVE native input. No further LIVE is requested until those gates pass.
