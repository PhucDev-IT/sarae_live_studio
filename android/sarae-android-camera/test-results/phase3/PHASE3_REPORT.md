# Phase 3 — Stable Camera Pipeline, 23/10/2026

Đã triển khai EGL pipeline và kiểm chứng 100 switch. Theo yêu cầu rút ngắn của người dùng trong khi test, endurance dừng ở **1.433,994 giây (23 phút 53,994 giây)**. Không tuyên bố đã chạy đủ 30 phút. Máy: Samsung SM-G996U/API 34/arm64, không root; branch `feature/tools-android`.

## Thay đổi

`StableFrameProvider` giữ một EGL context/window producer và Surface consumer xuyên suốt switch. Hai decoder slot dùng chung **implementation `LoopingMp4Provider` hiện có**, không sao chép decoder. Nguồn mới được preload tới frame hợp lệ đầu tiên trong external SurfaceTexture rồi mới chọn; nguồn cũ tiếp tục được vẽ trong lúc chờ. Decoder cũ được đóng trên worker, texture được giải phóng trên EGL thread sau khi decoder đã dừng.

`switchNow(uri)` đổi sau khi frame mới sẵn sàng; `switchAfterCurrent(uri)` chờ thêm output EOS của nguồn hiện tại. Renderer chạy mặc định 30 fps, cấu hình qua constructor `targetFps` (1–60), output ban đầu 640×480. EGL presentation time lấy từ đồng hồ đơn điệu, tách khỏi media PTS và timestamp nguồn có thể reset. Xem [thiết kế pipeline](../../PHASE3_PIPELINE.md).

## Kết quả đo foreground

| Tiêu chí | Kết quả thực tế |
| --- | --- |
| 100/100 switch đúng nguồn | **PASS**, 50 `now` + 50 `after`; pixel consumer đúng sau từng commit, `after` có EOS trước commit |
| Không tạo lại Camera2 session/Surface/EGL | **PASS**, PID `17177`, generation `1`, một session redirect, một EGL creation |
| Timestamp đầu ra tăng nghiêm ngặt | **PASS**, 0 lần không tăng ở renderer và consumer |
| Không frame đen/unknown khi switching/playback | **PASS**, 0/43.020 frame; mọi callback consumer đều có pixel sample |
| Cadence consumer | **29,9994 fps**, median gap 33,3668 ms |
| Max frame gap <100 ms | **PASS**, consumer **66,8363 ms**, renderer **55,4667 ms**, 0 consumer gap ≥100 ms |
| Loop MP4 / nguồn khác resolution và FPS | **PASS**: A đỏ 640×480/30 fps/4 giây, B xanh 320×240/15 fps/3 giây |
| Java/native/pipeline crash trong lượt đo | **Không quan sát thấy**, JSONL không có lỗi foreground; AndroidRuntime/libc/DEBUG và crash buffer không có fatal |
| Đủ 30 phút | **CHƯA HOÀN THÀNH**, dừng sớm theo yêu cầu người dùng; giữ nguyên ngưỡng 1.800 giây trong analyzer |

[Summary tự động](summary.json), [lệnh 100 switch](endurance/switch-commands.jsonl), [metadata dừng theo yêu cầu](endurance/stopped-by-user.json), [run metadata](endurance/run.json).

Nguồn B 15 fps được lặp frame có chủ đích ở đầu ra 30 fps. Tổng log ghi 3.560 frame repeat/hold, 0 skipped renderer tick và 0 callback nguồn coalesced. Có 43.132 output swap so với 43.020 consumer updates: chênh 112 **không phải 112 frame HAL bị rơi**. Consumer đã ngừng hiển thị khi HOME nhưng bản consumer endurance còn giữ Surface, nên renderer chạy thêm trong background trước lúc thu log/dừng process. Không biến chênh lệch này thành kết luận drop chính xác.

## Lifecycle: phát hiện và sửa

Trong bản chạy endurance, HOME không hủy ngay TextureView Surface nên callback `onSurfaceTextureDestroyed` chưa đóng camera. Audit ban đầu ghi 101 decoder creation, 100 release, 1 EGL creation và 0 release tại mốc thu: **FAIL cleanup của bản consumer cũ**. Lỗi này được giữ nguyên trong summary, không xóa bằng chứng.

Đã sửa consumer đóng camera/session/Surface wrapper ở `onStop`, mở lại khi foreground và xử lý trường hợp camera open callback đến sau background. Engine/render APK không thay đổi sau bài endurance; chỉ consumer lifecycle được sửa và rebuild. Bản consumer cuối được kiểm tra ngắn với PID `21835`: **PASS**, 3 switch, 1.146 frame, hai generation 1→2, 5/5 decoder release và 2/2 EGL release, không pipeline error. [Summary lifecycle](lifecycle/summary.json), [bridge JSONL](lifecycle/bridge.jsonl), [consumer JSONL](lifecycle/consumer.jsonl), [logcat](lifecycle/logcat.txt), [build sau sửa](build-lifecycle.txt).

Background làm kết thúc producer/session; foreground tạo lifecycle mới. Cam kết một session áp dụng trong cửa sổ foreground switching, không áp dụng qua background. Bản consumer cuối **không** được chạy lại endurance 30 phút do yêu cầu rút ngắn; kiểm chứng dài chỉ áp dụng engine/EGL không đổi và bản consumer trước sửa shutdown.

## Bằng chứng và tái hiện

- [Bridge JSONL đầy đủ, gzip](endurance/bridge.jsonl.gz), [consumer JSONL đầy đủ, gzip](endurance/consumer.jsonl.gz), [PID logcat, gzip](endurance/pid-logcat.txt.gz). Bản raw còn giữ tại chỗ, được ignore để tránh đưa log lớn vào Git; gzip là nén lossless.
- [Crash buffer](endurance/crash-buffer.txt), [AndroidRuntime/libc/DEBUG](endurance/native-java-diagnostics.txt), [exit info](endurance/exit-info.txt). Không có quyền đọc native tombstone đầy đủ trên thiết bị non-root; kết luận giới hạn ở diagnostics quan sát được.
- [SHA-256 APK/JAR/fixtures/source/log](artifact-inventory.json); LSPatch vẫn là JingMatrix 1.2/487 debug đã PASS Phase 2, không đổi phiên bản hoặc bật signature bypass.
- `python scripts/analyze_phase3.py` đọc raw hoặc gzip và kiểm tra ngưỡng gốc. **Exit 1/FAIL là kết quả đúng với bộ evidence này**, vì thiếu 30 phút và cleanup của consumer endurance cũ. Các tiêu chí timestamp/switch/black/gap vẫn PASS. Không đổi threshold để ép PASS.
- `python scripts/analyze_phase3_lifecycle.py` kiểm tra bản sửa lifecycle và trả PASS. `scripts/run_phase3.ps1` có tham số duration/switch count cho lần thử sau; không tự chạy lại bài dài trong lượt này.

Không thêm UI/controller business functionality; consumer vẫn là test app riêng dùng đúng Surface đăng ký qua Camera2. Chưa kiểm chứng multi-output, ImageReader consumer, still capture, modern SessionConfiguration, context loss, orientation hoặc consumer bên thứ ba. Camera2 metadata/HAL vẫn từ camera thật. Không patch hoặc tích hợp TikTok.
