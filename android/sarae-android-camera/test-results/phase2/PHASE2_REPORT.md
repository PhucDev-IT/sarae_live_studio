# Camera Bridge feasibility — 23/10/2026

## Kết luận

**PASS cho phạm vi prototype:** LSPatch không module hoạt động với app PoC; consumer Camera2 độc lập nhận pattern MP4 đỏ/xanh qua chính output Surface đã đăng ký với Camera2, và đổi A/B 12 lần mà không tạo lại camera session. Không có Java/native crash được ghi nhận trong các lượt stability khoảng hai phút. Đây không phải xác nhận tương thích cho ứng dụng khác hay bài thử dài hạn.

Nhánh `feature/tools-android`, project `android/sarae-android-camera`, Samsung SM-G996U/API 34/arm64/non-root. Không patch, mở hoặc tích hợp TikTok. Không thêm tính năng hoặc UI vào controller; consumer test có duy nhất TextureView cần để quan sát output Camera2.

## Task 1: loader

Chọn **JingMatrix/LSPatch stable v1.2, versionCode 487, debug**, từ [release chính thức](https://github.com/JingMatrix/LSPatch/releases/tag/v1.2). Dùng cùng phiên bản với báo cáo crash cũ để kiểm tra riêng app PoC, không đổi phiên bản theo thử vận may.

- JAR SHA-256: `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153`.
- Core trong APK: API 102, Vector `canary-3106`, hash `e00c5c5038bbcc14e0b382ab893301d6993e6989`.
- APK không module SHA-256: `0173c8fb1bdb5465f1ec36f099de1af749038139ad66dd2fcf4de543684659f6`.
- Config đã đọc trực tiếp từ APK: `useManager=false`, `sigBypassLevel=0`, `injectDex=false`, `debuggable=true`, `embedded_modules=[]`. [Inventory đầy đủ](artifact-inventory.json), [metadata release](lspatch-release.json), [patch log](patch-empty.txt).
- Dùng prebuilt JAR với Temurin Java 21. Build PoC: Android SDK 34, Gradle 9.6/AGP 9.4.1. Nếu tự build LSPatch từ source: checkout tag v1.2 kèm submodules và Git history, JDK 21, SDK compile 37/target 36, build-tools 37.0.0, NDK 29.0.13113456, Ninja 1.12.0, `./gradlew buildAll`; theo [build config](https://github.com/JingMatrix/LSPatch/blob/v1.2/build.gradle.kts) và [CI workflow](https://github.com/JingMatrix/LSPatch/blob/v1.2/.github/workflows/main.yml). Không thực hiện source build trong lượt thử này.

**PASS:** 133,729 giây, PID `9827` giữ nguyên, Camera2 một session, 43 switch PASS, không có decoder/camera error. [JSONL](loader-empty.jsonl), [toàn bộ logcat của PID](loader-empty-pid-logcat.txt), [crash buffer](loader-empty-crash.txt).

Lần patch đầu không có `-d` làm app không nhận intent clip dành riêng debug; đã sửa đúng tùy chọn bằng cùng JAR, không thay loader. Crash cũ của Douyin không module (SIGSEGV ở ART VisitRoots và libmetasec_ml) trong `git show 1ddbf2708a648d96f3fe9585e01ed4a3b64fd61f:android/SaraeAgent/docs/DOUYIN_LSPATCH_ROOT_CAUSE.md` không tái hiện trên app PoC. Điều này không giải thích hoặc sửa crash Douyin. Không có crash mới để cần thử thêm phiên bản.

## Task 2: consumer nhận frame qua hook

Đã nghiên cứu các điểm chặn [Camera1](https://github.com/Yaahua/vcam/blob/main/app/src/main/java/com/yaahua/vcam/Camera1Handler.java) và [Camera2](https://github.com/Yaahua/vcam/blob/main/app/src/main/java/com/yaahua/vcam/Camera2Handler.java). Prototype viết mới, chỉ scoped package `co.sarae.camera.consumer`:

1. Consumer độc lập, **không phụ thuộc** controller/engine/bridge, gọi Camera2 `createCaptureSession(List<Surface>, ...)` và `CaptureRequest.Builder.addTarget` với một TextureView Surface.
2. Bridge chặn session output và request target, chuyển camera thật vào ImageReader YUV đệm được drain liên tục.
3. Bridge chuyển Surface gốc của consumer cho `LoopingMp4Provider` hiện có. Engine giải mã MP4 lên đúng Surface đó. Không có TextureView MP4 phụ hoặc đường render trực tiếp trong consumer.
4. Consumer tự đọc bitmap 16×16 của TextureView, phân loại đỏ A / xanh B và log từng SurfaceTexture timestamp. Hai pattern H.264 640×480/30 fps/4 giây được tạo bằng FFmpeg, có hash trong inventory.

Đối chứng consumer nguyên bản cho 9 mẫu `PHYSICAL_OR_UNKNOWN`, không nhận A/B: [physical log](consumer-physical-logcat.txt). Sau nhúng module, consumer nhận A (`rgb=254,0,0`) và B (xanh), đồng thời có `session_redirect` và `target_redirect` trong [logcat hook/consumer](consumer-hook-logcat.txt). Đây là interception của output đã đăng ký với Camera2, không phải hai preview độc lập như Phase 1.

| Chỉ tiêu lượt cuối | Kết quả |
| --- | --- |
| PID / thời lượng có frame | `12961` / 135,051 giây |
| Session consumer | 1, generation `1` xuyên suốt |
| Switch A/B đúng pixel sau mỗi yêu cầu | 12/12 PASS |
| SurfaceTexture frame updates | 4.028 |
| FPS consumer / median gap | 29,818 fps / 33,374 ms |
| Gap >100 ms / lớn nhất | 12 / 167,023 ms |
| Decoder release / chênh so với consumer updates | 4.037 / 9 |
| Java/native crash quan sát được | 0 |

[Summary máy đọc được](summary.json), [lệnh switch và PID](switch-commands.txt), [build log](build.txt). `python scripts/analyze_phase2.py` kiểm tra PASS/FAIL dựa trên log, đòi đúng pattern sau từng switch, một session, hook events và >=120 giây frame.

Chênh 9 frame **không phải exact HAL drop counter**: decoder release không đồng nghĩa SurfaceTexture đã hiển thị; có boundary frames và coalescing của compositor. Script còn ước lượng 161 slot 30 fps bị thiếu từ khoảng frame, gồm cả gián đoạn đổi decoder và sai số nhịp compositor; không dùng số này để tuyên bố có đúng 161 frame decode bị mất. Log giữ từng frame để phân tích thêm. Lượt trước màn hình tắt chưa tạo Surface được giữ riêng ở [screen-off log](consumer-screen-off-logcat.txt); không tính vào PASS. Consumer cuối có `KEEP_SCREEN_ON`.

## Crash diagnostics và giới hạn

[AndroidRuntime/libc/DEBUG](native-java-diagnostics.txt) và [crash buffer](crash-buffer.txt) rỗng trong thời gian thu. [Exit info consumer](consumer-exit-info.txt) chỉ ghi các lượt dừng chủ động trước đó; [controller exit info](controller-exit-info.txt) được lưu riêng. `/data/tombstones` bị từ chối truy cập trên máy không root ([bằng chứng](tombstone-access.txt)); không có tombstone đầy đủ. Kết luận là **không quan sát thấy native crash trong lượt test**, không bảo đảm không bao giờ crash.

Prototype chỉ hỗ trợ một output preview 640×480 và overload session cũ. Chưa hỗ trợ `SessionConfiguration`, ImageReader làm output của consumer, nhiều Surface, Camera1, still capture, recording, đóng/mở lại camera hoặc đổi orientation. Camera HAL và capture metadata vẫn là camera thật; đây không phải camera ảo cấp hệ thống. Frame PTS reset ở loop/switch: 36 timestamp regressions trong lượt cuối. Chuyển video tạo lại decoder nên có khoảng trống khoảng 167 ms. Chưa đạt production readiness hoặc chuyển nguồn hoàn toàn liền mạch.

## Task 3 và hướng tiếp theo

`:app` chỉ compose `HookEntry` với engine hiện có; `:engine` giữ toàn bộ decode/loop/switch; `:camera-bridge` giữ interception và Surface routing. API Xposed 82 chỉ `compileOnly`, không đóng gói API giả vào APK. `:consumer-test` là app kiểm chứng độc lập, không phải module sản phẩm. Không nhân bản Video Engine.

Hướng phù hợp tiếp theo là EGL relay giữ Surface producer ổn định giữa decoder, chuẩn hóa timestamp đơn điệu và log fence/render callbacks để đo drop chính xác hơn; rồi mở rộng từng loại Camera2 output bằng bài thử riêng. Nếu cần HAL metadata và frame nguồn đồng nhất cho mọi app, app-level hook này không đủ: cần tích hợp hợp tác trực tiếp trong consumer, hoặc camera provider cấp hệ thống có quyền/OEM/root, hoặc đường camera USB/external mà consumer tự hỗ trợ. Không suy ra tương thích TikTok từ kết quả này.
