# Phase 16 — TikTok Native Audio Integration

## Decision: NO-GO for target PCM injection

**New finding:** a concrete static OpenSL callback-registration candidate in the pinned liblyrax: call site **0x6bdab4**, callback candidate **0x6be020**. **Actual LIVE execution and PCM buffer ownership remain UNKNOWN.** No native PCM hook was implemented or deployed. No LIVE, post, account action, app launch, module reload or recorder test was performed.

Branch `feature/tools-android`. Reused Phase 10–15 implementation/evidence. Current device inventory confirms SM-G996U/API 34, 61 installed TikTok APK paths, and unchanged Phase 14 module/base hashes. TikTok had **no running PID**; this is not evidence about its active native audio path.

## 1. VCAM source comparison

Inspected pinned source revisions, not README feature claims. Selected source snapshots are retained only in ignored `.tools/phase16/references`; no upstream implementation was copied into Android modules. Repository metadata reports MIT. Prebuilt release APK/ZIP binaries were not audited.

| Implementation | Actual source interception / behavior | Relevance and limitation |
|---|---|---|
| Yaahua/vcam `MicrophoneHandler`, commit `aa23a684767cfffe01b4bc964fd855024f781275` | Java Xposed hooks for `AudioRecord.read(byte[],int,int)`, then an attempted `read(byte[],int)`, then `read(short[],int,int)`; `MediaRecorder.setAudioSource` logs selection; `setCamera` detects recording | No ByteBuffer read hook in this file. On AOSP Android 14, `read(byte[],int)` does not exist: its lookup fails inside the shared try block, so later short-array registration is not reached. This is a source/API finding, not an executed VCAM-on-Samsung test. |
| Same class: mute/replace/video_sync | A before-hook fills the requested buffer and returns a count, bypassing original read; video_sync decodes/cache-loads audio on demand, with a compressed-data fallback | Does not preserve original successful-read pacing/error contract. Full-file caching/decode in the read path, absent observed capture-format adaptation and compressed fallback are unsuitable for the bounded real-time target bridge. No copied code. |
| Yaahua `NativeAudioHook.java` | Tries to load `native_audio_hook`; declares JNI setters for source/mute/PCM | No reviewable C/C++ or standalone `.so` implementation in the inspected source tree, no native build block in inspected `app/build.gradle`; HookMain invokes MicrophoneHandler. The JNI declarations/load-success flag do not establish an OpenSL/AAudio interception point or PCM ownership contract. Release archives remain unverified. |
| VSN6Tu/android_VCAM-Revise, commit `523f5c0e93a8888dfe91470935985771012812f7` | Inspected HookMain/VideoToFrames: camera hooks, `MediaRecorder.setCamera` recording detection and `MediaPlayer.setVolume` for local playback | No AudioRecord/OpenSL/AAudio capture hook in those entry/decoder files. Hearing local media playback is not soundtrack delivery into TikTok capture. |
| Sarae Phase 10–11 Java bridge | Calls original `AudioRecord.read` and replaces only successful returned PCM, with capture config/cursor tracking | Actual TikTok recording confirmed `read(ByteBuffer,int)`, 44.1 kHz stereo; encoded recording passed. Does not cover the unobserved native LIVE route. |
| Sarae Phase 15 native adapter | Owns its own OpenSL recorder and replaces completed buffers before local encoding | Controlled output PASS only. Its two 480-frame buffers, context struct and callback ownership must not be imposed on TikTok. Do not call its `NativeRecorder.create()` in the target. |

Sources: [Yaahua MicrophoneHandler](https://github.com/Yaahua/vcam/blob/aa23a684767cfffe01b4bc964fd855024f781275/app/src/main/java/com/yaahua/vcam/MicrophoneHandler.java), [NativeAudioHook declarations](https://github.com/Yaahua/vcam/blob/aa23a684767cfffe01b4bc964fd855024f781275/app/src/main/java/com/yaahua/vcam/NativeAudioHook.java), [Yaahua HookMain](https://github.com/Yaahua/vcam/blob/aa23a684767cfffe01b4bc964fd855024f781275/app/src/main/java/com/yaahua/vcam/HookMain.java), [build configuration](https://github.com/Yaahua/vcam/blob/aa23a684767cfffe01b4bc964fd855024f781275/app/build.gradle), [VSN6Tu HookMain](https://github.com/VSN6Tu/android_VCAM-Revise/blob/523f5c0e93a8888dfe91470935985771012812f7/app/src/main/java/com/example/vcam/HookMain.java), [Android 14 AudioRecord overloads](https://github.com/aosp-mirror/platform_frameworks_base/blob/android14-release/media/java/android/media/AudioRecord.java). Pin/blob/snapshot hashes are in `reference-sources.json`; overload evidence in `aosp-audiorecord-api.json`.

## 2. Target-specific liblyrax findings

Only the preserved, already identified ARM64 liblyrax was inspected. No repeated 61-APK / 250-ELF scan. One bounded ADRP/ADD search rooted in Phase 14's recorder strings yielded a new registration candidate; follow-up was restricted to that call, its outlined thunk and callback entry. Static investigation stops here because execution/ownership requires runtime evidence.

- ELF SHA256: **`9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`**; Build ID **`800d53bec62ddf0a1774842f5a16e1693ab7f0de`**.
- At RVA **0x6bdaa0**, x0 is loaded from `[x19+0xd8]`; x1 becomes **0x6be020**, x2 is x19. Call **0x6bdab4 → 0xdde074** loads vtable slot **0x18** and branches. On ARM64 this matches the fourth function in the Android simple buffer queue table, `RegisterCallback`.
- Its failure branch references the known recorder registration assertion. Slot 0x18 alone is not sufficient because different OpenSL interface types share offsets. This combination supports a **static registration candidate**, not runtime success.
- Eight exact instruction words are validated against the pinned ELF by `scripts/inspect_phase16_lyrax.py`. Runtime addresses require verified load bias/ASLR; these RVAs are not deployment instructions.
- Callback candidate entry uses the context argument, but the inspected entry does not establish which PCM buffers are complete or where PCM reaches the encoder. The callback signature supplies queue/context, **not PCM pointer/size**. No context-layout cast or guessed buffer offset is used.
- Existing JNI exports `AudioDeviceModule_nativeSetRecordingCallback` (0x665704), `nativeStartRecording` (0x665628), `AudioRecordProcessor_nativeAudioRecordWritePcm` (0x65f8a8) and `nativeAudioRecordInit` (0x65fe48) are alternative observation anchors only. No proof that any one is the LIVE PCM handoff. Nearest-export disassembly labels are not real names for stripped internal functions.

Evidence: `lyrax-targeted.json`, `.tools/phase16/registration-candidate-disasm.txt`, `queue-call-thunk.txt`, `callback-candidate.txt`; Android NDK 28.2 `SLES/OpenSLES_Android.h` callback/table declaration, lines 234–264.

## 3. Confirmed / suspected / unknown

| Question | Classification | Evidence / limit |
|---|---|---|
| Native/system microphone capture during Phase 12 LIVE | **CONFIRMED observation** | AudioFlinger active RECORD track PID 16744, 48 kHz/stereo/S16; no Java bridge reads/substitution observed |
| OpenSL recorder implementation exists in liblyrax | **CONFIRMED static presence**, with registration candidate now mapped | Imports, recorder-specific assertions and instruction sequence; not runtime backend selection |
| Phase 12 LIVE selected OpenSL ES | **SUSPECTED** | Same-PID libOpenSLES log plus static recorder implementation; no active registration/callback trace |
| AAudio or direct native AudioRecord selected / excluded | **UNKNOWN** | Other route strings, dynamic symbol lookup and system libraries do not establish selection; Java read absence does not identify the replacement API |
| LIVE invokes callback candidate 0x6be020 | **UNKNOWN** | No runtime hit, registration return, queue/context or matching capture lifecycle |
| Client PCM layout, block size and ownership | **UNKNOWN** | Phase 12 HAL 960-frame / track 2,880-frame observations are not the app's OpenSL queue contract |
| Target capture-to-encoder handoff | **UNKNOWN** | No observed target PCM handoff or native soundtrack encoded-output evidence |
| Non-LIVE route suitable for proving native LIVE capture | **NOT ESTABLISHED** | Phase 11 recording used the Java route; Phase 13/14 preview and LIVE-preparation preview had no active RECORD thread; Phase 16 PID absent |

The actual selected backend remains unknown. Static OpenSL presence and Phase 15 PASS do not convert it to CONFIRMED. No further non-LIVE camera/recorder round is justified by the available evidence. See [Phase 12](../phase12/PHASE12_REPORT.md), [Phase 14](../phase14/PHASE14_REPORT.md), [Phase 15](../phase15/PHASE15_REPORT.md).

## 4. Changes actually made

- Added `scripts/inspect_phase16_lyrax.py`: bounded static candidate extraction and instruction validation for this exact ELF.
- Added `scripts/phase16_audio_observe.py`: **read-only** package/module/PID inventory; optional bounded snapshots of an already running process. It does not attach/debug, launch/stop apps, mutate buffers, load modules or control LIVE. Optional capture mode was not exercised because TikTok was not running.
- Added pinned reference metadata, findings, hashes and [observation-only plan](OBSERVATION_PLAN.md).
- **No Android implementation changes or builds/deployments.** Existing MediaAudioEngine, VideoAudioClock, PCM ring and native adapter are reused as references; their Phase 15 source hashes all remain unchanged. Phase 15 PcmRing correction remains source-only for TikTok; installed Phase 14 cache was preserved.

## 5. Exact next action and missing evidence

**Next implementation step: prepare and validate a target-scoped, pass-through native observer for the registration/callback candidate, not a PCM injector.** First establish native observation loading support under pinned LSPatch; the current Java hook and `.so` recorder do not prove this capability. Verify forwarding/lifecycle/performance with the existing Phase 15 recorder. No second recorder implementation is needed. Only after these prerequisites and a separately authorized safe pre-broadcast deployment should a future user-controlled observation session be considered. No LIVE is requested now.

Required runtime evidence: successful registration and selected recorder/queue/context; callback address/thread/cadence tied to capture start/stop; actual client PCM format; buffer pointer/byte count, enqueue/completion/clear/release ownership; capture-to-encoder handoff. The plan forbids PCM mutation, global native hooks, context-layout guesses, blocking logging and changing modules during broadcasting. A metadata-only ADB snapshot cannot provide the missing callback/ownership trace by itself. See `OBSERVATION_PLAN.md` for boundaries and prerequisite gates.

Until that evidence exists: **NO-GO for native PCM deployment, LIVE audio PASS, or a new LIVE test request.** Current code does not implement the observation hook or a target-native Audio Bridge. An unsuccessful future observation must be reported as partial; it is not grounds to repeat a broadcast or hook all audio APIs.

## 6. Verification and preservation

- **Device work: 1.047 seconds**, one read-only inventory; no endurance, camera regression, audio recording or LIVE. Hard timeout per ADB command. No further device test after inventory.
- Cached Phase 14 module SHA256 verified **`53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`**.
- Installed base APK SHA256 verified **`4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`**; 61 APK paths. No installation/account/config/media change.
- No running TikTok PID; app left unlaunched. No claim that the module was loaded into a current process.
- **104 Phase 12 original evidence files** and **15 Phase 15 source snapshots** checked unchanged. Prior recorder/unit/encoded-output results were reused; no new PCM-output or target audio PASS is claimed.
- Static instruction validation and Python compile checks PASS. Optional future snapshot collection and native observer loading are unverified; no Android build was necessary.

Audit files: `results.json`, `reference-sources.json`, `aosp-audiorecord-api.json`, `lyrax-targeted.json`, `nonlive-inventory.json`, `preservation.json`, `adb.jsonl`, `source-sha256.json`, `evidence-sha256.json`. Third-party source snapshots and disassembly remain in ignored `.tools/phase16/` for local inspection only.
