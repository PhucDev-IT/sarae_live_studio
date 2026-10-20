# Sarae Android Agent V1

Android Agent is a separate Jetpack Compose app for managing Android playback from the existing Sarae FastAPI backend and Web Dashboard. It does not connect to TikTok or start a LIVE session. **Mock LIVE** is only a label/state inside the test player.

## Open in Android Studio (standalone project)

Open **`android/SaraeAgent`** directly with **File → Open**, NOT `android/SaraeAgent/app` or `android/VCAMManager`.
This folder now has its own `settings.gradle.kts`, `build.gradle.kts`, version catalog, and Gradle Wrapper.
Wait for Gradle Sync, select the **app** run configuration and the connected device, then press **Run ▶**.
Android Studio must have a compatible JDK 17+ and Android SDK (compile SDK 37 as currently configured).

Build from PowerShell:

```powershell
cd D:\\Android_Programming\\sarae_tool_live_studio\\android\\SaraeAgent
.\\gradlew.bat :app:assembleDebug
adb install -r .\\app\\build\\outputs\\apk\\debug\\app-debug.apk
```

Only install/open the **Sarae LIVE Agent** controller app. `VCAMManager` is legacy and not needed for the Agent's local playlist apply action.
The Xposed injection module and patched TikTok remain separate technical components when the camera hook is used; Android cannot merge the patched target into the Agent APK. TikTok LIVE is not needed for Agent development.

## Connect a device

1. Create an Android activation key through the existing Sarae administration flow.
2. In the app, enter the backend HTTPS URL and one-time activation key.
3. Tap **Kết nối**. Android displays an ongoing foreground-service notification while heartbeat, HTTPS video sync, and WebSocket command delivery run.
4. Upload MP4s from the Web Dashboard's **Agents → Android Agent → Video Android** card. The app downloads by content SHA-256 into its private files directory.
5. Select files in the dashboard for a playlist, set auto rotation, and send controls. The local player also exposes play, pause, previous/next, sync, and after-current switching.

Only HTTPS/WSS endpoints are accepted. Production builds trust system certificates only. The debug variant additionally trusts the public test CA in `src/debug/res/raw/sarae_test_ca.crt` for the isolated localhost acceptance backend; its private key is excluded from Git.

## Backend contract

- Registration: `POST /api/identity/activate` with `platform=ANDROID`.
- Heartbeat/state: `POST /api/agents/heartbeat`.
- Commands: persistent `WSS /api/agents/ws/{agent_id}`, sharing the existing command queue and ack lifecycle.
- Media: owner upload/list/delete under `/api/android-agents/{agent_id}/videos`; device catalog and SHA-256-verified downloads under `/sync` and `/content`.
- Supported command names: `android_play`, `android_switch` (`immediate` / `after_current`), pause/resume/next/previous/stop, queue set, rotation set, and mock-live start/stop.

The server stores uploaded Android MP4 assets in its existing `DATA_DIR`; no second backend is introduced. Windows Agent behavior remains on its existing endpoints and command namespace.

## Offline/mock validation

Run the isolated local backend over HTTPS, use ADB reverse for localhost, upload test MP4s through the normal owner API, then use the Web Dashboard or the exact same owner command API. The endurance monitor in `scripts/endurance_monitor.py` records agent online state, playback state, current media transitions, foreground-service checks and app-process checks. It must not be interpreted as evidence about TikTok LIVE audio or the TikTok hook.

## Unified VCAM source control (Android Agent)

The Android Agent is the **only user-facing controller** needed for local media sync, queue controls and exporting that queue to the existing VCAM file convention. Product sync from TikTok is deliberately **not** part of the Android Agent. The separate injection module/TikTok patched package is still a technical prerequisite for non-root camera hook setups, not an independent management application.

1. Activate and connect the Agent, then sync the MP4 files from the backend.
2. Prepare a queue using the existing Agent local/Web control.
3. In the Agent, under **Nguồn VCAM**, grant Android's all-files access permission when prompted.
4. Tap **Áp dụng queue cho VCAM**. The Agent exports files to `DCIM/Camera1/playlist/001.mp4` etc., `virtual.mp4`, and `playlist.txt` when looping is enabled.
5. Reopen the **test camera preview** to check whether the hook has picked up the changed source. Applying a queue does not guarantee a live camera session reloads its source.

**Limits:** The Agent's local MediaPlayer playback and queue commands are separate from the VCAM camera hook. The explicit apply step is required; there is currently no verified direct hot-switch from backend to a running TikTok camera session. This feature neither opens TikTok LIVE nor verifies TikTok audio. Do not use LIVE accounts to debug.
