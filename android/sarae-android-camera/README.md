# Sarae Android Camera PoC

Phase 14: [TikTok target integration & native audio](test-results/phase14/PHASE14_REPORT.md) — Camera2 target preview PASS: Phase 13 fixes đã nạp qua cache, real submit/capture hoạt động; retired close không teardown owner mới (1.292 frame tiếp tục). Gap max 51,694 ms, 0 PTS regression trong ba preview lifecycles. Native PCM entry UNKNOWN, chưa triển khai native audio hook; LIVE vẫn NO-GO. 256,828 s, không phát LIVE; giữ APK/account, có rollback module Phase 11.

Phase 13: [LIVE root cause & readiness](test-results/phase13/PHASE13_REPORT.md) — PARTIAL_PASS, **NO-GO cho LIVE tiếp theo**. Xác nhận lỗi prebuilt request và close nhầm CameraDevice bằng test đối chứng; local encoder trước/sau ownership fix ghi 78/251 frame. Freeze Phase 12 khớp frame gần cuối trước early teardown, nguyên nhân caller vẫn SUSPECTED. Native PCM entry UNKNOWN, chưa sửa/deploy TikTok; tổng device budget bảo thủ 276,393 s, không phát LIVE.

Phase 12: [Real LIVE video & audio](test-results/phase12/PHASE12_REPORT.md) — FAIL trong phiên LIVE duy nhất: viewer giữ hình từ khoảng 17,8 s, sau đó giữ hình kéo dài; chỉ nghe mic. EGL vẫn xuất frame nhưng Java AudioRecord bridge không có read/substitution. Không đổi A/B trong LIVE, không thử lại. Phân tích clip viewer 107,967 s ngoại tuyến; 62,033 s thao tác kiểm chứng thiết bị.

Phase 11: [Audio continuity & real A/B recording](test-results/phase11/PHASE11_REPORT.md) — actual saved recording confirms A→B→A. Startup missing PCM 201→0 ms; loop A max 83→13.33 ms. Two B head underruns, one preparation timeout and clock resync discontinuities remain; PARTIAL_PASS. Cache-only module update, unchanged installed TikTok base, 166.422 s device execution; no new LIVE session.

Phase 10: [Audio Engine & TikTok Audio Bridge](test-results/phase10/PHASE10_REPORT.md) — Java AudioRecord recording path confirmed; actual saved VIDEO contains fixture A soundtrack, MICROPHONE pass-through and MUTED output PASS. Short-file A/V alignment estimate 2.75–30 ms (one-frame uncertainty); A/B audio switching NOT TESTED. Startup/loop PCM underruns remain; overall PARTIAL_PASS. Current lab mode restored to MICROPHONE.

Phase 8: [Rotation & smooth switching](test-results/phase8/PHASE8_REPORT.md) — auto orientation trên TikTok LIVE preview PASS, không dùng rotation override; 10/10 switch cùng session/Surface trong từng lượt 5 switch. Switch-window max 45,112 ms; cadence toàn foreground vẫn FAIL vì normal max 161,628 ms. Định vị một native latch/mutex stall nhưng chưa có fix được chứng minh. Lượt test tiếp được cấp phép dùng 229,866 s; lượt lỗi kết nối trước đó 326,765 s được giữ và ghi nhận vượt budget. Chưa phát LIVE.

Phase 7: [Real MP4 pipeline validation](test-results/phase7/PHASE7_REPORT.md) — giữ nguyên APK/module/engine, 10 switch, 153,203 giây device testing. Normal gap max 71,237 ms; switching max 191,926 ms nên FAIL ngưỡng 100 ms. Root cause chưa xác định; footage thật lộ sai rotation 90°, chưa sửa.

Phase 6: [Actual TikTok APK report](test-results/phase6/REAL_TIKTOK_REPORT.md) — loader/hook/preview injection trên TikTok lab 47.4.3 PASS, pixel A→B→A và 2 switch giữ session/Surface. Cadence vượt ngưỡng cũ 100 ms nên tổng hợp PARTIAL_PASS; chưa test broadcast LIVE. Tổng device testing tính bảo thủ 280,900 giây.

Phase 5: [Integration feasibility](test-results/phase5/INTEGRATION_FEASIBILITY.md) bổ sung SessionConfiguration một output và kiểm chứng consumer riêng PASS 5/5 switch trong 33,764 giây tương tác thiết bị. TikTok injection vẫn BLOCKED/chưa xác minh; báo cáo phân biệt media sharing chính thức và experiment APK cần quyết định riêng.

Phase 4: [TikTok camera compatibility assessment](test-results/phase4/TIKTOK_CAMERA_COMPATIBILITY.md) xác nhận snapshot Camera2/SurfaceTexture 1920×1080; viewport fix PASS trên consumer riêng, chưa patch hoặc xác nhận injection vào TikTok.

Phase 3 thêm EGL producer ổn định, preload và timestamp đầu ra đơn điệu. [Báo cáo Phase 3](test-results/phase3/PHASE3_REPORT.md) ghi 100/100 switch cùng lượt đo 23 phút 54 giây được rút ngắn theo yêu cầu người dùng; không xác nhận đủ 30 phút. [Thiết kế pipeline](PHASE3_PIPELINE.md).

Phase 2 đã có prototype Camera2 hook và consumer độc lập: xem [báo cáo feasibility và bằng chứng](test-results/phase2/PHASE2_REPORT.md). Các mô tả bên dưới ghi lại baseline Phase 1; bridge hook Phase 2 chỉ scoped `co.sarae.camera.consumer`.

Đây là dự án Gradle độc lập trong nhánh `feature/tools-android` của monorepo. Mục tiêu Phase 1 là đo khả năng giải mã MP4 lặp và đổi nguồn liên tục trong app thử nghiệm của chính dự án trên Samsung SM-G996U (Android 14/API 34, arm64, không root).

## Cấu trúc

| Module | Vai trò |
| --- | --- |
| `:camera-bridge` | `CameraFrameProvider` và `CameraBridge` là hai hợp đồng riêng. `Camera2PreviewBridge` mở camera thật, duy trì preview session và đếm `sessionGeneration`. |
| `:engine` | `LoopingMp4Provider` dùng `MediaExtractor` + `MediaCodec` để giải mã video MP4 vào `Surface` do caller sở hữu. Decoder chạy trên `HandlerThread`; khi đổi nguồn, decoder/extractor được đóng và tạo lại nhưng output `Surface` không đổi. Khi EOS, seek và flush để lặp. Nếu codec đầu tiên không cấu hình được, thử decoder phù hợp tiếp theo. |
| `:app` | Camera2 preview và MP4 preview nằm cạnh nhau; chọn MP4 A/B qua Storage Access Framework, đổi thủ công hoặc mỗi 3 giây. App ghi JSONL và logcat cho decoder, frame PTS, SurfaceTexture timestamp, FPS, session, vòng đời và Java crash. |

Camera2 ở Phase 1 là **consumer thử nghiệm của app**, nhận camera thật. MP4 chạy trên Surface riêng. Kết quả continuity chứng minh Camera2 session trong app sống qua các lần đổi MP4, nhưng **không chứng minh frame MP4 đi vào Camera2 hay bất kỳ app khác**. `CameraBridge` chưa có triển khai hook; không có TikTok integration/patch.

## Build và chạy

```powershell
cd android/sarae-android-camera
./gradlew.bat :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Cấp quyền CAMERA, chọn hai MP4 H.264 bằng các nút trong app, rồi bật auto switch. Log JSONL nằm ở `Android/data/co.sarae.camera.poc/files/logs/phase1-*.jsonl`; logcat dùng tag `SaraeCameraPoC`. Audio track trong MP4 không được phát ở Phase 1.

Để tái hiện test ADB với file trong app-specific external files directory (chỉ bản debug nhận intent extras):

```powershell
adb shell pm grant co.sarae.camera.poc android.permission.CAMERA
adb push path/to/a.mp4 /sdcard/Android/data/co.sarae.camera.poc/files/a.mp4
adb push path/to/b.mp4 /sdcard/Android/data/co.sarae.camera.poc/files/b.mp4
adb shell am start -n co.sarae.camera.poc/.MainActivity --es clipA /sdcard/Android/data/co.sarae.camera.poc/files/a.mp4 --es clipB /sdcard/Android/data/co.sarae.camera.poc/files/b.mp4 --ez autoSwitch true
```

```powershell
python scripts/analyze_phase1.py test-results/phase1-sm-g996u-20261023.jsonl test-results/phase1-loop-sm-g996u-20261023.jsonl
```

## Kiến trúc tham khảo

Đã đọc [Yaahua Camera1Handler](https://github.com/Yaahua/vcam/blob/main/app/src/main/java/com/yaahua/vcam/Camera1Handler.java), [Camera2Handler](https://github.com/Yaahua/vcam/blob/main/app/src/main/java/com/yaahua/vcam/Camera2Handler.java), [Camera2SessionHook](https://github.com/Yaahua/vcam/blob/main/app/src/main/java/com/yaahua/vcam/Camera2SessionHook.java) và [VSN6Tu HookMain](https://github.com/VSN6Tu/android_VCAM-Revise/blob/master/app/src/main/java/com/example/vcam/HookMain.java). Chúng tách đường Camera1 callback/preview và Camera2 request/session/Surface; PoC chỉ lấy ý tưởng phân ranh provider và consumer. Không sao chép hook hoặc mã nguồn từ hai dự án.

## Bằng chứng và bước tiếp theo

Xem [báo cáo Phase 1](test-results/PHASE1_REPORT.md) cho baseline và [báo cáo Phase 2](test-results/phase2/PHASE2_REPORT.md) cho gate loader đã PASS cùng prototype hook. Phase 2 thêm `:consumer-test` độc lập để kiểm chứng output Camera2; chỉ package này nằm trong scope hook. [Hướng dẫn tái hiện](test-results/phase2/REPRODUCE.md) có lệnh patch/test; loader thành công trên app PoC không xác nhận tương thích ứng dụng khác.
