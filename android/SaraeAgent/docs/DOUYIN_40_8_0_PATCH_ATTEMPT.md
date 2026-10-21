# Douyin 40.8.0 VCAM patch attempt — 2026-10-20

Device: Samsung SM-G996U, Android 14, ADB RFCR10DGF1T. No LIVE was started.

## Result

**PATCHED STARTUP FAIL; ORIGINAL 40.8.0 RESTORED.** The LSPatch build installed and loaded the experimental VCAM module, but the Douyin process crashed before login or camera preview. The original 40.8.0 APK was reinstalled and launched; PID 24779 remained alive during a short smoke check with `BulletContainerActivity` resumed. Long-term stability and login were not verified.

| Check | Status | Evidence |
|---|---|---|
| Input APK | PASS | `com.ss.android.ugc.aweme`, 40.8.0 / code 400800; SHA-256 `0C693B17830E7A799B8C4A9E3D736B3402525C54C54AAD13274FE974EC821D43`; original signer SHA-256 `5182ae3b1b85337bb182cf24882449f84447ded18e296a747a9f6a0a2622512e` |
| Experimental patch | BUILD/INSTALL PASS | LSPatch 1.2/487, embedded `VCAMInjection-Douyin-debug.apk` from the earlier 33.6.0 study, signature bypass level 0; patched APK SHA-256 `0C9236613842B5E975F7249D9FD6B177A20BC99C4CB1A4CFF59F760BC93299B7`; package/version verified before install |
| Module load | PASS | `patched-launch-logcat.txt`, 18:26:31–18:26:34: LSPatch extracted and initialized module; `VcamDouyin` logged target and app `PathClassLoader`. This is not evidence of a Camera2 callback. |
| Startup | FAIL | 18:27:02 `SIGSEGV` in PID 22766, thread 22885; tombstone at 18:27:05: `libart.so art::JavaVMExt::VisitRoots` and Douyin `libmetasec_ml.so` offset `0x1f1ba0`. See `patched-crash-buffer.txt` and `patched-launch-logcat.txt`. The same failure area appeared with patched 33.6.0, but the exact cause is unproven. |
| Camera injection | NOT TESTABLE | Crash occurred before preview. No camera callback or MP4 frame verified. |
| Audio injection / LIVE | UNVERIFIED | Audio injection was disabled in the experimental Douyin module; no LIVE attempted. |
| Restore | PASS (short smoke) | Patched app uninstalled; original 40.8.0 APK installed. Version 40.8.0 and original signer checked; process remained alive for the short observation. |

LSPatch also logged `Original AppComponentFactory not found: com.ss.android.ugc.aweme.app.host.AwemeAppComponentFactory`; this warning alone does not identify the crash cause. The process reached module initialization before the native fault. Do not infer that changing the camera hook will fix this startup failure. No protection or signature bypass was attempted.

The uninstall of 33.6.0 removed its private app data; installing either 40.8.0 build does not restore that data. Because the patched build crashed before login, the working original 40.8.0 was left installed for the user's login. Do not replace it again while the user is away.

## Artifacts

All paths below are relative to `android/test-results/douyin-40.8.0-20261020/`:

- `douyin-40.8.0-original.apk`: saved original input and current installed build.
- `patched/douyin-40.8.0-original-487-lspatched.apk`: failed experimental output.
- `patched-launch-logcat.txt`, `patched-crash-buffer.txt`: startup and tombstone evidence.
- `after-restore-logcat.txt`, `after-restore-crash-buffer.txt`: post-restore snapshots. The crash buffer retains the earlier patched crash; it was not cleared.
- `installed-before-path.txt`, `installed-before-metadata.txt`: 33.6.0 state before uninstall.

The experimental module source was reverted after the 33.6.0 attempt; current `HookMain.kt` matches its pre-experiment backup. No source, Gradle, or Git changes were made for this 40.8.0 attempt beyond this report and ignored test artifacts.