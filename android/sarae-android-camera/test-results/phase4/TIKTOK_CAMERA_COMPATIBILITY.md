# Phase 4 — TikTok camera compatibility, 23/10/2026

## Kết luận và điểm dừng

**PASS chẩn đoán:** quan sát được TikTok dùng Camera2 cho preview đã mở, với một SurfaceTexture PRIVATE 1920×1080. **PASS cải tiến độc lập:** renderer dùng đúng kích thước EGL consumer, kiểm chứng 3/3 switch trong consumer của dự án. **TikTok injection/LIVE: UNVERIFIED, chưa đạt compatibility PASS.**

Dừng tại đây: bridge chỉ được nạp vào `co.sarae.camera.consumer`. TikTok production không được instrument trong lượt này; để quan sát chính xác overload tạo session hoặc chạy bridge trong process TikTok cần đường instrumentation được phép, ví dụ test build có instrumentation hoặc quyết định riêng cho việc patch APK. Phase này không cho phép patch TikTok, nên không tiếp tục theo hướng đó. Không thực hiện signature/security bypass, không ghi hình, đăng bài hay bắt đầu LIVE.

## Thiết bị, phiên bản và thời lượng

- Branch `feature/tools-android`, Samsung SM-G996U, Android 14/API 34, ARM64, non-root.
- Package hiện diện: `com.ss.android.ugc.trill`, versionName **47.4.3**, versionCode **470403**, minSdk 23, targetSdk **36**, primary ABI `arm64-v8a`, installer được PackageManager ghi là `com.android.vending`. [Metadata](package-metadata.txt), [version recheck](version-recheck.txt).
- Camera và RECORD_AUDIO đã được cấp. Đây không phải bằng chứng đường capture đang được gọi.
- Tổng **6 lượt quan sát/kiểm chứng được đo: 53,565 giây**, gồm cả lượt thất bại do mất ADB 3,226 giây; lượt dài nhất 14,898 giây. Consumer verification 14,534 giây, **3 switch**. 0 switch video bên TikTok, không endurance/stress. Mọi lượt dưới 60 giây, tổng dưới 300 giây. [Thời lượng và kết quả tự động](summary.json).
- Metadata read/ADB transfer/static analysis/build được ghi riêng khỏi thời gian foreground test; không lặp bài Phase 1–3. Chỉ build `:app:assembleDebug` và `:consumer-test:assembleDebug` cùng dependency engine bị ảnh hưởng. [Build log](build.txt).

Trong lượt thu có một lần USB/ADB ngắt rồi kết nối lại. Bản APK pull dở ban đầu có thể bị Python nhận nhầm là ZIP vì gặp một ZIP asset lồng bên trong; nó không có manifest/dex và đã bị loại khỏi phân tích. Bản base đọc lại đủ **160.873.676 byte**, có AndroidManifest và 15 DEX; SHA-256 trong [inventory](artifact-inventory.json).

Package code path và `lastUpdateTime` đổi từ 11:03:32 sang 22:00:24 nhưng version vẫn 47.4.3/470403 trong lúc thực hiện. Không có lệnh cài/patch TikTok nào do lượt này thực hiện. Có thể liên quan delivery feature split, nhưng nguyên nhân **UNKNOWN**. Runtime snapshot dưới đây chụp lúc **22:00:19**, trước mốc update; không suy rộng sang mọi split/build có cùng versionName. Base static snapshot và camera feature split được ghi hash riêng.

## Quan sát đã xác nhận

1. **Camera2:** log của PID `22934` ghi `Camera 0 ... client com.ss.android.ugc.trill API Level 2` qua OPENING → OPEN → ACTIVE. Một stack lúc đóng camera có `android.hardware.camera2.impl.CameraDeviceImpl.close()` và thread `TECameraServer`. [Camera API logcat đã lọc](camera-api-logcat.txt). Đây là bằng chứng runtime Java Camera2 cho đường preview quan sát được; không loại trừ Camera1/NDK ở đường khác.
2. **Một output ở snapshot:** CameraService ghi client `com.ss.android.ugc.trill`, PID 22934, một `Stream 0 Surface 0`, không input stream; operation mode NORMAL. Consumer name `SurfaceTexture-1-22934-1`, dims **1920×1080**, format **0x22 (PRIVATE)**, usage `0x20100`, timestamp base 0, 78 frame đã produced. [Đoạn client/stream](active-client-stream.txt). Không coi danh sách camera capabilities toàn thiết bị là stream TikTok đang dùng.
3. **Lifecycle:** quan sát OPEN/ACTIVE và IDLE/CLOSED khi rời preview; cùng PID. Không thử thay lens, background recovery nhiều lần hoặc camera mode khác.
4. **Policy size khác stream:** log strategy `ve_camera_preview_size` trả 1080×1920, còn stream HAL là 1920×1080. Đây là hai số đo ở hai tầng; chưa xác định TikTok xoay/mirror/crop ở đâu. Không tự thêm rotation 90°.
5. **Audio:** AppOps RECORD_AUDIO tại snapshot không ghi capture mới (lần allow được thấy cách khoảng hai ngày); không có bằng chứng PCM capture của preview này. [AppOps](audio-preview-appops.txt). Không thu audio hoặc test LIVE.

Đã đọc tĩnh method-reference tables của base APK: có các reference Camera1, Camera2 và AudioRecord trong classes2/classes9; [dữ liệu tĩnh](static-camera-api-refs.json). Reference chỉ xác nhận API có mặt trong DEX, **không chứng minh invocation**. Base không cho thấy `createCaptureSession` reference trong phần đã quét; camera feature split đọc được không chứa DEX. Không kết luận overload vắng mặt vì code có thể ở split khác, dynamic/native hoặc reflection. Chưa phân tích hết splits.

## Compatibility matrix

| Hạng mục | TikTok đã quan sát | Bridge hiện tại / kết luận |
| --- | --- | --- |
| Camera1 | Có reference tĩnh; runtime UNKNOWN ở các mode khác | Không có Camera1 hook. Chưa có lý do để triển khai Camera1 trong Phase này |
| Camera2 | **CONFIRMED** trên preview đã chụp | Có scoped Camera2 prototype, nhưng TikTok không nạp module của dự án |
| Legacy `createCaptureSession(List,...)` hay `SessionConfiguration` | **UNKNOWN**; CameraService không lộ Java overload | Bridge chỉ xử lý overload đầu tham số List; SessionConfiguration hiện bị bỏ qua. Chưa implement theo suy đoán |
| Nhiều output / shared/deferred Surface | Snapshot có **1 output SurfaceTexture**, không input | Bridge chỉ hỗ trợ một Surface; multi-output, deferred/shared/reprocess chưa hỗ trợ và chưa được xác nhận cần ở preview này |
| Output type | SurfaceTexture PRIVATE, normal session | Phù hợp dạng preview đã thử ở consumer riêng; chưa kiểm chứng queue/usage thực tế trong TikTok |
| Resolution | **1920×1080** runtime snapshot | Viewport cố định 640×480 là gap cụ thể. Đã sửa query EGL extent và test độc lập 1920×1080 PASS |
| Rotation/mirror/crop | **UNKNOWN** ở compositor TikTok | Decoder texture matrix được áp dụng; chưa có mapping sensor/display/front-camera cho TikTok. Dynamic resize/orientation vẫn chưa test |
| Lifecycle | Camera đóng/mở ở các snapshot | Reuse kết quả lifecycle consumer Phase 3; chưa chứng minh lifecycle TikTok |
| Audio | Quyền granted + reference tĩnh; runtime capture path **UNKNOWN** | Engine/bridge hiện chỉ inject video. Không có audio capture/injection trong pipeline này |
| LIVE | **NOT TESTED** | Camera preview không chứng minh encode/uplink/receiver hoặc audio LIVE |

Theo [CameraDevice API](https://developer.android.com/reference/android/hardware/camera2/CameraDevice) và [SessionConfiguration API](https://developer.android.com/reference/android/hardware/camera2/params/SessionConfiguration), các overload có thể tạo cấu hình stream giống nhau. Vì vậy snapshot CameraService không phân biệt được đường gọi; việc targetSdk 36 cũng không chứng minh app dùng overload mới.

## Cải tiến tối thiểu và quick verification

`EglOutput` query `EGL_WIDTH`/`EGL_HEIGHT` của window Surface, kiểm tra extent hợp lệ và dùng nó cho viewport. EGL context, producer, Surface routing và Video Engine được giữ nguyên. Consumer nhận test-only intent `testWidth/testHeight`, mặc định vẫn 640×480, không thêm UI. Không thay allowlist bridge, không thêm hook overload chưa được quan sát.

Chạy consumer riêng với `testWidth=1920`, `testHeight=1080`: EGL log xác nhận **size=1920x1080**, một camera session, 332 frame, 3/3 switch đúng pixel, 0 timestamp không tăng, 0 frame đen, max gap **51,106 ms**, không lỗi pipeline hoặc fatal Java/native được ghi, EGL release đầy đủ. [Bridge JSONL](consumer-bridge.jsonl), [consumer JSONL](consumer-frames.jsonl), [crash log](consumer-crash.txt), [summary](summary.json). `python scripts/analyze_phase4.py` kiểm tra riêng tiêu chí này và ngân sách thời gian, không trả TikTok integration PASS.

## Blocker còn lại / quyết định cần cho phase sau

1. **Module không chạy trong TikTok.** Mở rộng allowlist không tự tạo injection trên app production non-root. Cần quyết định riêng về instrumentation/patch trong phase khác hoặc test build được instrument; phase này dừng trước ranh giới đó.
2. **Overload và callback path chưa xác định.** Cần runtime trace của chính đường camera để quyết định SessionConfiguration/native/multi-output; không sửa bridge chỉ vì khả năng đó tồn tại.
3. **Transform và lifecycle chưa đối chiếu trực tiếp với MP4 trong TikTok.** Sửa viewport chỉ là điều kiện cần, không phải xác nhận preview hoạt động.
4. **Audio và LIVE hoàn toàn chưa xác minh.** Không có bằng chứng sender encode, uplink, receiver hoặc PCM injection.

Kết quả cuối: **diagnosis PASS; targeted own-consumer fix PASS; TikTok bridge compatibility chưa xác minh/blocked, không integration PASS**. Không cần chạy thêm stability hoặc lặp Phase 3 để giải quyết các blocker quan sát/instrumentation này.
