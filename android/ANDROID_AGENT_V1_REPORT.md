# Android Agent V1 acceptance report

Date: 2026-10-20 (Asia/Saigon)

## Scope and safety

- TikTok 47.4.3 patched APK and the previously working injection module were preserved under `android/test-results/baseline-20261020`; the baseline SHA-256 manifest is unchanged.
- Android Agent is a separate package, `co.sarae.agent`, and was installed on the Galaxy S22 Ultra `SM-G996U` (`RFCR10DGF1T`). No TikTok LIVE was started.
- During the endurance run the Android media stream is muted to avoid looping the synthetic fixture tone; this run measures video/queue/service stability, not audio quality. The original device media volume was 6/15; it was restored to 6/15 after the run. The USB stay-awake setting was restored to 0.
- **Audio in TikTok LIVE: UNVERIFIED.** This app's Mock LIVE only exercises its own local player and backend controls.
- The app enforces HTTPS/WSS. An automatic review blocked a manifest change that would have enabled cleartext globally; the isolated device test instead uses a debug-only local CA. Release networking retains normal system certificate validation.

## Results

| Area | Result | Evidence |
|---|---|---|
| Existing baseline preserved | PASS | `test-results/baseline-20261020/BASELINE.md`, `SHA256SUMS.txt`, and `git-status-before-agent-v1.txt`; no reset/clean/checkout was run. |
| Camera2 `NoSuchMethodError` | PASS (prior offline resolver test) | Root cause was exact lookup on a TikTok callback subclass that inherited `onClosed`; resolver now uses public inherited/overridden method lookup and `XposedBridge.hookMethod`. Unit regression test and module build passed in the prior verification. The installed TikTok baseline was not replaced by this Agent build. |
| Backend compatibility | PASS | `py_compile` passed; identity/cloud-agent tests passed, including Android platform registration, rejection of Android on the Windows long-poll channel, and preservation of legacy `verify_agent` response shape. |
| Dashboard build | PASS | `npm run build` completed; Vite emitted only a chunk-size advisory. TypeScript issues in `LiveControl.tsx` and `LiveSessions.tsx` were corrected so the integrated dashboard can build. |
| Android build | PASS | `:sarae-agent:assembleDebug` succeeded. Gradle's `testDebugUnitTest` reports `NO-SOURCE`; physical ADB validation is recorded separately below. |
| Device install and activation | PASS | `adb install -r` succeeded. Device registered as Android through the existing identity endpoint using HTTPS. |
| Foreground service / WebSocket / heartbeat | PASS | ADB shows `AndroidAgentService` as foreground (`isForeground=true`, service type dataSync+mediaPlayback). Backend log shows accepted `/api/agents/ws/{agent_id}` and repeated `POST /api/agents/heartbeat` responses `200 OK`. |
| Backend MP4 upload and device sync | PASS | Two fixture MP4s returned `201 Created`; device then requested both content endpoints and the app displayed `2` downloaded videos after SHA-256 verification. See `test-results/android-agent-v1-data/server-log.txt` and screenshots. |
| Backend command delivery and ack | PASS | `queue_set`, `mock_live_start`, `play`, `switch after_current`, `pause`, and `resume` each finished with command status `SUCCESS`, `attempts=1`; details in `test-results/android-agent-v1-data/command-smoke-results.json`. |
| Local playback and rotation | PASS (smoke) | Phone screenshot shows decoded MP4 test pattern and `PLAYING`, with the current media ID changing between the 6.0 s and 4.0 s clips. `mock_live=true` is an app-local flag. |
| 60-minute continuous playback | INCOMPLETE (stopped at user request) | `test-results/android-agent-v1-endurance.jsonl`: 32 samples over 529.2 seconds (8m49s), 32/32 online, 31/32 PLAYING, 2 media IDs, 17 observed transitions. This is a short smoke run; the 60-minute criterion has not been met. |
| TikTok preview audio / LIVE audio | UNVERIFIED | Not tested in TikTok preview in this task; no LIVE was opened. A successful harness/player test does not establish that `TEAudioDataInterface.onReceive` is invoked by TikTok or that LIVE audio is audible. |

The broader pre-existing `test_owner_isolation` / `test_multi_agent` run did not pass in this environment (49 passed, 6 errors, 2 failures; product/session setup calls returned unexpected responses). Those product and session route implementations were not changed as part of Android Agent V1. Identity/cloud-agent tests and the focused Android platform test were run separately and passed.

## Git metadata

The repository's main Git status is readable with `--ignore-submodules=all`. The nested `vendor/react-ui-core/.git` pointer and the submodule's `core.worktree` path both resolve correctly. After access to `.git/modules/vendor/react-ui-core` was granted for inspection, `git rev-parse` succeeded and the Web UI preparation script reported the exact submodule commit. No Git metadata repair was necessary; the earlier `Permission denied` came from restricted tool access to `.git/modules`, not a stale `gitdir`. Existing staged and unstaged source changes were left in place.

## Artifacts

- APK: `android/test-results/SaraeAndroidAgentV1-debug.apk`
- Baseline: `android/test-results/baseline-20261020/`
- Device screenshots: `android/test-results/android-agent-v1-first-screen.png`, `android-agent-v1-synced-screen.png`, `android-agent-v1-playing.png`
- Endurance sample log: `android/test-results/android-agent-v1-endurance.jsonl`
- Local test backend/credentials and TLS keys are isolated under `android/test-results/android-agent-v1-data/` and `android/test-results/android-agent-v1-tls/`; private credentials/keys are Git-ignored.
