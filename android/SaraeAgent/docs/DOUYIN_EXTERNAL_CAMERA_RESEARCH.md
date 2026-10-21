# Douyin stock app: external camera input research

**Updated:** 2026-10-20 (Asia/Saigon)  
**Target:** Samsung SM-G996U, Android 14; Douyin `com.ss.android.ugc.aweme` 40.8.0; Windows PC with OBS/FFmpeg. TikTok Vietnam is out of scope.

## Executive finding

There is no verified, no-root/no-patch path in the evidence collected that feeds a PC/OBS/MP4 source into the **stock Douyin Android camera** on this device. A USB UVC camera can be read directly by a purpose-built app, but that alone does not make it a CameraService camera or make Douyin select it. Android supports USB cameras through Camera2 only when the device image includes and enables the External Camera Provider, kernel UVC support, permissions and configuration. This is an OEM/system-image feature, not an app permission that SaraeAgent or another ordinary app can add.

The best no-purchase route is to use Douyin’s official PC Live Companion on the PC, if the account qualifies, or an official phone/game-screen-live flow if the Douyin app exposes it for that account. Those routes publish through Douyin’s supported creator workflow; they do not make a PC video source appear as the phone’s camera. No LIVE was started and no account rights were checked.

## Status vocabulary

- **SUPPORTED BY ANDROID**: Android platform/framework documentation describes the capability. For external cameras this is conditional on the phone manufacturer implementing it.
- **SUPPORTED BY DOUYIN**: Douyin’s official documentation describes a product path. It does not mean every account/device has access.
- **VERIFIED ON DEVICE**: directly observed on the specified phone and app/version.
- **NOT VERIFIED**: no device-specific proof was available. Do not infer Douyin compatibility from Android capability or a separate USB-camera app.

## Platform facts and limits

Android’s AOSP documentation describes external USB webcams exposed through the standard Camera2 API and camera HAL. It requires USB host capability, kernel UVC/video support, an external camera provider, vendor configuration and SELinux/device integration. The provider enumerates USB availability and publishes camera devices through the system camera framework. AOSP characterizes this feature as aimed at lightweight use cases, not demanding high-resolution/high-speed camera workloads. [AOSP: External USB cameras](https://source.android.com/docs/core/camera/external-usb-cameras)

Android’s USB Host API separately lets an app enumerate USB devices and communicate with their interfaces after user permission. That is a direct app-to-USB path; it is not CameraManager registration. [Android Developers: USB host](https://developer.android.com/develop/connectivity/usb/host) CameraManager returns the camera devices currently exposed by the camera framework, including removable cameras when the platform has exposed them. [Android Developers: CameraManager](https://developer.android.com/reference/android/hardware/camera2/CameraManager#getCameraIdList())

The Camera HAL/provider sits between framework camera APIs and device drivers. A regular third-party app does not have a public API to install/register a new Camera HAL/provider or inject a virtual device into CameraService. Doing so requires OEM/system-image integration or privileged system access; on a locked, non-root stock phone, an app that merely reads USB cannot grant Douyin a new system camera. [AOSP: Camera HAL](https://source.android.com/docs/core/camera/camera3)

The AOSP documentation is not evidence that Samsung enabled this feature on the SM-G996U firmware in question. Samsung’s product specifications establish the USB-C hardware connector, but the material found does not establish UVC-to-CameraService support for this exact model/firmware. USB-C/OTG host capability must not be treated as proof of camera framework enumeration.

## Results on the Samsung device

The current ADB server could not start during this research (`could not read ok from ADB Server` / `cannot connect to daemon`), so no fresh device command or Douyin UI observation was possible. No UVC device was attached or available for a physical test. No hardware was purchased.

Existing local evidence in `android/test-results/douyin-study-20261020/` confirms that this handset was previously identified as SM-G996U on Android 14 and that an installed **Douyin 33.6.0** camera preview used CameraService camera IDs 0/1. It does not list CameraManager IDs with a UVC peripheral connected, does not test USB Host enumeration, and does not prove anything about the requested 40.8.0 build. The requested 40.8.0 package/version is therefore **NOT VERIFIED ON DEVICE**. The older preview evidence is useful only to show that Douyin used an ordinary system camera in that older preview session; it does not show an external-source selector.

To verify the phone’s external-camera support later without modifying it, connect an already-owned UVC device through a powered OTG adapter, record `pm list features` for `android.hardware.usb.host` and `android.hardware.camera.external`, capture `dumpsys media.camera`, and query `CameraManager.getCameraIdList()` from a read-only diagnostic app. Compare IDs before and after connection. Do not call a USB-camera app preview a CameraService result. Then, with the original Douyin 40.8.0 installed, inspect only its non-LIVE camera preview/source controls. The current access failure prevented these checks.

## Route assessment

| Phương án | No root | No patch | Douyin nhận thực tế | Audio | Kết luận |
|---|---|---|---|---|---|
| USB UVC | Yes | Yes | **NOT VERIFIED** on this phone/Douyin. Douyin can only receive it as a normal camera if Samsung’s CameraService exposes it and Douyin enumerates/selects it. | Some UVC devices also carry USB Audio Class audio; support/routing depends on hardware, firmware and app. No automatic Douyin audio path established. | **SUPPORTED BY ANDROID (conditional)** for devices with external provider; **NOT VERIFIED** for Samsung/Douyin. Do not buy a UVC camera yet. |
| HDMI Capture → USB-C OTG/UVC | Yes | Yes | **NOT VERIFIED**; HDMI-to-UVC capture is still just a UVC source from Android’s perspective, subject to the same provider and Douyin limitations. | HDMI embedded audio may be exposed as USB audio by a particular capture device; not guaranteed and no Douyin routing verified. | **NOT VERIFIED**. No evidence justifies purchasing a capture dongle. |
| OBS Virtual Camera | Yes on PC | Yes | **NOT VERIFIED / no direct Android path**. OBS virtual camera creates a webcam endpoint on Windows for Windows apps; it does not register a camera on the Android phone. [OBS Virtual Camera](https://obsproject.com/kb/virtual-camera-guide) | OBS can handle source audio on PC, but this does not route it into the Douyin Android microphone/camera path. | **SUPPORTED BY OBS on PC**, not evidence of stock Douyin Android support. A separate hardware bridge would still face UVC/provider limitations. |
| NDI / RTSP / SRT | Yes | Yes | **NOT VERIFIED** as a Douyin Android camera input. These network streams need a receiving app/decoder; ordinary apps cannot expose them to CameraService as a new camera. | Stream protocols may carry audio to a compatible receiver, but the phone Douyin capture pipeline is not thereby fed. | **NOT VERIFIED** for Douyin stock camera; feasible for custom receiver apps or PC production, not a demonstrated phone-camera route. Network buffering adds variable delay (commonly hundreds of ms or more, implementation-dependent). |
| Android External Camera Provider | Yes at runtime if OEM integrated it | Yes | **NOT VERIFIED** on SM-G996U/Douyin 40.8.0. If exposed as a Camera2 ID, Douyin may still filter/reject it; app support must be observed. | Provider camera support does not itself imply audio capture/routing. USB audio is a separate path. | **SUPPORTED BY ANDROID (OEM/system dependent)**; third-party app cannot add the provider on a stock non-root phone. |
| Douyin PC LIVE / 直播伴侣 | Yes | Yes | **SUPPORTED BY DOUYIN** as a separate PC publishing workflow; the official product lists video/images/capture cards/phone casting and computer live. Account qualification and the installed Windows version must be checked. **NOT VERIFIED on this user’s account/device.** | PC Companion documents microphone/sound-card and speaker capture/mixing. [Official audio setup](https://streamingtool.douyin.com/docs/guide_4beqpl4o) | Strongest no-purchase candidate if already installed PC/OBS/capture inputs and account access exist. This is PC LIVE, not a camera source injected into Android Douyin. [Official product](https://streamingtool.douyin.com/) [Account requirements](https://streamingtool.douyin.com/docs/qna_start_error) |

## PC and phone workflows

Douyin’s official PC Live Companion page describes PC live support and source types including video, images, capture cards, phone casting and virtual avatars. Its documentation also states that PC access has account requirements, and its FAQ directs users to the in-product announcement for the current threshold. The product page currently describes PC live as having a 1,000-follower threshold, while the help page gives eligibility rules and notes that permission may be lost. Check the actual account inside the official app/site before planning around it. [Douyin Live Companion](https://streamingtool.douyin.com/) [Official eligibility FAQ](https://streamingtool.douyin.com/docs/qna_start_error)

The official help page for OBS mode says new applications for OBS mode are no longer accepted and directs users to use Live Companion. This is a warning against third-party “unlock” tools or assuming every account has a custom RTMP/OBS key. No official evidence found that the stock Android Douyin app accepts a user-supplied RTMP/NDI/SRT camera feed. [Official OBS-mode notice](https://streamingtool.douyin.com/docs/guide_96319)

For a phone workflow, Douyin’s official Companion documentation describes mobile live and mobile game live; screen/game sharing is its own supported live mode when shown to the account. It does not establish that an external PC stream becomes the phone camera. Any screen-capture route must be confirmed in the account UI and may carry Android capture prompts, app audio capture limits, notification/privacy exposure and encoding latency. Do not assume screen sharing is enabled for this account. [Official Live Companion app guide](https://streamingtool.douyin.com/docs/guide_d3z4b8of)

PC-to-phone hardware paths:

- **PC/OBS → HDMI → capture card → USB-C OTG → Android:** HDMI capture devices commonly present UVC video to hosts; Android still needs its External Camera Provider to expose that UVC stream to Douyin. USB-audio/UAC compatibility is another independent condition. Expected capture latency is hardware-specific; even when the system sees the source, preview/encoding can add frame buffering. No precise delay or Douyin compatibility is verified here.
- **OBS virtual camera:** stays on Windows and is usable by Windows software that accepts a webcam. OBS documents it as sharing the OBS scene with applications that can use a webcam. It does not cross USB/network into Android by itself.
- **NDI/RTSP/SRT over Wi-Fi or Ethernet:** transfers encoded or packetized media to a receiver. It needs a receiver/decoder application on Android or a PC workflow. A custom Android receiver can display it inside itself but cannot, without privileged/system integration, publish it as an additional CameraService device for Douyin.
- **MP4:** can be played by an Android media app or desktop tool, but file playback alone does not create camera input for Douyin. To use the PC publishing workflow, add the MP4 as a source in the official Companion if supported by the current version/account; to use Android Douyin, no official stock-app MP4-as-camera feature was verified.

Latency statements above are architecture-based expectations only, not measurements on this setup. Audio transmission is also separate from video: UVC/HDMI devices may expose a USB audio interface, network protocols may transport audio, and PC Companion can mix audio, but none of these facts proves the Douyin Android capture app will consume that audio.

## Recommended minimum-cost path

1. **Buy nothing.** First restore ADB access and query the Android external-camera feature flag, USB-host feature flag, `dumpsys media.camera`, and CameraManager IDs with no peripheral attached.
2. If a compatible UVC device is already owned, test only that device through a powered OTG adapter. Check whether a new external Camera2 ID appears. If it does not, stop the direct Douyin UVC path; a USB-camera app preview would not change that result.
3. If Android does expose a new camera ID, inspect Douyin’s standard camera preview for a source selector and test the selected external camera there without starting LIVE. Only a visible, stable preview in the original Douyin app counts as phone-route evidence.
4. If no Douyin selector appears, use official PC Live Companion or the official mobile game/screen-live option only if already available to the account. Check account access in the official UI; do not seek unofficial RTMP/OBS unlocks.

## Confirmation gates before implementation

- Confirm the on-device Douyin version is actually 40.8.0 (`dumpsys package com.ss.android.ugc.aweme`); this research did not verify it.
- Confirm Android exposes `android.hardware.camera.external` / an external Camera2 ID on this exact firmware, first without and then with an already-owned UVC source.
- Confirm the original Douyin preview lists/selects that ID. Do not infer from another camera app.
- Confirm what media source types the current official PC Live Companion accepts and whether this account has PC-live permission. No LIVE is needed just to inspect settings/previews; stop before the final Go Live action.
- Verify audio separately with a non-live local preview or official test workflow; do not assume HDMI/UVC audio follows the video.
- Only after all these checks should a specific capture device, adapter, cabling or integration be considered. This report recommends no hardware purchase at present.

## Source and test boundary

Sources were checked on 2026-10-20. Android facts come from AOSP/Android Developers; Douyin product and eligibility claims come from Douyin’s official Live Companion site. Samsung material found confirms the S22 Ultra USB-C port but did not establish UVC CameraService behavior for SM-G996U Android 14. No live session, account change, app install/removal, patch, root, protection bypass or hardware purchase was performed. No physical UVC test was possible, and no TikTok Vietnam app was in scope.
