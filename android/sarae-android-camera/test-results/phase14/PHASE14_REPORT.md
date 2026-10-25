# Phase 14 — Target integration và native audio

**Camera2 integration trong TikTok lab preview: PASS. Tổng hợp: PARTIAL_PASS; LIVE readiness vẫn NO-GO.** Không bắt đầu, yêu cầu hoặc phát LIVE. Không quay/đăng nội dung. TikTok/account được giữ nguyên.

## Đã deploy và xác minh

- Branch `feature/tools-android`, SM-G996U Android 14/API 34, TikTok lab 47.4.3, PID mới **13645**.
- Chỉ thay **cached module** theo procedure debug LSPatch đã dùng ở Phase 11. Kiểm tra HOME và camera rảnh trước khi force-stop process để load module. Không re-install, patch APK hoặc clear data.
- Module hiện tại SHA256 **`53a7246f01d1902de9aa596212c238d03e29e69d4eab33cb4724d321f42b1351`**; cache path `cache/lspatch/co.sarae.camera.poc/183496786.apk`. Marker runtime `revision=phase14 submissionRemap=true` đã xác nhận.
- Base APK trước/sau vẫn **`4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b`**; 61 installed APK paths được kiểm trước update. Nguồn A/B và auto orientation/FIT/override-null giữ nguyên.
- Backup Phase 11 thực tế từ cache trên máy: `.tools/phase14/rollback-module.apk`, SHA256 **`2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0`**; có thêm remote `.phase14-rollback`. Xem [rollback](ROLLBACK.md).

| Thay đổi | Trạng thái |
|---|---|
| Ownership guard theo identity CameraDevice + lifecycle lock | **DEPLOYED** vào TikTok cache |
| Remap prebuilt request tại conversion lookup, default single output | **DEPLOYED**, bật đúng TikTok lab allowlist |
| Submit, framework capture-result, owner/close stack diagnostics | **DEPLOYED**; chỉ quan sát, không tạo metadata/callback giả |
| Lệnh diagnostic close object đã đóng, khác owner active | **DEPLOYED và đã dùng một lần**, weak reference, từ chối nếu không có object hợp lệ |
| EGL/decoder/Video Engine/PCM Engine/VideoAudioClock | Không sửa |
| Java Audio Bridge | Giữ implementation Phase 11, MICROPHONE mặc định |
| Native Audio Bridge / LIVE encoder correction | **Không implement/deploy**; chưa đủ evidence |

Module build PASS bằng JDK 21 / Gradle offline, chỉ `:app:assembleDebug` và dependencies cần thiết. Marker JSONL lúc startup còn trong buffer nên host check ban đầu chưa thấy; sau khi có camera frame đã flush đúng marker. Không restart/reload thêm để giải quyết việc này.

## Runtime Camera2 và lifecycle thực tế

Các thao tác: mở camera preview → tab LIVE **còn nút Phát LIVE chưa bấm** → đóng bằng X → mở lại camera preview → replay close camera cũ → về HOME. Ba generation là lifecycle chủ động, không phải session bị recreate do đổi nguồn.

Mọi session đều `SessionConfiguration`, regular, một default output. Correction chỉ cấp lookup copy để ánh xạ original Surface tới stream dummy; request/tag/real configured outputs không bị sửa. Runtime target thực sự đã cần remap và submit thành công, không chỉ là case consumer riêng.

| Generation | Owner identity | Original Surface | EGL frames | Capture final-result count đã sample | Max output gap |
|---|---:|---:|---:|---:|---:|
| 1: camera preview | 242573835 | 236612308 | 3.168 | ≥3.120 | 50,724 ms |
| 2: LIVE preparation preview | 5419527 | 137571251 | 661 | ≥630 | 51,595 ms |
| 3: mở lại camera | 143546974 | 146598359 | 1.973 | ≥1.950 | 51,694 ms |

- Tất cả submit result được thu đều `error=none`. Snapshot LIVE preview: CameraService **ACTIVE**, request counter **1**, dummy stream đã produced **105 frame** (Phase 12/13 cũ là 0). Snapshot sau retired close: ACTIVE, produced **795 frame**.
- **0** output gap ≥100 ms, **0** PTS không tăng trong từng generation. Không lỗi capture observer/device/pipeline trong JSONL thu được; crash buffer rỗng. Không chứng nhận dài hạn từ lượt ngắn này.
- Capture counts là sample mỗi 30 frame của **framework/Binder final-result delivery** (partial=2, sensor timestamp thật). Chưa instrument trực tiếp việc TikTok app thực thi `onCaptureCompleted`; không dùng quan sát này để tuyên bố metadata gắn với từng frame MP4.
- Ảnh target cho thấy footage A đúng chiều và tư thế thay đổi; ROI subject trước/sau replay có MAD **72,949/255**. Đây là kiểm chứng ảnh thực ở target, không thay cho detector freeze/black trên mọi frame.

### Ownership gate: PASS

Replay ns **9440171034814958**, closing **5419527** đã đóng; owner active **143546974**, generation **3**. Log `camera_close_ignored` xuất hiện, sau đó:

- **1.292 EGL output** tiếp tục trong cùng generation/original Surface.
- Capture final-result count sample tăng **660 → 1.950**.
- **0 EGL release** giữa replay và lần đóng owner thật sau **43,069 s**.
- Chỉ khi kết thúc preview về HOME, owner hiện hành mới close và release EGL như dự kiến.

Đây là replay có kiểm soát trên object cũ thực của TikTok, không phải phát hiện spontaneous finalizer. Nó xác minh guard trên target; caller gây freeze của LIVE Phase 12 vẫn thiếu identity/stack lịch sử để CONFIRMED. Không gọi preview PASS là LIVE freeze fixed.

## Native audio: SUSPECTED backend, UNKNOWN PCM entry

Reuse Phase 12 AudioFlinger (MIC 48 kHz/stereo/PCM16, Java read/substitution=0) và Phase 13 scan 61 original APK / 250 ELF, cùng hash installed liblyrax đã xác minh ở Phase 13.

Phase 14 kiểm thêm ELF ARM64 `liblyrax.so`:

- SHA256 **`9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`**, build ID **`800d53bec62ddf0a1774842f5a16e1693ab7f0de`**.
- Imported `slCreateEngine`, `SL_IID_RECORD`, `SL_IID_ANDROIDSIMPLEBUFFERQUEUE`; relocation của RECORD là GLOB_DAT, slCreateEngine là JUMP_SLOT. `SL_IID_RECORD` và strings `AudioRecordOpenSLES`/`CreateAudioRecorder` phân biệt candidate recorder với các import chỉ dùng playback.
- Có string assertion `(*simple_buffer_queue_)->RegisterCallback(simple_buffer_queue_, SimpleBufferQueueCallback, this)`: candidate recording buffer-queue callback, **chưa có runtime address/registration/owner**.
- Export `AudioDeviceModule_nativeSetRecordingCallback`, offset **0x665704**, size **104 byte**, đã disassemble. Không chứng minh đây là PCM capture entry. Nhãn nearest-export+offset của objdump không phải tên callee thực; không suy ra call graph từ các nhãn đó.
- Thư viện cũng có Java/AAudio routes. Imports/strings không chứng minh LIVE chọn OpenSL; backend vẫn **SUSPECTED OpenSL ES**, exact capture PCM entry **UNKNOWN**.

AudioFlinger tại LIVE preparation preview và sau retired close đều có **0 RECORD thread**. Java capture/read cũng không xuất hiện. Vì capture LIVE không active ở các mode không broadcast đã quan sát, không đo được actual client buffer pointers/queue length/ownership/callback/start-stop. HAL 960-frame từ Phase 12 **không đủ** làm contract của client OpenSL buffer.

Do chưa đủ evidence, **không triển khai native adapter**. Decoder PCM readiness và Java recording PASS Phase 11 không được dùng làm native LIVE audio PASS. MIC/MUTED code và audio commit/ring-buffer logic giữ nguyên; không quay lại regression audio trong phase này. Không quan sát được active target encoder output ngoài recording/LIVE nên không có HEVC delivery PASS mới.

## Remaining GO/NO-GO

**Đã đóng gate:** candidate module hash/load trên target; Camera2 submit/remap thật; framework capture results; ownership replay; EGL continuity trong target preview.

**Vẫn NO-GO:** native capture entry/buffer ownership chưa xác nhận; chưa có native soundtrack-output evidence; sensor CaptureResult và injected MP4 PTS chưa được ghép contract; `onCaptureBufferLost` target remap/multi-output chưa kiểm; HEVC/actual LIVE media graph và viewer delivery chưa xác minh. Không sửa timestamp/engine hay thêm broad native hooks bằng phỏng đoán. Không yêu cầu thêm LIVE vì preview đã PASS.

## Timing, cleanup, evidence

**256,828 s** tổng device budget bảo thủ: inventory **2,203 s** + span liên tục từ deployment tới cleanup **254,625 s**, gồm cả chờ/đọc log/phân tích trong lúc app đang chạy. Host build/static trước deploy và phân tích sau cleanup không tính. Không test thêm, không endurance, không LIVE.

Đã về HOME, `Active Camera Clients: []`. Cached module hiện vẫn Phase 14; không rollback vì target preview gates PASS. CameraService final dump còn lưu historical client status ACTIVE, không được nhầm với current active clients rỗng.

- `analysis.json`, `results.json`, `state.json`, `adb.jsonl`, `native-import-evidence.txt`, `native-candidate-strings.json`: kết quả và audit.
- `final-bridge.jsonl`, `final-audio.jsonl` cùng các snapshot trước/sau: runtime đầy đủ của PID mới.
- `.tools/phase14/`: module/rollback APK, ảnh UI, raw camera/audioflinger/logcat, ELF và disassembly; không đưa dữ liệu UI/account vào Git.
- `evidence-sha256.json`: hashes; original Phase 12 evidence được kiểm hash lại và không sửa.

Nguồn đối chiếu compatibility: [AOSP Android 14 CameraDeviceImpl](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android14-release/core/java/android/hardware/camera2/impl/CameraDeviceImpl.java), [CaptureRequest conversion](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android14-release/core/java/android/hardware/camera2/CaptureRequest.java).
