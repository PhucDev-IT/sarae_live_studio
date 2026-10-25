# Phase 12 — Real TikTok LIVE video & audio

**Kết quả: FAIL.** Đã sử dụng đúng **một** phiên LIVE do người dùng tự bắt đầu và tự kết thúc vì cả máy phát và viewer bị đứng hình. Không yêu cầu phiên thứ hai; không restart, force-stop, patch, cài lại hay reload module để thử lại.

## Kết quả từng gate

| Gate | Kết quả | Bằng chứng / giới hạn |
|---|---|---|
| Preflight module | PASS | Cache LSPatch đúng SHA Phase 11; PID 16744 log `pipelineVersion=phase11`, VIDEO có ack mới; A/B đúng hash. Không cần khôi phục cache. |
| Preview / orientation | PASS trong phạm vi quan sát | Video thật ở preview đúng chiều; cấu hình auto, FIT, override null. Hai lệnh B→A hoàn tất **trước LIVE**. Viewer ban đầu cũng thấy người và chữ đứng đúng chiều. Chưa đo aspect ratio định lượng ở receiver. |
| Actual LIVE | CONFIRMED | Người dùng báo ĐANG LIVE; ảnh sender có UI đang phát; clip viewer độc lập có UI LIVE, video và audio. |
| Receiver moving video / continuity | **FAIL** | Có chuyển động ban đầu rồi giữ hình. Clip độc lập xác nhận hold từ khoảng 17,8 s; đoạn giữ hình dài về cuối. Người dùng xác nhận cả sender và viewer đều bị đứng. |
| Receiver MP4 soundtrack | **FAIL** | Người dùng báo chỉ nghe mic. Không có Java AudioRecord read/substitution trong window LIVE; AudioFlinger có track MIC đang hoạt động của PID 16744. Waveform viewer không xác nhận được soundtrack A/B. |
| LIVE A→B→A video/audio | **NOT TESTED** | **0 lệnh đổi nguồn trong LIVE**. Dừng kế hoạch sau khi báo freeze. Hai switch trước LIVE không được tính vào gate này. |
| A/V synchronization | **UNVERIFIED** | Soundtrack MP4 không được xác nhận, video bị giữ hình. Không có shared clock để đo network delay. |
| Local EGL output cadence | PASS trong window log | 2.671 output trong 88,995 s, max gap **56,129 ms**, 0 gap ≥100 ms, 0 PTS không tăng. Đây không phải PASS truyền hình. |
| Crash / lifecycle stability | UNVERIFIED đầy đủ | Không lỗi Java/native fatal trong phần log thu được, crash buffer rỗng. PID lúc cleanup khác PID LIVE; chưa xác định nguyên nhân kết thúc PID cũ. Không chứng nhận “không crash” toàn phiên. |

## Clip viewer và mốc 17 giây

File gốc do người dùng cung cấp, giữ nguyên: `C:\Users\nguye\Downloads\IMG_5061.MP4`.

- 107,966667 s; H.264 880×1920, khoảng 59,842 fps; AAC stereo 44.100 Hz.
- SHA256 `8328a39e29f4219dc294ab45a864f30856fd19f8ecf843eb4beb3cd375a71f68`.
- Phân tích ROI nội dung `[x=100,y=350,w=740,h=900]`, bỏ clock, chat và controls; lấy mẫu 10 fps, grayscale 148×180. Những giây đầu có overlay Control Center nên không dùng để kết luận chuyển động của footage.
- Với MAD giữa frame liên tiếp <0,5/255: hold **17,8–21,1 s (3,3 s)**, **23,9–29,0 s (5,1 s)**, **31,3–35,1 s (3,8 s)** và **35,2–105,9 s (70,7 s)**.
- Với ngưỡng chặt hơn <0,25/255: đoạn giữ hình dài **35,3–105,2 s (69,9 s)**. Các dao động nhỏ về cuối là lý do không gọi toàn bộ đoạn này là các frame bit-identical. Xem sensitivity ở `analysis.json` và `viewer-content-timeline.png` tại máy local.
- Các ảnh 18/20 s cùng giữ một tư thế; đoạn 60/90/107 s cũng giữ cùng tư thế. Điều này phù hợp báo cáo bắt đầu đứng từ khoảng 17 s, có vài lần thay hình tiếp theo trước khi giữ hình kéo dài.

**Không có bằng chứng freeze do lệnh next của agent:** `adb.jsonl` chỉ có TEST_SWITCH B và A trước `human_live_started`. Trong window session mới chỉ có commit A, switchId=0 (initial load); không có commit B. Sự kiện `switch_complete id=0` là khởi tạo nguồn A của session mới, không phải một lệnh next trong LIVE.

## Camera2 / EGL: quan sát và điều chưa biết

TikTok tự đóng camera preview cũ và tạo session mới gần lúc bắt đầu LIVE:

- `CameraDeviceImpl.createCaptureSession(SessionConfiguration)`, sessionType=0, input=null, **1 output**.
- Camera `id=1`, front, SENSOR_ORIENTATION=270, displayRotation=0.
- Original Surface identity **8990532**, dummy **157940265**; EGL output **1920×1080**.
- Metadata MP4 540×960, mediaRotation=0. Auto profile áp dụng output rotation 90° và mirror bù cho front preview; không đặt rotationOverride và không sửa renderer trong phase này.
- Session mới redirect tại elapsed ns `9427995822473674`; một EGL producer được tạo, sau đó đóng tại `9428085072033276`. Không có session thứ ba trong snapshot LIVE.
- Trong session mới: output sourceTexture tăng từ **66.015.000** đến **89.032.682.000 ns**, **1.899** lần đổi texture timestamp. Khoảng cách lớn nhất giữa các lần đổi texture **88,700 ms**. Output PTS tăng đều, swap tiếp tục được ghi nhận.

**Kết luận giới hạn:** bằng chứng không cho thấy decoder/renderer dừng nhiều giây ở window đã thu. Freeze vẫn hiện rõ ở clip receiver và được báo ngay trên sender. Điểm nghẽn có thể ở đường TikTok tiêu thụ Surface / xử lý preview / encoder sau EGL, nhưng **chưa xác định được khâu gây lỗi hay root cause cụ thể**. Không sửa EGL hoặc timestamp dựa trên phỏng đoán. Clip receiver và runtime log không có marker chung, nên không gán chính xác “giây 17” vào một renderer event.

## Audio: blocker cụ thể

Module Phase 11 đã load, mode VIDEO đã ack trước LIVE. Sau preview có audio decoder AAC 48 kHz stereo, `audio_pcm_ready`, 7.439 `pcm_write` (window bao gồm đuôi preview và LIVE). Tuy nhiên:

- **0** `audio_capture_start`, **0** `audio_read_confirmed`, **0** `pcm_substituted` mới trong window LIVE.
- AudioFlinger: active RECORD thread `AudioIn_62E`, client **PID 16744**, session **56441**, track **3535**, **48.000 Hz / PCM16 / stereo**, MIC / AUDIO_DEVICE_IN_2MIC. HAL 960 frame, track buffer 2.880 frame; read 1.274.880 frame khi snapshot.
- Có dòng `libOpenSLES` tại 10:59:43.612 của PID 16744. Đây là dấu hiệu phù hợp với native audio path, **không đủ xác nhận chính xác entry point capture là OpenSL ES**. Chưa trace native API/call stack.
- Bridge hiện chỉ hook Java public AudioRecord read. Vì không có hooked read, PCM decode được không đi vào đường capture đã quan sát của LIVE. Không được dùng decoder readiness làm audio bridge PASS.
- Tám probe audio receiver dài 5 s tại 0/10/15/20/35/60/90/100 s được cross-correlate với toàn bộ source A/B ở mono 4 kHz. Max absolute correlation A **0,056844**, B **0,066193**: không xác nhận waveform soundtrack. Low correlation riêng lẻ không loại trừ mọi biến đổi/mix audio; kết luận FAIL dựa thêm báo cáo người dùng và capture diagnostics.
- Không có replacement read nên **không đo được underrun/resync của audio LIVE bridge**. Không diễn giải “0 underrun log” thành audio ổn định.

## Nguồn và phiên bản đã dùng

| Artifact | SHA256 |
|---|---|
| Installed TikTok 47.4.3 base, giữ nguyên | `4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b` |
| Phase 11 cached module `cache/lspatch/co.sarae.camera.poc/183496786.apk` | `2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0` |
| JingMatrix LSPatch v1.2/487 debug jar, pinned từ phase trước | `5f2afaecc0fe36296bdd607acce2521a1dadd1c7c8ab6e3d163cbc77d8121153` |
| A `c56c3dcfeb91499d970d953604771f88.mp4` | `9bfea45a25cb2b9f41e2bb93b1af9e42781bcf911f40a3f158df01c775b8e90c` |
| B `5baed9ab52bf4730a3db889c3707dd12.mp4` | `82ba1c36acff4436638a888e8f0589e6bf1bda9824a8697aa8a19738ec0fad77` |

A dài 383,421 s; B dài 491,839 s; cả hai H.264 540×960 30 fps + AAC stereo 48 kHz, không có rotation metadata. Upload và kiểm hash, đổi file engine trước LIVE; giữ file cũ `phase12-original-A.mp4/B.mp4`. Không sửa APK, module hay engine. Cache Phase 11 tồn tại sẵn, không cần restore.

## Thời gian và cleanup

- **62,033 s** tổng thời gian agent chạy các stage kiểm chứng thiết bị, gồm chuẩn bị nguồn, các stage thất bại có ghi budget, thu diagnostics và cleanup. Dưới 300 s; thời gian chờ người dùng và phân tích offline không tính. Xem `budget.jsonl`.
- Clip receiver dài **107,967 s**, không đồng nghĩa tổng thời lượng LIVE. Timestamp human_start/end trong `state.json` là lúc agent ghi nhận phản hồi, không phải timestamp chính xác nhấn phát/tắt; không lấy hiệu hai timestamp đó làm thời lượng phát.
- Người dùng tự tắt LIVE. Cleanup chụp màn hình home, CameraService `Active Camera Clients: []`; không có thao tác tự kết thúc broadcast.
- Lúc cleanup PID **23365** (khác PID LIVE 16744), có Phase 11 attachment với default **MICROPHONE**. Đã gửi thêm intent MICROPHONE sau xác nhận người dùng kết thúc. Broadcast result=0 **không phải ack mode mới**; log attachment hiện có xác nhận MIC mặc định, không có mode ack mới trong snapshot cleanup. Không mở lại TikTok để ép lấy ack.
- Không uninstall/clear data/rollback, không sửa account. Không có hook hay code Android mới được deploy. Không khẳng định nguyên nhân PID đổi khi chưa có process-exit diagnostics.

## Bằng chứng và tái phân tích ngoại tuyến

- `analysis.json`: frame hold sensitivity, waveform probes, runtime counts/timings.
- `results.json`: gate độc lập và blocker.
- `state.json`, `protocol.jsonl`, `budget.jsonl`, `adb.jsonl`: session duy nhất, lệnh đã chạy và thời gian.
- `frozen-bridge.jsonl`, `frozen-audio.jsonl`, `live-A-*.jsonl`: JSONL snapshot PID LIVE. `final-*` là PID cleanup, không dùng nhầm làm toàn bộ log LIVE.
- `runtime-evidence.txt`: trích đoạn camera/audio native chẩn đoán. Full dumpsys/logcat ở `.tools/phase12/private/`.
- `evidence-sha256.json`: hash các evidence local và media gốc. Screenshots/XML được gitignore vì có UI tài khoản; file viewer gốc không copy vào Git.

Chạy từ project bằng Python có NumPy/Pillow: `python scripts/analyze_phase12.py`. Script chỉ đọc media và evidence host, không gọi ADB. Cần giữ nguyên ba media gốc theo đường dẫn đã ghi.

## Blocker trước Live Studio và bước tiếp theo

1. **Video:** xác định nơi ngừng tiêu thụ/hiển thị frame sau EGL bằng chẩn đoán producer/consumer và timestamp/encoder path. Renderer counters không đủ phân biệt preview transform, encoder starvation, consumer queue hoặc app freeze. Chưa có fix được chứng minh.
2. **Audio:** xác định đúng native capture entry point/call stack trước khi chọn hook có phạm vi hẹp; Java AudioRecord recording PASS của Phase 11 không bao phủ đường LIVE đã quan sát.
3. **A/B và sync LIVE:** chưa test do phiên duy nhất đã dừng; không xin hoặc tự chạy phiên thứ hai để bù bằng chứng. Kết quả recording cũ vẫn chỉ áp dụng cho recording.

Phase này dừng ở chẩn đoán và phân tích evidence. Không tuyên bố tương thích LIVE hoặc production ready; không tích hợp Live Studio dựa trên kết quả này.
