# Phase 8 — Rotation & switching, 24/10/2026

## Kết quả

**PARTIAL_PASS. Rotation tự động PASS; functional switching PASS; cadence toàn pipeline FAIL theo ngưỡng `<100 ms`.** Không tuyên bố stall đã được sửa hoặc LIVE broadcasting compatible.

| Mục | Kết quả | Bằng chứng |
| --- | --- | --- |
| Portrait đứng đúng chiều, không mirror sai | **PASS** | MP4 thật trong TikTok LIVE preview; người/áo/quần đứng đúng, chữ trên túi đọc bình thường; override null |
| Source A/B | **10/10 PASS** | Hai lượt × 5 switch, ảnh thật sau từng switch và renderer source timestamps |
| Session/Surface | **PASS trong từng lượt** | Mỗi lượt một Camera2 session và một EGL output; không tạo lại khi switch |
| Output PTS | **PASS** | 2.759 output, 0 timestamp không tăng |
| Cadence trong switch windows | **PASS trong mẫu** | Max 45,112 ms và 42,733 ms |
| Cadence toàn foreground session | **FAIL** | Normal playback có gap 161,081 ms và 161,628 ms |
| Stall fix | **Chưa hoàn tất** | Định vị được một stall ở latch/native mutex; chưa biết lock/owner; stall thứ hai khác biểu hiện |
| Crash | Không thấy trong dữ liệu thu | JSONL không pipeline error, crash buffers rỗng; logcat thu riêng bị timeout nên không trọn vẹn |
| Unit/build | **PASS** | 9 geometry tests, build chỉ app/engine/bridge cần thiết; không build consumer-test |

[Summary](summary.json), [analysis lượt auto](auto-run/analysis.json), [analysis bản cuối](final-run/analysis.json), [visual review](visual-review.json), [unit tests](unit-tests.xml).

## Target và trạng thái cài đặt

- Branch `feature/tools-android`; Samsung SM-G996U, Android 14/API 34, non-root. TikTok lab `com.ss.android.ugc.trill` 47.4.3/470403; JingMatrix LSPatch v1.2/487 debug, signature bypass **0**.
- Không uninstall, clear data, đăng nhập/đăng xuất, thay account settings, quay/đăng video hoặc nhấn **Phát LIVE**. Các ảnh vẫn có nút Phát LIVE của màn hình chuẩn bị broadcast.
- Update base cùng signer bằng `pm install-create -r -p com.ss.android.ugc.trill`, `install-write`, `install-commit`; inherit 60 split. Bộ cuối vẫn **61 APK**. `firstInstallTime=2026-10-23 22:41:09`, version và signer không đổi. Giữ dữ liệu bằng cơ chế update; không đọc private account database để tuyên bố so sánh từng byte.
- [Package trước](package-before.txt), [sau update đầu](calibration-visual/package-after.txt), [sau update cuối](final-run/package-after.txt), [61 đường dẫn cuối](final-run/paths-after.txt), [install cuối](final-run/install.txt).

| Artifact cuối | SHA-256 |
| --- | --- |
| TikTok lab base | `201abaaca8432a512ab283686c11014a399f74eeae0f4bd508393b084aafbe8b` |
| Sarae module | `63bb56fbee356c0ac1305910c2ea563580dbdec43531769daa10aa41b63d27eb` |
| Lab signer certificate | `8da3f4d5bb423f8150de8789ce27202cefb621b3acb9d4d13f8f24b639094e3b` |

[Static validation cuối](final-update-static.json) kiểm tra package/version, certificate, embedded module hash, origin hash/ZIP CRC và loader config. APK giữ riêng tại `.tools/phase8/final/`; không phân phối TikTok binary qua source. Ba build base tương ứng calibration, inverse transform và bản cuối giữ nguyên decoder behavior khi dùng trực tiếp; không đổi loader version hoặc bypass.

## Điều tra orientation

### Dữ liệu xác nhận

| Tầng | Quan sát |
| --- | --- |
| MP4 A | H.264 576×1024, 30 fps, 11 s, không rotation side data; MediaMetadataRetriever rotation 0 |
| MP4 B | H.264 720×1280, khoảng 25,167 fps, 16,967 s; MediaMetadataRetriever rotation 0 |
| MediaFormat | Rotation absent → 0; SAR 1:1 trong footage đã dùng |
| Decoder SurfaceTexture | `1,0,0,0, 0,-1,0,0, 0,0,1,0, 0,1,0,1`: flip dọc cho texture sampling, không hoán đổi trục 90° |
| Camera TikTok mở | ID **1**, SENSOR_ORIENTATION **270**, LENS_FACING **0/front**, display rotation **0** |
| EGL | Viewport/output **1920×1080**, source portrait; raw readback trước bù vẫn đứng đúng chiều |
| TikTok pre-LIVE preview | Khi EGL upright: sideways và reflected; khi chỉ đảo rotation: có thể lộn ngược hoặc upright nhưng chữ mirror |

[MP4 inventory tái sử dụng](source-media.json), [metadata/texture/geometry log](final-run/analysis.json), [camera service evidence cũ](reused-camera-evidence.txt), [buffer EGL upright trước correction](live-calibration/diagnostics/egl-live-before.png), [TikTok trước correction](live-calibration/live-before.png).

**Nguyên nhân orientation được xác định ở net consumer transformation sau EGL**: TikTok xử lý input như buffer camera trước, áp rotation/reflection; MP4 đã upright nên cần đưa vào hệ tọa độ mà consumer mong đợi. Không phải MP4 có tag xoay 90°, decoder tự làm ảnh sideways hoặc EGL viewport tự xoay. Phần kéo giãn toàn viewport ở renderer cũ cũng đã được thay bằng geometry giữ tỷ lệ.

Đây là kết luận từ đối chiếu pixel và metadata; chưa xác định tên shader/hàm nội bộ của TikTok. Không giả định mọi app/mode có cùng mapping.

### Correction và kiểm chứng

- Engine nhận sensor orientation, facing, display rotation từ bridge. Tính relative rotation theo camera metadata; composition riêng cho lab TikTok đánh dấu **front preview reflection đã được pixel xác nhận**. Geometry áp inverse rotation **và** inverse reflection, giữ ma trận SurfaceTexture do decoder cung cấp.
- Với mẫu đã đo, net consumer mapping có thể biểu diễn `C = R270 × Fx`; inverse renderer là `Fx × R90` trong hệ tọa độ GL. Số 90 này là **kết quả** sensor 270/front/display 0, không phải constant áp cho mọi thiết bị. Unit tests kiểm tra composition trả lại source axes.
- Calibration chỉ dùng override 270 để phân biệt rotation và reflection: ảnh upright nhưng chữ mirror. [Ảnh calibration](live-calibration/live-calibration-270.png). Bản auto ban đầu chỉ đảo rotation cho ảnh lộn ngược: [negative evidence](live-calibration/live-auto-initial.png). Các trường hợp FAIL này được giữ.
- Bản cuối `rotationOverride=null`, `autoCameraOrientation=true`, `mirrorX=false`; engine tự triệt reflection vốn có của consumer. Kết quả hiển thị **không mirror source**. `rotationOverride` tùy chọn 0/90/180/270, mặc định tắt; không UI mới.
- Source metadata rotation do renderer sở hữu chỉ ở StableFrameProvider. Decoder dùng trực tiếp ở controller giữ behavior rotation cũ, tránh double rotation hoặc regression ngoài bridge.
- FIT mặc định; FILL cấu hình được. Pure geometry hỗ trợ portrait/landscape và các quarter turn, scale đều theo pixel aspect; không thay output Surface/EGL context khi đổi source. Footage thực đã test là hai portrait có square pixels. Non-square SAR và arbitrary crop metadata chưa được xử lý đầy đủ.

Tài liệu nền: [Camera preview orientation](https://developer.android.com/media/camera/camera2/camera-preview), [SurfaceTexture sampling transform](https://developer.android.com/reference/android/graphics/SurfaceTexture#getTransformMatrix(float[])), [MediaFormat rotation](https://developer.android.com/reference/android/media/MediaFormat#KEY_ROTATION). Camera metadata cho quy ước hệ tọa độ; pixel thực xác nhận reflection/order của target này.

### Ảnh sau sửa

[Bản cuối A upright, chữ bình thường](final-run/A-before.png), [B thật sau switch](final-run/switch-1-B.png), [A sau switch ngược](final-run/switch-2-A.png).

[Contact sheet 5 switch lượt auto](auto-run/contact-sheet.png) và [5 switch bản cuối](final-run/contact-sheet.png) được đối chiếu với source footage. 10/10 ảnh đúng nguồn và giữ orientation. Không thấy black frame trong các ảnh; không có capture liên tục từng frame để chứng minh zero transient black frames hoặc mọi source edge đều không bị compositor crop. FIT/FILL được kiểm chứng bằng unit test; crop/scaling ngoài EGL thuộc consumer vẫn cần đánh giá ở mode khác.

## Cadence và switch

| Đo | Auto run | Final run |
| --- | ---: | ---: |
| PID | 15529 | 17808 |
| Original Surface identity | 44394571 | 114568893 |
| Camera2 sessions / EGL output trong bài | 1 / 1 | 1 / 1 |
| Active session | 45,394 s | 47,244 s |
| Output samples | 1.352 | 1.407 |
| Output FPS, weighted gaps | 29,917 | 29,916 |
| Normal max gap | **161,081 ms** | **161,628 ms** |
| Switch-window max gap | 45,112 ms | 42,733 ms |
| Output PTS regressions | 0 | 0 |
| Switch commit latency range | 151,233–222,959 ms | 142,435–207,906 ms |

Switch window giữ định nghĩa Phase 7: request → renderer commit + 1 s, tính cả output interval giao cửa sổ. Renderer commit không đồng nghĩa end-to-end display latency. Source arrival, media PTS, texture PTS và output PTS được log riêng. Không đổi threshold hoặc loại gap normal khỏi kết quả tổng để PASS.

Hai lượt là hai lần mở preview/cập nhật module riêng; **không tuyên bố toàn phase chỉ một camera session**. Trong mỗi lượt 5 switch, original Surface, session và EGL output giữ nguyên; chỉ decoder slot hiện có được preload/release theo cơ chế cũ. Không build consumer hoặc tạo engine khác.

## Stall diagnostics và mức kết luận

Đã thêm timing: scheduled/start tick, latch, draw, swap, slot construction, decoder worker polling; giữ **emission timestamp và handler receipt**. Logger synchronous vẫn giữ nguyên, đo serialize/write/flush/logcat; output log call duration cũng được đo. Native atrace sections `Sarae.latch`, `Sarae.draw`, `Sarae.swap`, `Sarae.decodeTick` kết hợp Perfetto scheduling/bionic. Không chuyển sang async logger khi chưa có bằng chứng nó gây gap.

### Stall thứ nhất: định vị được native latch contention

Gap **161,081 ms** ở A normal playback, khoảng 5,408 s sau session start:

- Tick bắt đầu gần đúng lịch; active latch **126,123 ms**, native `Sarae.latch` **125,889 ms**.
- Bên trong latch có `Contending for pthread mutex` **124,349 ms**, EGL thread ngủ khoảng **123,869 ms**. TikTok `LiveCoreVideoCa` cũng có mutex contention đồng thời; MediaCodec_loop là waker quan sát được sau đó.
- Draw **1,899 ms**, swap **0,804 ms**, previous output logging **0,223 ms** ở chính output này. Vì vậy không quy gap này cho slow draw/swap hoặc previous output logger.
- Native trace không chứa khoảng 3 ms đầu của toàn presentation interval; **chứa trọn tick/latch bị stall**. Không có positive overwrite/drop/data-loss counters.

[Stall evidence/slices/thread states](stall-evidence.json), [trace](auto-run/pipeline.pftrace), [gap và context](auto-run/analysis.json). Không có mutex address/owner hoặc native call stack, nên chưa biết component giữ lock hay cách sửa tối thiểu. Waker không tự chứng minh là lock owner. Không kết luận nguyên nhân sâu là GPU, buffer reallocation hoặc codec bug.

### Stall thứ hai: khác biểu hiện, thiếu native coverage

Final run có gap **161,628 ms**, khoảng 5,972 s sau session start. Tick tới trễ **99,465 ms**, latch gần như không tốn thời gian, swap **32,011 ms**. Sự kiện nằm **trước native trace**, nên chưa biết vì sao tick trễ. Không gộp nó với mutex stall hoặc gap 191,926 ms của Phase 7.

Logger max flush toàn lượt auto **26,595 ms**, toàn lượt cuối **3,393 ms**; các số này đáng theo dõi nhưng không chứng minh logger gây các gap >100 ms đã thấy. Timing logger không bao gồm attribution đầy đủ mọi lock waiter và chính diagnostic row phụ; native stack vẫn thiếu.

**Không áp speculative stall fix.** Giữ decoder/EGL đang hoạt động; không đổi version codec, dùng thread khác để latch shared texture, hoặc rewrite engine. Muốn sửa tiếp cần tái hiện có native stack/lock-owner evidence hoặc đo thêm sub-call quanh buffer acquisition/codec operations. Renderer tick vẫn có thể chặn trong native updateTexImage; giữ last texture không tự bảo đảm present khi chính render thread bị block.

## Ngân sách, timeout và giới hạn bằng chứng

Lượt đầu trước khi người dùng cho phép tiếp: **326,765 s**, vượt 300 s — **FAIL giới hạn thời gian**. Bao gồm preflight 3,005 s, install syntax rejection 8,379 s, block update/calibration 315,380 s kết thúc ở lỗi ADB/UI dump trong lúc kết nối gián đoạn. Harness cũ kiểm tra thời gian trước lệnh nhưng không có per-command host timeout. Không loại block này khỏi journal hoặc gọi nó là thời gian build.

Người dùng sau đó nói **“cho phép test tiếp, vì nãy mất internet”**. Cửa sổ tiếp được ghi riêng: **229,866 s = 3 phút 49,866 s**, dưới 300 s; **10 switch tổng cộng**. Tổng cộng cả hai cửa sổ **556,631 s**, không tuyên bố toàn execution gốc dưới 5 phút. [Journal gốc](device-budget.jsonl), [journal được cấp tiếp](device-budget-resume.jsonl).

Đã bổ sung host-side ADB timeout, remote timeout cho UI dump, command timing journal và cleanup reserve; harness cũ bị vô hiệu hóa để không lặp lỗi. Một resumed UI dump dừng sau 8 s; chuyển sang navigation/pixel evidence và UI dump bounded khi screen đã ổn định. Lệnh dump logcat cuối mỗi verification bị timeout 5 s, được lưu rõ trong [auto command journal](auto-run/adb-commands.jsonl), [final journal](final-run/adb-commands.jsonl). HOME, JSONL/native trace pull và crash-buffer/exit-info đã hoàn tất; không coi partial logcat là full diagnostics.

Đã dừng sau 10 switch. Không endurance, stress hoặc third-party consumer khác. Native trace 128 MiB/35 s không báo data loss; không bao phủ mọi setup/teardown. PNG readback chỉ có trong calibration và bị tắt trong cả hai cadence run; chi phí calibration vẫn tính vào budget.

`analysis.json` ở thư mục gốc là snapshot phân tích trung gian trước lượt cuối; dùng summary và analysis trong từng run làm kết quả chốt. Các ảnh của attempt `calibration-visual` bắt nhầm thời điểm chuyển màn hình feed/camera, không được dùng làm bằng chứng orientation PASS. Bằng chứng LIVE hợp lệ nằm ở `live-calibration`, `auto-run`, `final-run`.

## Handoff và rollback giữ dữ liệu

Bản cuối đang giữ package lab và dữ liệu; render config `auto=true`, override null, FIT, không capture. [Build cuối](build-final.txt), module nguồn/hashes trong [artifact inventory](artifact-inventory.json), tất cả bằng chứng có [SHA-256 manifest](evidence-sha256.json).

Nếu cần rollback module về Phase 7, dùng **partial base update cùng signer**, inherit splits; không dùng script rollback Phase 6 có uninstall. Chưa thực hiện rollback:

```text
adb push .tools/phase6/bridge/base-487-lspatched.apk /data/local/tmp/sarae-rollback-base.apk
adb shell pm install-create -r -p com.ss.android.ugc.trill -S 169323268
adb shell pm install-write -S 169323268 <session-id> base /data/local/tmp/sarae-rollback-base.apk
adb shell pm install-commit <session-id>
```

Offline analysis: export native trace bằng Perfetto trace processor v58.2, rồi chạy `python scripts/analyze_phase8.py` và `python scripts/analyze_phase8.py --run final-run`; exit **1** là cadence FAIL có chủ ý. Không cần thiết bị.

Remaining blockers trước LIVE: cadence toàn foreground chưa đạt; nguyên nhân sâu của native lock/late tick chưa rõ; chưa device-test rear camera, xoay thiết bị khi session đang mở, landscape/rotation-tag input, multi-output, HDR/non-square pixels. Camera metadata được lấy khi tạo session, chưa tự refresh khi display đổi orientation trong cùng session. LIVE encode/audio/uplink/receiver hoàn toàn **NOT TESTED**.
