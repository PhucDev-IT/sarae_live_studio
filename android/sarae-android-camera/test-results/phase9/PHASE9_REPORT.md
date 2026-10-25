# Phase 9 — observation, 24/10/2026

## Kết quả: NOT TESTED đối với LIVE end-to-end

Người dùng xác nhận: “tôi đã thử tính năng quay trước, chưa live” và “tôi đang test quay video trước”. Vì chưa có LIVE thật, không thực hiện A→B→A trong LIVE và không gán VIDEO PASS/PARTIAL PASS cho phiên phát chưa tồn tại. Viewer độc lập được báo sẵn sàng nhưng chưa có receiver evidence của LIVE.

| Gate | Kết quả |
| --- | --- |
| Preflight APK/module/media/config | PASS, giữ nguyên Phase 8 |
| Actual LIVE preview A | PASS về pixel upright, hook/EGL hoạt động |
| User-started LIVE | **NOT TESTED** |
| Camera2 đổi session khi bắt đầu LIVE | **NOT TESTED** |
| LIVE switches | **0**, chưa gửi B/A |
| Independent viewer moving A/B | **UNVERIFIED** |
| User-ended LIVE | Không áp dụng: người dùng xác nhận chưa phát |

Agent không nhấn Start/End LIVE, record/post, login/consent, không uninstall/reinstall/patch/clear data, không build module. Cuối block giữ nguyên giao diện CAMERA của người dùng, không HOME/BACK hoặc can thiệp bản nháp.

## Preflight và installation

Branch `feature/tools-android`; SM-G996U, TikTok lab `com.ss.android.ugc.trill` 47.4.3/470403; đủ 61 APK. SHA-256 base trên thiết bị khớp artifact Phase 8; module embedded được xác định thông qua exact base hash và module local đã pin.

- Base: `201abaaca8432a512ab283686c11014a399f74eeae0f4bd508393b084aafbe8b`.
- Module: `63bb56fbee356c0ac1305910c2ea563580dbdec43531769daa10aa41b63d27eb`.
- A/B hash khớp footage thật Phase 8, file truy cập được.
- Config auto orientation=true, FIT, override=null, không diagnostic readback.
- Metadata package trước/sau giống nhau, `lastUpdateTime=2026-10-24 08:43:02`, `firstInstallTime=2026-10-23 22:41:09`.

[Base hash](installed-base-sha256.txt), [media hashes](media-sha256.txt), [config](render-config.json), [package trước](package-before.txt), [package sau](package-after.txt).

## Pixel/lifecycle evidence và cadence

[Preview A](preview-A.png) là màn chuẩn bị LIVE, còn nút Phát LIVE. Camera2 SessionConfiguration redirect Surface **185876732**, EGL 1920×1080, rotation 90 + inverse consumer reflection, override null; source A upright và chữ đọc bình thường.

Sau đó người dùng tự test quay: [ảnh chỉnh video](awaiting-start.png), [ảnh cuối CAMERA/15s, không broadcasting](final-observation.png). Đây không phải receiver screenshots và không phải LIVE active.

[Summary tự động](summary.json), [JSONL phạm vi Phase 9](phase9-events.jsonl), [full PID JSONL cuối](final-bridge.jsonl) ghi:

- PID **17808**, 5 camera sessions/EGL instances quan sát được trong navigation/recording; không gọi các generation này là “LIVE session recreation”.
- **2.405** output sample; weighted output FPS **30,020**, max recorded within-provider gap **45,483 ms**; 0 gap ≥100 ms trong sample đã ghi, 0 PTS regressions khi xét từng EGL output.
- Có khoảng camera đóng/chuyển mode; output gap counter reset theo provider. Các số trên không chứng minh hình liên tục xuyên mọi mode transition, không thay thế phép đo file quay hoặc receiver.
- **Một pipeline error** `eglSwapBuffers error=12301` = `EGL_BAD_SURFACE` (đối chiếu Android API 34 android.jar). Sau lỗi, EGL release; CameraDevice close được log khoảng **114 ms sau lỗi**. Đây là lỗi lifecycle Surface trong luồng người dùng thử recording/chuyển màn, không phải fatal Java/native crash và không phải LIVE FAIL đã xác minh. Chưa có bằng chứng về thời điểm hủy Surface phía TikTok, nên chưa sửa/refactor engine.

[Crash buffer](crash-buffer.txt), [relevant logcat](relevant-logcat.txt), [ADB timing/timeouts](adb-commands.jsonl). Không suy ra production stability từ mẫu ngắn hoặc từ việc không thấy fatal.

## Quan sát audio và giải thích quay/LIVE

Người dùng báo file quay có tiếng mic, không có tiếng MP4. Chưa nhận/đọc file quay hoặc waveform để xác minh độc lập. Source `LoopingMp4Provider` chỉ chọn MIME `video/` và select đúng video track; không giải mã/inject audio MP4. Camera Bridge hiện chỉ thay video Surface. Vì vậy không có đường cấp soundtrack MP4 vào audio capture của TikTok trong implementation này.

Về kiến trúc chung, camera/video input có thể dùng chung, còn ghi file và phát mạng có downstream encode/output khác nhau. Audio input cũng là đường riêng; [Android MediaRecorder](https://developer.android.com/media/platform/mediarecorder) mô tả cấu hình nguồn mic riêng và output file. Đây không phải bằng chứng TikTok dùng MediaRecorder trong cả hai mode. Cần LIVE thật để quan sát target có thay Camera2 session/Surface hoặc nguồn audio hay không.

Không thêm audio engine/hook theo constraint Phase 9. Kết quả quay có hình đúng không chứng minh viewer LIVE nhận hình/tiếng đúng.

## Thời gian và blocker

Tổng **163,953 giây = 2 phút 43,953 giây**, gồm preflight, preview, thời gian chờ/thao tác người dùng và cleanup/diagnostics. Không tiếp tục device test sau khi đóng observation. [Session state](session-state.json), [protocol](protocol.jsonl). Marker collection_finished cũ ghi 166,437 s do cộng lặp thời gian của chính finish call; ngân sách đúng trong session-state là 163,953 s, tính từ monotonic active start + preflight. Harness đã sửa phép tính marker; raw journal giữ nguyên để đối soát.

**Blocker trước Phase 10:** chưa có actual LIVE do người dùng bắt đầu, chưa quan sát start transition và A/B trong LIVE, chưa có video người xem độc lập, audio MP4 chưa được hỗ trợ, còn lifecycle EGL_BAD_SURFACE khi đổi recording mode và cadence >100 ms ở Phase 8 chưa được giải quyết. Không mở vòng test khác tự động. Cần lượt phối hợp LIVE riêng khi người dùng sẵn sàng bắt đầu/kết thúc thủ công; ghi rõ receiver delay và motion evidence.

[Evidence hashes](evidence-sha256.json). APK, source engine/bridge và account data được giữ nguyên.
