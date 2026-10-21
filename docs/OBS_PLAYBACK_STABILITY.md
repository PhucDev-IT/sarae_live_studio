# OBS playback: orientation, audio, A/B, camera fallback

## Windows Agent setup

- Install `ffprobe.exe` and `ffmpeg.exe` on the Agent PC and add both to the Agent process `PATH`. Playback rejects an unprobeable video. Rotated files require FFmpeg; the original file is never modified. The Agent writes a verified MP4 copy under `%LOCALAPPDATA%\SaraeAgent\obs_media` (or `SARAE_AGENT_HOME\obs_media`). Ensure enough disk space for that copy before LIVE.
- In the current OBS scene, create one Media Source (`Sarae Video`) or two (`Sarae Video A` and `Sarae Video B`). The Agent reuses these sources; it does not create sources. Configure OBS canvas for the desired LIVE format, normally 1080×1920. The Agent sets each video scene item to fit inside the canvas with zero rotation, crop and mirror.
- For fallback, add a **Video Capture Device** in the same scene, select an actual device in its Properties, and enter its exact OBS source name in Agent Settings → Camera dự phòng. The desktop OBS row then exposes **Dùng camera** and **Về video** while playback is active. Switching sources keeps the playback session active; no StartStream/StopStream request is sent.
- The Agent also recognizes `playback_camera` / `playback_video` commands. Exposing these buttons on the Web requires the existing Cloud command allowlist/API to accept them; that Cloud change is outside this OBS/local-playback work.
- A camera source with no selected device or missing from the scene is rejected while the video stays visible. obs-websocket cannot prove that a selected physical camera is producing valid frames; confirm this in OBS Preview before LIVE.

## What the Agent checks

- FFprobe reads dimensions, frame rate, display-matrix/rotate metadata, audio presence, sample rate and channels. The Agent logs this per file.
- Rotation of 90°, 180° or 270° is baked into a cached copy using FFmpeg autorotation; the copy is checked again for upright dimensions, zero rotation metadata and the same audio presence. Unsupported rotation and failed probes block playback.
- The active OBS Media Source is unmuted and read back. A/B keeps the standby source muted and mutes the outgoing source before unmuting the incoming source. The next file is staged in the hidden source.
- OBS scene items use fit-inside bounds, so different source aspect ratios are letterboxed instead of stretched or cropped.

## Verification matrix

| Case | Automated | Windows OBS Preview / TikTok LIVE |
| --- | --- | --- |
| MP4 portrait 9:16, landscape, MOV with 90° metadata, MOV with 180° metadata | FFprobe/FFmpeg tests verify rotation, dimensions and source immutability | Confirm actual orientation, no mirror, letterbox and full frame in Preview before LIVE; then inspect TikTok output |
| Audio present/absent, 24/25/30/60 FPS | Tests verify streams, sample rate, channel count, FPS and OBS mute calls | Listen to OBS monitor and TikTok output for correct speed/level/silence |
| Immediate switch, after-current switch, repeated A/B switching | Fake OBS tests verify stage/swap and state | Watch for black frames and listen for overlapping audio during rapid switching |
| Video → camera → video; missing camera | Fake OBS tests verify session state and visibility rollback | Confirm live camera frames, no black screen and return to video |

**Release gate:** Test the full matrix in OBS Preview on the intended Windows machine before any actual LIVE. Only an actual TikTok LIVE test can confirm transmitted video/audio quality and transition behavior. The automated tests do not establish those results.

Run local tests from `agent-core` with `python -m pytest tests/test_playback_engine.py tests/test_media_playback.py tests/test_obs_controller.py tests/test_media_probe.py -q`. The FFmpeg cases skip when the tools are absent.
