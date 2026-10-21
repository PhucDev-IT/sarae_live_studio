# TikTok VCAM pre-LIVE readiness

**Checked:** 2026-10-21 (Asia/Saigon)  
**Requested target:** Samsung SM-G996U, Android 14, TikTok package `com.zhiliaoapp.musically` 47.4.3  
**Rule:** no LIVE was started. No TikTok package was installed, removed, patched, or otherwise changed.

## Decision

**PRE-LIVE READY: NO.** The project now targets the installed TikTok package and has explicit camera-source requests, Camera2 session-state reporting, an orientation diagnostic path and expanded audio counters. Although `com.zhiliaoapp.musically` 47.4.3 is installed, preview orientation, REAL↔VIRTUAL transitions, lens changes and audio injection have not yet been verified on this build. Do not treat build success, a rendered test clip, or Audio Harness playback as readiness evidence.

Read-only ADB confirms `com.zhiliaoapp.musically` version 47.4.3 is installed on the SM-G996U. `com.ss.android.ugc.aweme` version 40.8.0 (Douyin) is also installed but is not the target for this run. Neither app was changed.

## Orientation and scale

### Source-path audit

The current Camera2 preview path has two different media paths:

1. `MediaPlayer` decodes the selected MP4 and renders directly to the original TikTok preview `Surface` captured from `CaptureRequest.Builder.addTarget`. The Camera2 output list/request is redirected to the injection sink surface while VIRTUAL_VIDEO is active.
2. `VideoToFrames` uses `MediaExtractor` + `MediaCodec` for ImageReader-style outputs. It decodes to the configured `play_surf`; it does not establish the orientation of the MediaPlayer preview path.

There was no metadata logging for MP4 rotation, decoded output size, or camera sensor orientation, and the preview render did not explicitly request aspect-fit. Therefore the old implementation could not distinguish a rotation-metadata issue from a target-surface aspect/scale issue. **The runtime root cause is not established without observing the requested TikTok preview.**

### Changes

- The preview MediaPlayer now requests `VIDEO_SCALING_MODE_SCALE_TO_FIT`, which avoids asking the renderer to crop to fill.
- Logs now include `MediaMetadataRetriever` encoded width/height and rotation, the decoded `onVideoSizeChanged` dimensions, Camera2 camera ID, sensor orientation and lens-facing metadata. The code records that it applies no explicit rotate, mirror, crop, or `SurfaceTexture` transform to preview video.
- No 90°/180° rotation or front-camera mirroring was added by guess. The actual TikTok preview may itself mirror its surface; that behavior remains to be compared with the test card.
- Added a four-second, 720×1280 (9:16) reference clip with TOP/BOTTOM/LEFT/RIGHT labels, thin colored borders and four differently colored corners. `ffprobe` reports 720×1280 and 4.000 seconds; no non-zero rotation metadata is present. This validates the test asset, not TikTok rendering.

Reference frame:

![Portrait orientation test reference](../test-results/prelive/orientation-portrait-reference.png)

Test clip: [`orientation-portrait-9x16.mp4`](../test-results/prelive/orientation-portrait-9x16.mp4) (local ignored test artifact).

| Check | Result |
|---|---|
| Test card dimensions/labels/corner markers | **PASS** — local reference frame inspected; ffprobe 720×1280, 4.000 s |
| MP4 rotation/decoded-size/camera-sensor diagnostics in code | **PASS** — instrumentation added; values require a target preview log to be observed |
| TikTok preview upright, correct aspect, no unexpected mirror/crop | **UNVERIFIED** — no screenshot/video evidence from package 47.4.3 |
| Orientation acceptance criterion | **NOT PASSED** |

## REAL_CAMERA / VIRTUAL_VIDEO and recovery

SaraeAgent now exposes `REAL_CAMERA` and `VIRTUAL_VIDEO` requests. It writes the request to `DCIM/Camera1/sarae-source-mode.txt`; the existing `disable.jpg` control takes precedence and continues to force REAL_CAMERA. If the request is missing, invalid, or the virtual MP4 is absent, the injected camera mode fails closed to REAL_CAMERA.

The injected process snapshots the selected mode when a camera opens. It does not mutate the output configuration of an already configured Camera2 session in response to a button press. This is deliberate: a CameraCaptureSession has a fixed configured output set, so a status-file change alone cannot safely turn an existing real output into a virtual output or vice versa. To change modes, request the new mode, close TikTok’s camera preview, then open its preview again. It is not a hot switch.

On REAL_CAMERA, all supported Camera2 session creation overloads keep TikTok’s supplied outputs and callbacks intact; request targets are not redirected, and VCAM playback does not start. `REAL_CAMERA_ACTIVE` is reported only from the Camera2 `onConfigured` callback. In VIRTUAL_VIDEO, the injection keeps the original callback while changing session outputs and request targets; the status sequence distinguishes camera open, configured virtual session, and MediaPlayer’s video-rendering callback. The SaraeAgent receiver accepts status broadcasts only when the sender UID belongs to `com.zhiliaoapp.musically`.

When a new camera opens, the old MediaPlayers are released and `VideoToFrames` decoders are signaled/interrupted; a stopped decoder releases its output buffer without rendering and exits through its codec cleanup path. The code does not release TikTok’s original preview surface. A virtual sink is recreated only as a new camera opens. These recovery behaviors have **not** been exercised in the requested TikTok package on device.

| Transition/check | Result |
|---|---|
| Fail-closed mode selection and missing-MP4 fallback | **PASS** — policy unit tests |
| REAL_CAMERA preserves original outputs and callback | **IMPLEMENTED; UNVERIFIED at runtime** |
| VIRTUAL_VIDEO route and render status | **IMPLEMENTED; UNVERIFIED at runtime** |
| REAL → VIRTUAL, VIRTUAL → REAL, repeated changes | **UNVERIFIED** — requires preview restart in `com.zhiliaoapp.musically` 47.4.3 |
| Front/rear switch, hide/reopen preview | **UNVERIFIED** |
| Camera remains active after returning to REAL_CAMERA | **UNVERIFIED** — requires REAL_CAMERA `onConfigured` status from target |
| Safe hot switching within the running session | **NOT SUPPORTED** — close/reopen preview is required |

## Audio diagnostics

Existing Audio Harness behavior is unchanged. The injection now logs, by path:

- whether each `AudioRecord.read` overload hook registered;
- hook read/callback counts, PCM bytes fed, underrun buffers, zero-output/drop buffers, sample rate/channels and `AudioRecord` recording state;
- the routed Android audio device type when available, reported as inactive/unknown when it cannot be read;
- `TEAudioDataInterface.onReceive` hook registration/invocation counts, sample rate/channels, fed bytes, short reads, PCM RMS/peak, processing time and sample timestamp monotonicity/delta.

`AudioRecord`/`TEAudioDataInterface` PCM replacement is the capture/injection path. Audio Harness `AudioTrack` output and the MediaPlayer speaker path are playback routes. Hearing the clip from the phone speaker, a successful WAV export, or a registered hook does **not** show that TikTok supplied the same samples to a LIVE receiver.

| Audio check | Result |
|---|---|
| Harness playback/decoder regression | Existing instrumented tests are present but were not run in this task |
| Hook registration/callback/format/bytes/underrun/timestamp/route diagnostics | **IMPLEMENTED; UNVERIFIED** on requested package |
| MP4 audio reaches TikTok LIVE capture | **UNVERIFIED** — no LIVE was started |
| Independent receiver confirmation | **NOT RUN** |
| AUDIO LIVE PASS | **NOT CLAIMED** |

### Receiver-side procedure for a future authorized LIVE

1. Before LIVE, verify the target version/package, capture a local logcat baseline, and confirm VIRTUAL_VIDEO renders correctly in TikTok preview; separately confirm REAL_CAMERA returns to a working camera preview.
2. Connect a second, independent receiving device/account before any LIVE starts. Use a controlled non-public/private test mode if TikTok offers one for that account; otherwise wait for the user to authorize a compliant session. This task does not launch it.
3. Start with a short MP4 containing a distinctive tone and speech-free interval, compare the receiver’s audio to that reference, then stop the session manually. Save sender injection logs and receiver recording/time markers together.
4. Call audio successful only if the independent receiver hears/records the expected MP4 signal with continuous timestamps and no material underruns. Local playback and callback counts remain supporting diagnostics only.

## Samsung ADB observations

Read-only ADB was available after starting the local ADB server. Observed facts:

- device: `RFCR10DGF1T`, model `SM_G996U`, Android release `14`;
- Android reports `android.hardware.usb.host` and the built-in camera features;
- `pm list features` did not report `android.hardware.camera.external`;
- CameraService reports four public camera devices when checked with no UVC source attached;
- `com.zhiliaoapp.musically` 47.4.3 is installed with Camera and Record Audio permissions granted; `com.ss.android.ugc.aweme` 40.8.0 (Douyin) is also present. The target is `com.zhiliaoapp.musically`.

These observations do not prove that an external camera can never be supported by another Samsung firmware or by a separate USB Camera app. They show no external-camera framework feature in this checked system image. No UVC device was attached, and no TikTok camera preview was opened during the preceding verification.

## Build and test results

The following tasks were requested and run using the project Gradle distribution and its local `.gradle-home` cache:

- `:app:assembleDebug` — **PASS**
- `:injection:app:assembleDebug` — **PASS**
- `:injection:app:testDebugUnitTest` — **PASS**, including existing Camera2 callback method-resolution tests and new fail-safe camera-mode policy tests
- ADB target presence and platform-feature queries — **PASS** (read-only; `com.zhiliaoapp.musically` 47.4.3 present)
- TikTok preview, injection runtime, repeated switching, lens change, hide/reopen and receiver audio — **NOT RUN / UNVERIFIED**

No APK was installed or removed. The installed TikTok app and its private data were not changed. No LIVE was started. The Android Agent source and Windows Agent/backend were not changed outside this Android project task.

## Before considering PRE-LIVE READY

1. Confirm the injection build is installed and enabled for `com.zhiliaoapp.musically` 47.4.3 without removing or replacing either existing TikTok installation or risking app data.
2. Ensure the existing injection environment has the Sarae injection module enabled for that exact package. Then test preview only, not LIVE.
3. Copy the orientation test clip into `DCIM/Camera1/virtual.mp4`. Capture TikTok preview screenshots for both camera orientations if the app supports changing lenses. Compare the four labels and corner colors, and correlate them with logs for source rotation, decoder dimensions, target surface dimensions (if observed), sensor orientation and lens-facing. Only then adjust a transform if evidence identifies it.
4. Execute REAL_CAMERA → VIRTUAL_VIDEO → REAL_CAMERA with preview close/reopen between requests; repeat several times, switch front/rear, and hide/reopen TikTok. Each REAL_CAMERA pass requires a fresh `REAL_CAMERA_ACTIVE` session callback and visible live camera frames. Each virtual pass requires `VIRTUAL_VIDEO_RENDERING` plus a correct preview.
5. Review audio logs in a non-LIVE capture diagnostic. Arrange a separate authorized receiver-side test before reporting any LIVE audio result.
6. Update this report with screenshot/log evidence and pass the transition checklist before changing the decision to PRE-LIVE READY.
