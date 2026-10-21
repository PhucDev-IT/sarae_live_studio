# Sarae LIVE Studio Android Agent

Open this folder (`android/SaraeAgent`) in Android Studio. It is the Android Studio project root; `android/` is only the monorepo platform folder. Android Studio discovers the `app` Run configuration from the sole user-facing application module (`co.sarae.agent`). Select a connected device and run `app`.

Build the controller from PowerShell:

```powershell
.\gradlew.bat :app:assembleDebug
```

Build the injection module when preparing a technical Xposed/LSPosed APK:

```powershell
.\gradlew.bat :injection:app:assembleDebug
```

The controller includes backend registration, HTTPS/WSS connection, heartbeat, video sync, local playback and queue controls. `feature:vcam` brings the legacy playlist editor and offline audio test harness into the controller. `core:audio` is the shared PCM decoder/converter, metrics, and WAV writer used by both the test harness and injection hooks. `feature:playback` owns local playback state and its StateFlow ViewModel. `core:network` owns encrypted device credentials in DataStore and the existing backend API client. `injection:app` retains the camera/audio hook implementation as a technical APK and has no launcher entry point.

The app does not sync products from TikTok, and its Mock LIVE flag only exercises the local player and existing backend. Do not use TikTok LIVE for development verification. Audio callback delivery to TikTok remains unverified by the offline harness.

Detailed module, SDK, device-test, and migration notes are in [docs/build.md](docs/build.md).
