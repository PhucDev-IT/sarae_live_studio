# Android VCAM tools

This folder contains the Compose playlist manager, the offline audio test harness, the VCAM hook source, and the shared PCM implementation. No TikTok LIVE testing is required or performed by the harness.

## Projects

- `VCAMManager/` — Jetpack Compose app for choosing, ordering, looping, and applying the `DCIM/Camera1` playlist; includes the Audio Test screen.
- `audio-core/` — the single `VirtualAudioFeeder` implementation used by both the hook project and test harness, plus PCM metrics and WAV writing.
- `VCAMInjectionModule/` — the Xposed/LSPosed module. Its source set compiles the shared `audio-core` sources directly, so it does not maintain a second feeder implementation.
- `test-fixtures/VCAM-harness-av-10s.mp4` — generated test clip with H.264 video and 44.1 kHz AAC audio.
- `test-results/` — build/test artifacts and the Markdown test report.
- `docs/patch-target.md` — instructions for patching the TikTok target APK.

## Build

From PowerShell:

```powershell
cd android/VCAMManager
./gradlew.bat --no-daemon :app:assembleDebug

cd ../VCAMInjectionModule
./gradlew.bat --no-daemon :app:assembleDebug
```

Set `local.properties` to the local Android SDK path if Android Studio has not created it. Build outputs stay in each project's ignored `build/` directory.

## Offline audio test

1. Install the VCAM Manager debug APK and open **Audio test**.
2. Select `test-fixtures/VCAM-harness-av-10s.mp4` (or another MP4 with an audio track).
3. Tap **Start / Replay**, listen, then **Stop**. **Xuất WAV** saves the PCM emitted by the same feeder used by the hook. **Export Logs** saves format, RMS/peak, processing latency, dropped-buffer count, read count, and timestamp drift.
4. For a 30-minute endurance run, enable the mute checkbox if audible output is unwanted, keep the screen awake, tap Start, and stop after 30 minutes.
5. Change the selected MP4 while a run is active and tap Start / Replay to test switching sources.

The harness result validates decoding, PCM conversion, WAV output, and local AudioTrack playback. It cannot establish whether TikTok invokes the private `TEAudioDataInterface.onReceive` callback offline or whether TikTok LIVE receives audio. Those remain `NOT_TESTABLE_OFFLINE` and `UNVERIFIED` until evidenced in the applicable TikTok context; this workflow never opens or starts a LIVE.

## Manager behavior

The manager stores selected document URIs for later sessions. **Apply playlist** copies them to `DCIM/Camera1/playlist/001.mp4`, `002.mp4`, etc., writes `DCIM/Camera1/virtual.mp4`, and updates `playlist.txt` when looping is enabled. Android 11+ requires the app's “All files access” special permission for this legacy shared path.
