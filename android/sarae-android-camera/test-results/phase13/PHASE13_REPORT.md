# Phase 13 — LIVE root cause và GO/NO-GO

**PARTIAL_PASS cho điều tra/correction cục bộ. Readiness: NO-GO cho LIVE tiếp theo.**

Không phát hoặc yêu cầu LIVE trong Phase 13. Chính sách cập nhật cho phép cân nhắc lượt LIVE sau khi các gate đáng tin cậy đạt yêu cầu; hiện chưa đạt. TikTok 47.4.3, account và cached Phase 11 module giữ nguyên. Không triển khai bản sửa vào TikTok.

## 1. Phân loại kết luận

| Vấn đề | Phân loại | Bằng chứng / giới hạn |
|---|---|---|
| Hook teardown pipeline của camera mới khi camera cũ bị close lần nữa | **CONFIRMED** | Test đối chứng SM-G996U: old close làm EGL release và mất packet encoder; ownership guard ngăn lỗi. |
| Request đã build trước redirect vẫn chứa Surface cũ | **CONFIRMED** | Baseline lỗi `unconfigured Input/Output Surface`, CameraService 0 request/0 frame dù EGL chạy. Conversion lookup correction khôi phục capture results. |
| Caller gây early teardown trong LIVE Phase 12 là camera cũ/finalizer | **SUSPECTED, mạnh** | Lifecycle và pixel matching phù hợp; Phase 12 thiếu identity/call stack của object gọi close nên chưa thể CONFIRMED caller. |
| Chính EGL bị nghẽn nhiều giây | Không được bằng chứng ủng hộ trong window đã thu | Swap max 6,385 ms, output gap max 56,129 ms trước teardown. Không có trace ownership/fence đầy đủ để loại trừ mọi consumer issue. |
| LIVE native audio qua OpenSL ES / liblyrax | **SUSPECTED** | Phase 12 MIC native/system track + libOpenSLES log, Java read=0. liblyrax có OpenSL recorder và cả các route khác. |
| Native PCM capture entry của phiên LIVE | **UNKNOWN** | Không giữ được active capture stack/API registration trong Phase 12; preview chưa LIVE không có RECORD thread hoạt động. Không triển khai native hook. |

## 2. Correlation mới của freeze Phase 12

Phải sửa cách hiểu sơ bộ “EGL liên tục nên lỗi chắc chắn ở consumer”: log chỉ chứng minh EGL chạy **trước khi bị teardown**, không chứng minh EGL tiếp tục suốt đoạn receiver bị giữ hình. Bản evidence/report Phase 12 được giữ nguyên; phân tích này bổ sung và thay thế suy luận đó.

Chuỗi sự kiện PID 16744:

1. Original Surface **8990532**, dummy **157940265**, `SessionConfiguration`, một output; EGL 1920×1080.
2. Last output elapsed ns **9428084995063276**, sourceTexture **89.032.682.000 ns**.
3. Hook `camera_close` ns **9428085018605828**, sau last output **23,543 ms**.
4. `egl_released` ns **9428085072033276**, sau close **53,427 ms**. Không còn output trong snapshot này.
5. Dùng cặp logcat wall/elapsed cùng PID suy ra hook close khoảng **11:01:02,449 giờ thiết bị**; CameraService ghi disconnect thực tế lúc **11:02:49**, muộn hơn khoảng **107 s**. Wall-time suy ra và log service độ phân giải giây có sai số; không dùng để suy ra network delay.

Pixel matching độc lập, không lấy nguồn từ hook acknowledgment:

- Clip viewer gốc `IMG_5061.MP4`, giữ SHA256 `8328a39e29f4219dc294ab45a864f30856fd19f8ecf843eb4beb3cd375a71f68`.
- Source A tại viewer 8/10/15 s lần lượt khớp **79,033 / 81,033 / 86,033 s**, correlation **0,9940–0,9945**. Offset nội dung trước freeze khoảng **71,033 s**.
- Viewer **17,8 / 18 / 20 / 24 / 35,5 / 40 / 60 / 90 / 107 s** đều khớp A khoảng **88,7 s**, correlation khoảng **0,9924**. Một vài lần thay hình xen giữa khớp các frame cũ 86,9–87,6 s.
- Last renderer PTS 89,0327 s trừ offset nội dung dự đoán cuối output ở viewer khoảng **18,00 s**, sát hold đầu **17,8 s**. Crop/seek/frame matching chỉ có độ chính xác xấp xỉ 0,1 s, không phải timestamp packet tuyệt đối.
- **0 lệnh next trong LIVE**; hai switch B→A trước LIVE không nằm trong window này.

**Diễn giải:** việc source ngừng gần đúng frame cuối trước close/release giải thích freeze tốt hơn giả thuyết renderer vẫn sản xuất vô hạn trong khi consumer không lấy hình. Chưa có log caller để chứng minh camera cũ cụ thể đã đóng; chưa có counter packet của HEVC encoder TikTok để xác định chính xác phản ứng downstream.

### Root defect ownership

Hook cũ `CameraDeviceImpl.close()` luôn đóng `provider`/dummy toàn cục, kể cả object khác object tạo session hiện hành. Android close vốn idempotent; framework cũng có `finalize()` gọi close. Hook after-method vẫn phải tự bảo vệ ownership khi close cũ return sớm. [AOSP Android 14 CameraDeviceImpl](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android14-release/core/java/android/hardware/camera2/impl/CameraDeviceImpl.java).

Bản sửa lưu object `redirectedDevice`, so sánh **identity** (không so camera id), khóa lifecycle và bỏ qua close không thuộc owner. Log mới có closing/owner identity và stack. Không thay EGL/decoder.

## 3. Dummy stream 0 frame và capture metadata

Snapshot LIVE: camera **CONFIGURED**, request counter **0**, produced **0**, last timestamp **0**, no in-flight requests, metadata chưa allocated. Đây là **HAL→dummy ImageReader 640×480 YUV**; không phải counter của EGL→original Surface 1920×1080. Không có dấu hiệu queue đầy trong snapshot này; chưa có request gửi xuống là lời giải thích trực tiếp hơn “dummy bị consumer không drain”.

Hook cũ chỉ sửa `Builder.addTarget()` khi mapping đã tồn tại. Request build trước `createCaptureSession()` không được cập nhật. Android chuyển target thành configured stream IDs trước submit; target ngoài configured outputs bị từ chối. [AOSP CaptureRequest Android 14](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android14-release/core/java/android/hardware/camera2/CaptureRequest.java).

Correction thử nghiệm dùng **bản sao lookup SparseArray** tại `convertSurfaceToStreamId()`, giữ nguyên stream ID dummy nhưng ánh xạ original Surface cho bước lookup. Không sửa immutable request, không thay metadata/tag, không mutate configured outputs thật. Chỉ bật cho `co.sarae.camera.consumer`; **chưa bật cho TikTok**. Snapshot preview TikTok chưa sửa ở Phase 13 cũng có 0 request/0 frame. Tuy nhiên chưa log được submission/call stack TikTok để khẳng định nó thực sự submit request build sớm, thay vì chưa submit vì nguyên nhân khác.

### Giới hạn metadata còn mở

- CaptureResult là sensor/dummy camera thật, không tự trở thành metadata của MP4.
- Consumer probe đo sensor timestamp REALTIME khác injected PTS `System.nanoTime()` khoảng **7,0566×10^15 ns**. Không thể join hai timestamp trực tiếp. [Camera timestamp source](https://developer.android.com/reference/android/hardware/camera2/CameraCharacteristics#SENSOR_INFO_TIMESTAMP_SOURCE).
- Remap lookup vẫn chưa giải quyết contract `onCaptureBufferLost` cho target original và chưa ghép từng injected frame với capture result. Không sửa timestamp toàn cục khi chưa xác định consumer có phụ thuộc join này hay không.
- Chưa hỗ trợ nhiều output, physical/reprocess/high-speed sessions ngoài cấu hình đã có.

## 4. Test đối chứng trên thiết bị, không broadcast

Giữ nguyên Video Engine/decoder/EGL. Consumer hiện có nhận thêm test extras và callback diagnostics. Cờ tắt correction chỉ tồn tại cho consumer lab.

| Ca | EGL output | CameraService | Kết quả |
|---|---:|---|---|
| Prebuilt request, mapping tắt | 190 frame | CONFIGURED, 0 request, 0 produced | Tái hiện lỗi submit; vẫn có 185 TextureView updates. |
| Prebuilt request, mapping bật | 195 frame | ACTIVE, 1 request, 71 produced tại snapshot | 90 capture-completion đã log tới cuối lượt; request identity/tag giữ nguyên. |
| Close camera cũ, guard tắt | 78 frame | Sau đó CONFIGURED, 42 produced | EGL release **46,236 ms** sau trigger; 0 output sau trigger; có capture failure do teardown dummy. |
| Close camera cũ, guard bật | 237 frame | ACTIVE, 91 produced tại snapshot | **159 output sau trigger**, old close bị bỏ qua; không early release, capture failure=0. |

Snapshot CameraService và cuối file callback không cùng thời điểm nên số produced/callback không dùng làm hai counter đồng thời.

### Kiểm chứng encoder thực tế cục bộ

Theo chính sách cập nhật, bổ sung **MediaCodec Surface-input encoder + MP4 muxer** vào consumer diagnostic. Đây là sink, không phải engine/decoder thứ hai. Dùng đoạn footage chuyển động từ MP4 A, prebuilt request remap bật ở cả hai ca, đóng camera cũ tại khoảng 2,5 s. Không gửi mạng, không audio track trong probe này.

| Ca encoder | Packet / duration MP4 | PTS regression | Max packet gap | Sau retired close |
|---|---|---:|---:|---|
| Guard tắt | **78 / 2,5966 s** | 0 | 35,967 ms | Không có encoded packet log mới; output hết sớm dù probe chạy khoảng 8 s. |
| Guard bật | **251 / 8,3642 s** | 0 | **35,267 ms** | 173 EGL output và 6 encoded-packet progress events mới; worker đóng bình thường. |

ffprobe xác nhận container và packet; decoded footage có chuyển động (subject MAD median 5,82/255 trong ca sửa), median frame-source correlation **0,9916** với source A. Min correlation **0,8428** cũng được giữ trong JSON, không gọi mọi frame đã được xác minh hoàn hảo. Max gap giữ nguyên tiêu chí **<100 ms**.

Probe dùng **c2.qti.avc.encoder 640×480**. Phase 12 sender có **c2.qti.hevc.encoder**; test này xác nhận cơ chế mất producer làm encoder hết input, **không** chứng nhận encoder HEVC / resolution / effect graph của TikTok. Hai MP4 ở `.tools/phase13/encoder-baseline.mp4` và `encoder-corrected.mp4`.

## 5. Native audio

Đã kiểm hash **61 APK gốc**, scan **250 ELF**. `liblyrax.so` có imported `slCreateEngine`, `SL_IID_RECORD`, `SL_IID_ANDROIDSIMPLEBUFFERQUEUE`; strings có `AudioRecordOpenSLES`, `CreateAudioRecorder`, các route Java/AAudio. Những thư viện khác cũng import OpenSL cho recorder hoặc playback; không hook theo mọi nơi có symbol.

Đã xác nhận `liblyrax.so` được map khi mở **LIVE preview chưa phát**, và hash installed trùng artifact gốc:
`9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de`.

| Quan sát | Phase 11 recording | Phase 12 LIVE | Phase 13 chưa LIVE |
|---|---|---|---|
| API/capture | Java `AudioRecord.read(ByteBuffer,int)` confirmed | API entry chưa xác định; MIC track của PID 16744 active | Preview có liblyrax, **0 RECORD thread** |
| Format | 44.100 Hz stereo PCM16 | 48.000 Hz stereo PCM16 | Không có active capture format để xác minh |
| Buffer | Java 2.664 frame; read đầu 7.104 byte =1.776 frame | HAL 960 frame/20 ms; track 2.880 frame | Không có native completed-buffer ownership/size |
| Replacement | MP4 soundtrack được xác minh trong clip saved | Java read/substitution=0; viewer nghe mic | Không tạo capture/LIVE để ép lấy stack |

`libOpenSLES` log của PID LIVE cộng với recorder implementation trong liblyrax làm OpenSL là ứng viên mạnh, **chưa loại trừ AAudio/native AudioRecord/route khác**. Library mapped không chứng minh backend đang chạy. Phase 12 không giữ native capture call stack; stack Handler-dead-thread ở cuối phiên là release path, không phải PCM entry. Không có backtrace active PCM hợp lệ để báo CONFIRMED.

**Đề xuất có điều kiện, chưa implement:** nếu xác nhận recorder OpenSL, chỉ theo đúng recorder object/callback registration và config quan sát được, thay PCM của completed recording buffer trước khi chuyển tới callback ứng dụng; giữ queue/lifecycle/return contracts. Reuse bounded PCM Engine và source commit clock. Phải phân biệt recorder/player, buffer đang thuộc HAL/app, sample layout và native start/stop/release trước khi can thiệp. Không hook mọi `AudioRecord::read`/AAudio/OpenSL hoặc giả định JNI `nativeAudioRecordWritePcm` là mic capture entry. [Android OpenSL recorder/buffer queue contracts](https://developer.android.com/ndk/guides/audio/opensl/opensl-for-android).

## 6. BufferQueue: kết luận được và chưa được

Phase 12 swap p50 **0,582 ms**, p95 **1,432 ms**, max **6,385 ms**; max draw **17,765 ms**, tick-to-present **23,889 ms**. Không có queue/dequeue timeout được giữ cho original Surface. Các `detachBuffer ... state=FREE` trước session mới thuộc decoder/preview teardown cũ, chưa có bằng chứng gây freeze sau đó. `Davey` UI stalls không phải timing của Sarae EGL Surface.

Original Surface hash không xác định đầy đủ BufferQueue unique ID hoặc owner của mỗi buffer. Không có acquire/release/fence trace của original consumer hoặc per-frame TikTok encoder dequeue trong evidence. CameraService dump chỉ mô tả dummy stream. [AOSP BufferQueue ownership/trace](https://source.android.com/docs/core/graphics/arch-bq-gralloc).

| Cần phân biệt | Measurement có giá trị | Hiện có |
|---|---|---|
| Producer bị teardown | Close caller/owner identity, EGL release, packet/input dừng | Tái hiện CONFIRMED; historical caller thiếu. |
| Capture metadata starvation | Submit exception/result, actual requests, capture-completion và SENSOR_TIMESTAMP | 0 request/frame historical; reproduction và correction consumer PASS. |
| Consumer không latch | Original queue acquire/release, SurfaceTexture update/timestamp trên consumer | Không đủ trace TikTok. |
| Encoder starvation | Actual encoded packet PTS/count cùng input/frame marker | Có local probe; chưa có TikTok packet trace. |
| Buffer ownership/fence stall | Queue/dequeue duration, acquired count, release fence, bounded gfx/sched trace | Chưa có trace đúng original queue. |

## 7. GO/NO-GO trước khi cân nhắc LIVE khác

**Tất cả gate bắt buộc phải PASS; chưa rõ = NO-GO. Không dùng LIVE để bù các gate đang thiếu.**

| Gate khách quan | Hiện trạng |
|---|---|
| Retired close không release producer hiện hành; packets tiếp tục sau trigger; request identity giữ nguyên | **PASS trên consumer/probe**. Chưa deploy/verify candidate trong TikTok. |
| Prebuilt request được submit thực sự, capture-completion tăng; không metadata starvation ở cấu hình target | **PASS consumer; UNVERIFIED TikTok candidate**. |
| Hardware encode từ moving MP4, zero PTS regression, gap <100 ms, artifact decoded khớp nguồn | **PASS local AVC**; HEVC/config/effect graph TikTok chưa bao phủ. |
| Xác nhận native capture entry, backend, callback buffer ownership/format bằng runtime evidence | **NO-GO: UNKNOWN entry**. |
| PCM MP4 được xác minh ở đầu ra local sử dụng đúng native capture path; MICROPHONE/MUTED và A→B→A đúng nguồn, không wrong-source sustained | **NO-GO: chưa có bridge native**. Java recording Phase 11 không thay cho gate này. |
| Capture metadata/injected timestamp contract được kiểm chứng, A/V drift và các underrun được đo | **OPEN**: có timebase mismatch; chưa xác định consumer join rule; B-head underrun Phase 11 chưa được sửa trong phase này. |
| Candidate module hash/load được xác minh trong target lab, giữ account/data, không còn build/update pending | **OPEN**: TikTok vẫn dùng Phase 11 theo chủ đích bảo toàn evidence. |

Ngay cả các gate cục bộ trên đạt hết, chúng vẫn không chứng minh truyền tới viewer hoặc độ trễ mạng. Receiver moving video/audio, A→B→A thực tế, session sau Start LIVE và sự ổn định truyền phát chỉ kiểm chứng được bằng một lượt end-to-end được người dùng điều khiển **trong tương lai khi đủ GO**, không trong Phase 13. Hiện không yêu cầu lượt đó.

## 8. Thay đổi, timing và evidence

- `Camera2HookPrototype.kt`: ownership guard/lifecycle lock + close diagnostics; submission lookup correction cho consumer riêng.
- `HookEntry.kt`: chỉ bật submission remap trong consumer. Ownership correction mới chỉ chạy ở candidate consumer; TikTok đang cài chưa nhận module mới.
- `ConsumerActivity.kt`, `EncoderProbe.kt`: test extras, capture callbacks, retired-close trigger, actual local encoder sink. Không thêm UI/module/engine.
- `inspect_phase13_native.py`, `analyze_phase13.py`, `verify_phase13.py`, `verify_phase13_encoder.py`: static scan, offline matching và bounded verification. Java Audio Bridge, PCM Engine, EGL và decoder source không thay đổi.

Build PASS bằng JDK 21 và Gradle offline. Module/consumer build theo dependency cần thiết; lượt encoder cuối chỉ build consumer. Cài consumer lần đầu sai signer bị từ chối, sau đó dùng đúng debug signer đã có; **không uninstall consumer/TikTok**. Cả failed install và UI dump timeout 8 s đều nằm trong budget, không retry LIVE.

**Tổng bảo thủ: 276,393 s (<300 s)**: 41,752 s chuẩn bị/harness/read-only trước preview + **209,329 s toàn bộ span điều hướng/chờ preview/cleanup** + 25,312 s encoder harness. Không trừ thời gian suy luận/chờ UI trong span preview. Tổng command/stage active riêng là 85,128 s, không dùng con số thấp đó để thay budget bảo thủ. Build/static/offline không tính. Không test thêm.

Cleanup: HOME, CameraService active clients `[]`; không force-stop TikTok, không re-install/patch/cache-reload/clear data. Cached module trước/sau/final vẫn SHA256 `2c694dfd0e578e7374b656e7f1f86ad0c9dbbd85172d5b522e66973b84d1b6d0`; AUDIO mode không thay đổi (MICROPHONE). Không phát, quay hoặc đăng nội dung bằng TikTok trong Phase 13.

Evidence: `analysis.json`, `native-static.json`, `device-timing.json`, `results.json`, per-case bridge/consumer JSONL và CameraService dumps, encoded ffprobe packets, `evidence-sha256.json`. Full account-bearing logcat/maps/UI ở `.tools/phase13-*` hoặc `.tools/phase13/`, không đưa vào Git. Original Phase 12 media/evidence không sửa; hash manifest được kiểm lại.
