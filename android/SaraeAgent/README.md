# Sarae Android Agent V1

Android Agent is a separate Jetpack Compose app for managing Android playback from the existing Sarae FastAPI backend and Web Dashboard. It does not connect to TikTok or start a LIVE session. **Mock LIVE** is only a label/state inside the test player.

## Build

From `android/VCAMManager`:

```powershell
./gradlew :sarae-agent:assembleDebug
```

The app project is `android/SaraeAgent/app`; it is included as `:sarae-agent` in the existing Android Gradle build. The debug APK is `android/SaraeAgent/app/build/outputs/apk/debug/sarae-agent-debug.apk`.

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
