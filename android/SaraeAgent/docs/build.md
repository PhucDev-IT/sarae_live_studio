# Build, run, and migration notes

## Project root

Open `android/SaraeAgent` directly in Android Studio. Do not open `android/` as a Gradle root. The only launcher activity is the `app` module with application ID `co.sarae.agent`; Android Studio creates the normal `app` Run configuration from that application module.

The project uses the included Gradle wrapper, Java 17 source compatibility, Android SDK 37 compile target, Kotlin, Compose, and Material 3. The Android SDK location is machine-specific and belongs in the ignored `local.properties` file. On a fresh workstation, install the SDK platform/build tools requested by Gradle, open the project root, and let Gradle Sync finish before running `app`.

```powershell
.\gradlew.bat :app:assembleDebug
adb install -r .\app\build\outputs\apk\debug\app-debug.apk
```

## Modules

- `app`: sole user-facing Android Agent, backend connection foreground service, dashboard, and VCAM source bridge.
- `core/audio`: PCM feeder, conversion, metrics, and WAV writer shared by the harness and hook.
- `core/network`: backend API, device registration model, and encrypted DataStore credential repository.
- `feature/playback`: player runtime and `PlaybackViewModel` state exposed as `StateFlow`.
- `feature/vcam`: migrated playlist editor and offline Audio Test Harness. The app opens it as an internal screen; it has no launcher entry of its own.
- `injection/app`: technical Xposed/LSPosed module APK with the existing Camera1/Camera2 and audio hooks. It has no launcher entry and is not a second controller.

Only `:app:assembleDebug` is needed for regular Agent development. Build the injection APK separately when installing or updating the hook module:

```powershell
.\gradlew.bat :injection:app:assembleDebug
```

## Verification

The offline audio format matrix is now under `app/src/androidTest` and runs against the integrated controller APK:

```powershell
.\gradlew.bat :app:connectedDebugAndroidTest
```

It exercises two fixture input formats against four output profiles and checks PCM, AudioTrack writes, underruns, and timestamp drift. It does not test TikTok callbacks or LIVE audio. ADB camera-hook checks must use a test camera preview; do not start a TikTok LIVE session.

The project root should be the only Android Studio project opened for Android Agent work. The previous `VCAMManager` and `VCAMInjectionModule` directories remain in the monorepo as migration baselines until integrated builds and device checks pass. Their sources were preserved during intake, and they should only be removed after the matching integrated features have been verified.

## Existing backend behavior

The controller continues to use the existing Sarae backend and Web Dashboard. It registers as `ANDROID`, sends heartbeat and WebSocket command acknowledgements, downloads SHA-256-verified video assets, and supports playback, queue, auto rotation, and Mock LIVE commands. Product sync from TikTok is intentionally not implemented on Android. The Windows Agent and backend are unchanged.
