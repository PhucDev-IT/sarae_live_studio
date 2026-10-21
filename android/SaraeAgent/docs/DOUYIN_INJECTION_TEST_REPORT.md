# Douyin 33.6.0 — VCAM integration and test report

Date: 2026-10-20 (Asia/Saigon). Device: Samsung SM-G996U, Android 14, ADB `RFCR10DGF1T`.

## Final outcome: patched app startup FAIL; original app restored

**Injection build passed and LSPatch loaded the module, but patched Douyin lagged and crashed repeatedly before camera preview.** The user reported this directly. Native tombstones at 18:12:09, 18:12:30 and 18:13:02 (Asia/Saigon) show `SIGSEGV` in `libart.so` / Douyin `libmetasec_ml.so`. The trace does not prove which patch/hook operation triggered the fault. No Camera2 callback or injected video frame was observed. The patched APK was removed and the saved original Douyin 33.6.0 APK was reinstalled successfully; original signing certificate restored. The original process remained alive during a short launch smoke. Earlier uninstall already removed the old private app data; reinstalling the APK does not restore login or drafts.

| Stage | Result | Evidence |
|---|---|---|
| Device/package/version | PASS | `com.ss.android.ugc.aweme`, 33.6.0 / code 330601; metadata in `../../test-results/douyin-study-20261020/device-package-metadata.txt` |
| Original APK backup | PASS | `../../test-results/douyin-study-20261020/base.apk`, SHA-256 `69B639EBC289B8090DED33AEE0AFA564259186C10B36F939DD093213DEAFA3C7` |
| Xposed scope | PASS (source) | `injection/app/src/main/res/values/array.xml` already contains `com.ss.android.ugc.aweme`; manifest has `xposedmodule=true`, entry at `assets/xposed_init` |
| Experimental Douyin target/source configuration | PASS (build only) | The tested APK admitted Douyin, logged `VcamDouyin` + classloader, and selected shared `/sdcard/DCIM/Camera1/` source. The experimental source edit was reverted after the crash; TikTok audio branch stayed TikTok-only. |
| SaraeAgent separate app | PASS (launch smoke) | Existing `android/SaraeAgent/app/build/outputs/apk/debug/app-debug.apk` was installed as `co.sarae.agent` and launched with ADB; PID 29149 remained alive after 10 seconds, with no `FATAL EXCEPTION` for that process in sampled logcat. This does not verify media selection or Douyin injection. |
| Injection build | **BUILD PASS** | `./gradlew.bat :injection:app:assembleDebug --no-daemon --console=plain` returned `BUILD SUCCESSFUL in 1m 25s`. Build APK SHA-256 `0BE629EFCA1872B33ECA172EA76BA48033223BEFA4A04F0EC50530BF11FF5B8C` (22,416,448 bytes). |
| Camera2 resolver unit regression | **BLOCKED BY CONCURRENT BUILD**, not code FAIL | A focused `testDebugUnitTest` attempt hit a Windows file lock on `bundleDebugClassesToCompileJar/classes.jar`; see `../../test-results/douyin-study-20261020/camera2-resolver-unit-test.log`. No Gradle clean or forced process termination was done because another Agent is working in this project. Earlier baseline resolver test had passed. |
| LSPatch APK preparation/install | PASS (install only) | Embedded module with LSPatch 1.2/487, no signature bypass; installed after user approval. `../../test-results/douyin-study-20261020/patched/base-487-lspatched.apk`, SHA-256 `46813F2AC9B41F3FE250C1D08FCB797B7EC80583BCB483A3EF29708FF61B7EB2` (315,191,088 bytes). `apksigner verify --verbose` passed v2; package/version remain `com.ss.android.ugc.aweme` / 33.6.0. |
| Module loaded / package recognized | **PASS** | LSPatch extracted `io.twinkle.unreal`, loaded `HookMain_YukiHookXposedInit`; `VcamDouyin` logged `target=com.ss.android.ugc.aweme` with the origin APK `PathClassLoader`, and `VcamPro` logged `/storage/emulated/0/DCIM/Camera1/`. See `douyin-hook-startup.log`. This establishes module entry, not Camera2 callback. |
| Camera2 callback observed with module | **NOT OBSERVED** | Patched app crashed before camera preview. Prior unpatched preview showed Android `CameraDeviceClient[0]`, which proves API use, not this hook callback. |
| MP4 in Douyin preview | **NOT TESTABLE ON THIS BUILD** | Patched Douyin lagged/crashed before preview; no injected frame was seen. |
| Audio capture/injection | **UNKNOWN / NOT TESTED** | Deferred until camera video passes. No Douyin LIVE started. |
| Patched startup stability | **FAIL** | Patched Douyin PIDs 31972, 8049 and 9489 crashed with native `SIGSEGV` in `libart.so` / `libmetasec_ml.so` before preview. See `douyin-crash-buffer.txt`, `douyin-crash-filtered.txt`; user confirmed severe lag/crash. TikTok baseline untouched. |
| Original Douyin restore | **PASS (short smoke)** | `adb uninstall` patched app and `adb install` saved original both returned `Success`. Installed signer returned to `5182…2512e`; version remains 33.6.0. Original PID 12048 stayed alive through a short check. Private app data from before the first uninstall are not restored. |

## Source inspection, experimental change and rollback

The experimental build edited only `android/SaraeAgent/injection/app/src/main/java/io/twinkle/unreal/HookMain.kt`. After the patched app crashed, those edits were reverted; the current file was compared byte for byte with `android/test-results/douyin-study-20261020/HookMain-before-douyin.kt` and matched. The experimental version remains at `android/test-results/douyin-study-20261020/HookMain-douyin-experiment.kt`. The separate Android Agent's in-progress `app`, `core`, `feature`, Gradle and Git state were not reset/committed/cleaned.

- Experimental package gate: admitted **only** the confirmed Douyin package in addition to existing TikTok and lab probe defaults; the gate is now restored to its pre-test state.
- `xposed_scope` already listed Douyin; no manifest/resource or build configuration change was needed.
- Experimental video source: selected `DCIM/Camera1/virtual.mp4` and `playlist/NNN.mp4` for Douyin. This selection is no longer active in current source. The existing file on device was backed up as `virtual-before-test.mp4` (4,556,070 bytes; SHA-256 `0B9E801D85D6AFA1F592EF50CEDD4CA6981CDE0F7FEBA2ADB0A05307A4CE7B3D`). Playlist file currently names `001.mp4` and `002.mp4`. The device files were not overwritten.
- The experimental camera hooks reused the existing Camera1/Camera2 implementations: `CameraDevice.StateCallback.onOpened`, Camera2 session creation/callbacks, surface substitution, preview playback/playlist logic, and Camera1 fallback. The experimental `VcamDouyin` log reported package and app classloader at module entry. Existing `VcamPro` logs can show session and player activity once a patched build is installed.
- Audio hooks (`AudioRecord.read`, lifecycle and `TEAudioDataInterface.onReceive`) remain inside `isTikTokTarget`. Douyin has the same VE callback in DEX, but this task intentionally has not enabled PCM writing for Douyin before video validation. Static details are in `DOUYIN_COMPATIBILITY_REPORT.md`.
- Source reload behavior: playlist is read/started as the camera session starts. Applying a new queue does not prove an already-open Douyin preview hot reloads; close/reopen preview would be required for a controlled test.

## Signing and installation stop condition

Installed/original Douyin signing certificate SHA-256: `5182ae3b1b85337bb182cf24882449f84447ded18e296a747a9f6a0a2622512e`.

Prepared LSPatch Douyin signing certificate SHA-256: `c081890cf2a1adf13e56d7b50a4f3d8edb35b7c46d6ccc732dd997b7e433be1d`.

The package ID is identical and the signatures differ. Android cannot update in place (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`) or keep two installations of the same package in the current package namespace. After the user explicitly accepted possible loss of login/drafts/private data, original uninstall and patched install both returned `Success`. The patched app then crashed repeatedly. It was uninstalled and the saved original `base.apk` reinstalled; installed signer is again `5182…2512e`. Private app data from before the first uninstall were not backed up and cannot be restored from the APK. No signature bypass was used (`sigBypassLevel=0`).

The LSPatch archive's `assets/lspatch/origin.apk` uses an overlapping ZIP-entry layout that Python `zipfile` refuses to read; the same occurs in the previously working TikTok LSPatch baseline. `apksigner` and `apkanalyzer` successfully read/verify the Douyin output. This is not evidence of runtime success.

## Remaining limits and safe next research

- This LSPatch build **must not be treated as a working Douyin VCAM build**. Its native startup crash prevents camera and audio runtime conclusions. Module load is not camera-hook success.
- The `libmetasec_ml.so` stack is consistent with a native failure in a patched process, but cannot distinguish a module interaction from LSPatch/repackaging by itself. Do not disable Douyin detection, integrity or protection mechanisms to force it to run.
- Keep using the restored original Douyin for normal use. Any new Douyin patch experiment would require a separate risk decision; no further uninstall/reinstall was attempted after restoration. Continue camera/audio research with the saved APK, static analysis and isolated probe apps rather than LIVE.
- Re-run the focused Camera2 resolver test when no concurrent Gradle process holds shared intermediates. The existing file-lock error is environmental, not a test assertion failure.
- Audio injection and Douyin LIVE audio remain **UNVERIFIED**.

## Evidence paths

All local artifacts are under `android/test-results/douyin-study-20261020/`: original and patched APKs, built Injection APK, `HookMain-before-douyin.kt`, `HookMain-douyin-experiment.kt`, MP4 backup, static DEX logs, CameraService preview evidence, and `camera2-resolver-unit-test.log`, `patched-launch-log.txt`, `patched-postlaunch-log.txt`, `douyin-hook-startup.log`, `current-screen.png`, `douyin-crash-buffer.txt`, `douyin-crash-filtered.txt`. They are Git-ignored. No code commit was made in this task.
