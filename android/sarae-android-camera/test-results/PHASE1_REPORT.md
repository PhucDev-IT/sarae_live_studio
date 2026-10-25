# Báo cáo Phase 1 — 23/10/2026

## Môi trường và phạm vi

- Branch: `feature/tools-android` (nhánh hiện tại); project mới: `android/sarae-android-camera`.
- ADB: Samsung `SM-G996U`, Android 14/API 34, `arm64-v8a`; build release keys, không root.
- APK: `:app:assembleDebug` bằng Gradle 9.6/AGP 9.4.1, compile/target SDK 34. **PASS**.
- App test dùng Camera2 preview thật và output Surface riêng cho MP4. Không hook Camera1/Camera2, không patch hoặc mở TikTok.
- Input A: `VCAM-harness-av-10s.mp4` (H.264 720×1280, 30 fps). Input B: `VCAM-harness-av2-6s.mp4` (H.264 360×640, 15 fps). Cả hai là fixture đã có trong monorepo; audio bị bỏ qua.

## Kết quả

| Kiểm chứng | Kết quả | Bằng chứng |
| --- | --- | --- |
| Build ba module | **PASS** | `./gradlew.bat --offline --no-daemon :app:assembleDebug` → `BUILD SUCCESSFUL`. |
| Đổi A/B mỗi 3 giây, không restart Camera2 consumer | **PASS trong app PoC** | 12 request, 12 `switch_continuity=PASS`, 0 FAIL; chỉ 1 `camera2_session_started`, generation `1`; chỉ 1 `surface_available` cho video. `switch_continuity` đòi frame từ đúng nguồn mới, frame Camera2 sau request, generation và video Surface ổn định. [JSONL A/B](phase1-sm-g996u-20261023.jsonl), [logcat A/B](phase1-sm-g996u-logcat.txt). |
| Decoder/frame/FPS hai nguồn | **PASS** | 893 frame release: A 668, B 225; median FPS decoder 30,03 và 15,48; không có `decoder_error`, `camera2_error` hoặc `crash` trong lượt A/B. [JSONL A/B](phase1-sm-g996u-20261023.jsonl), [summary](phase1-summary.json). |
| MP4 hết file rồi lặp | **PASS** | Clip B 6 giây chạy 15 giây: 2 sự kiện `loop`, 231 frame; Camera2 vẫn chỉ 1 session. [JSONL loop](phase1-loop-sm-g996u-20261023.jsonl), [logcat loop](phase1-loop-sm-g996u-logcat.txt). |
| Fallback codec cho 64×64 | **PASS với decoder phần mềm** | Các decoder Qualcomm từ chối cấu hình fixture 64×64; engine thử tiếp `c2.android.avc.decoder`, phát 107 frame và lặp 1 lần. [Log fallback](codec-64x64-fallback.jsonl); [log lỗi trước sửa](codec-64x64-diagnostic.jsonl). |
| LSPatch loader trên app PoC | **CHƯA XÁC MINH — chặn tích hợp** | Không có LSPatch CLI/manager trong workspace hoặc package manager thiết bị ([device check](device-lspatch-check.txt)). Chưa có APK PoC vá *không module* để thử boot/stability. Báo cáo lịch sử `git show 1ddbf2708a648d96f3fe9585e01ed4a3b64fd61f:android/SaraeAgent/docs/DOUYIN_LSPATCH_ROOT_CAUSE.md` ghi crash `SIGSEGV/SEGV_ACCERR` trên cùng SM-G996U với bản Douyin 40.8.0 đã vá **không module**; sự cố đó không chứng minh loader hỏng với app PoC, nhưng loại bỏ giả định loader đã hoạt động. |
| MP4 là frame Camera2 cho app khác / TikTok | **CHƯA THỬ** | Phase 1 không có bridge hook hoặc target patch. Không tuyên bố compatible. |

Chạy `python scripts/analyze_phase1.py test-results/phase1-sm-g996u-20261023.jsonl test-results/phase1-loop-sm-g996u-20261023.jsonl` từ gốc project để kiểm tra điều kiện PASS/FAIL bằng log. Mỗi dòng JSONL có `wallTimeMs`, `elapsedRealtimeNs`, `kind`; frame có `presentationTimeUs`, source, index, FPS; `surface_frame`/`camera2_fps` có `SurfaceTexture.timestamp`. Logcat được lưu riêng để đối chiếu crash Java. Native crash cần capture thêm `libc`/tombstone nếu xuất hiện ở lượt sau.

## Giới hạn kỹ thuật và gate tiếp theo

`Camera2PreviewBridge` không nhận frame từ `LoopingMp4Provider`; hai preview độc lập, cùng sống trong app. Do Android Camera2 không cung cấp API công khai để thay frame camera trong một process khác trên thiết bị không root, việc đưa MP4 vào app khác cần một cơ chế bridge/loader được kiểm chứng riêng. Kết quả continuity ở đây chỉ xác nhận Camera2 session của **app test** không restart khi nguồn MP4 đổi.

Để mở Phase 2, trước tiên cần kiểm tra LSPatch trên **app PoC của chính dự án**: ghi hash/version loader, patch APK PoC không nhúng module, cài và chạy bài thử camera/đổi video, thu PID, logcat `AndroidRuntime`, `libc`, `DEBUG` và tombstone nếu crash; chỉ khi gate đó PASS mới xét bridge hook. Không dùng TikTok làm target thử loader.
