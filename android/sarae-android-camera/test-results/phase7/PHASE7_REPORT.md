# Phase 7 — TikTok camera pipeline validation, 23/10/2026

## Kết quả

**FAIL ngưỡng output gap `<100 ms`:** playback ngoài cửa sổ switch có max **71,237 ms**, nhưng switching có một gap **191,926 ms**. Lượt trace sau max **47,712 ms** không xóa kết quả FAIL của lượt trước. Không thay threshold, không tuyên bố đã sửa cadence.

**PASS chức năng:** TikTok lab thật 47.4.3 nhận MP4 cảnh thật; 10/10 source switch được renderer commit đúng nguồn, giữ nguyên session/Surface trong mỗi lượt 5 switch; không output PTS regression hoặc fatal Java/native/pipeline error quan sát được. Có ảnh và motion samples của preview TikTok, không dùng mock consumer.

**Root cause chưa xác định chắc chắn.** System trace đầu bị ghi đè đúng đoạn gap 191,926 ms. Trace thứ hai đủ dữ liệu nhưng không tái hiện gap >100 ms. Không đủ bằng chứng quy lỗi cho GC, GPU/EGL, scheduling hoặc log I/O. Không sửa engine theo suy đoán; APK/module/engine giữ nguyên.

Video thật còn lộ **sai hướng xoay khoảng 90°** trong preview, chưa sửa. Vì vậy không coi pipeline tổng thể đã đạt chất lượng hoàn chỉnh.

## Giữ nguyên môi trường

- Branch `feature/tools-android`, SM-G996U/API 34/non-root; `com.ss.android.ugc.trill` 47.4.3, process **4022**.
- Không uninstall, clear data, reinstall/patch, force-stop, build module/consumer hoặc thay code decoder/EGL. Hash source/module đối chiếu Phase 6 không đổi; package path/version/signature/update metadata trước/sau bằng nhau. [Trước](package-before.txt), [sau lượt cuối](trace-complete/package-after.txt), [hash checks](summary.json).
- Không login/logout hoặc sửa account settings. Chỉ mở camera preview, chọn mode LIVE và gửi broadcast đổi test media. Nút **“Phát LIVE” không được nhấn**; không record/post/broadcast.
- Chỉ hai file **test** A.mp4/B.mp4 do Phase 6 tạo được thay bằng footage; không phải account data. Bản cũ được sao lưu tại `.tools/phase7/previous-A.mp4`, `previous-B.mp4`. Hai footage thật vẫn ở test paths sau test; preview đã đóng bằng HOME. Bản lab và đăng nhập hiện tại được giữ nguyên.

## MP4 thật và bằng chứng chuyển động

Nguồn có sẵn trong `backend/data/media`, không download sample hoặc tạo engine khác:

| Alias test | File nguồn | Video | Duration |
| --- | --- | --- | ---: |
| A | `0275d431bc3c4d2b91ed0c54d54f0b78.mp4` | H.264, 576×1024, average 30 FPS | 11,000 s |
| B | `47ca398498084dcbb510b69adae93db2.mp4` | H.264, 720×1280, average ≈25,167 FPS | 16,967 s |

MP4 có audio track, nhưng engine hiện có chỉ chọn video; không thêm audio functionality. Full hashes/ffprobe thông tin nằm trong [summary](summary.json).

Ảnh actual TikTok: [A trước lượt đầu](trace-run/A-before-trace.png), [B sau 5 switch](trace-run/B-after-trace-1.png), [A sau lượt tiếp](trace-complete/A-after-trace-1.png). Đã xem ảnh để xác nhận cảnh khớp footage và thấy sai rotation; không suy ra orientation từ pattern đỏ/xanh.

Hai screenshot cuối mỗi lượt lấy cùng nguồn đang chạy. ROI `[300,500,780,1200]`, 3.360 sample: thay đổi >30 mức tổng RGB ở **81,99%** sample B và **69,97%** sample A; mean absolute channel difference **49,79** và **54,26**. [Motion evidence](motion-evidence.json). Đây là bằng chứng video có chuyển động trong preview; không phải đo từng frame compositor. Pixel tối trong tóc/quần/scene không được đếm thành black frame.

## Protocol và phạm vi continuity

Mỗi lượt có một session LIVE preview Camera2 `SessionConfiguration`, một output, EGL **1920×1080**, target 30 FPS. Đo playback yên, 5 switch-now, rồi playback tiếp; không screenshot trong đoạn quiet/switch của system trace. Screenshot lấy trước/sau trace và vẫn tính trong full-session gap metrics.

- Lượt 1: quiet 10 s, B→A→B→A→B, chờ 3 s mỗi switch; trace 45 s/32 MiB.
- Lượt 2: quiet 8 s, A→B→A→B→A, chờ 2 s mỗi switch; trace 30 s/128 MiB để giữ đủ dữ liệu.
- Preview được mở lại **giữa các lượt**, không trong chuỗi 5 switch. Process TikTok không đổi. Không tuyên bố một session duy nhất cho toàn Phase 7.
- Switching window là request→renderer commit **+1 giây** để gồm first presentation sau commit. Output interval chạm cửa sổ đó được phân loại switching; phần còn lại của active session là normal. Full-session max vẫn được báo độc lập.

## Số đo

| | Lượt 1 | Lượt 2 |
| --- | ---: | ---: |
| Active session | 60,145 s | 44,827 s |
| Session generation trong cumulative PID trace | 9 | 12 |
| Surface gốc trong lượt | 138792642 | 4876027 |
| Session / EGL trong chuỗi switch | 1 / 1 | 1 / 1 |
| Renderer switch commit đúng nguồn | 5/5 | 5/5 |
| Output frames | 1.796 | 1.339 |
| Output cadence từ các gap | ≈29,923 FPS | ≈30,014 FPS |
| Normal max gap | **71,237 ms** | **47,712 ms** |
| Switching max gap | **191,926 ms — FAIL** | **45,525 ms** |
| Output PTS không tăng | 0 | 0 |
| Decoder frame events | 1.570 | 1.213 |
| SurfaceTexture arrival events | 1.514 | 1.143 |
| Source callback coalesced events | 0 | 0 |
| MP4 loop events | 2 | 1 |
| Renderer repeated / skipped tick counters cuối lượt | 292 / 5 | 207 / 0 |

Request→renderer commit, theo thứ tự switch:

- Lượt 1: **201,689 / 293,063 / 153,080 / 151,287 / 161,357 ms**.
- Lượt 2: **181,279 / 140,781 / 155,156 / 146,917 / 143,433 ms**.

Đây không phải display latency đầu cuối. Commit xảy ra sau first valid frame readiness, không đồng nghĩa pixel đã tới màn hình tại đúng timestamp đó. Mỗi selection có ≥67 output sample và ≥38 source texture timestamps khác nhau trong trace. 10/10 là kết quả renderer/source routing; screenshot trước/sau và motion sample xác nhận actual preview, không chứng minh pixel của từng intermediate switch hoặc zero transient black frames.

[Analysis lượt 1](trace-run/analysis.json), [lượt 2](trace-complete/analysis.json), [JSONL session 1](trace-run/active-session.jsonl), [session 2](trace-complete/active-session.jsonl). Source media PTS/SurfaceTexture timestamps được giữ; reset lúc loop là expected. Output PTS được kiểm tra riêng, không áp yêu cầu monotonic media PTS xuyên loop.

## Điều tra nguyên nhân

### Đã xác nhận

Gap 191,926 ms xảy ra ở **switch thứ hai B→A của lượt 1**. Timing trong [gap context](trace-run/analysis.json):

1. Last old-source presentation ở khoảng 191,96 ms trước presentation tiếp theo.
2. Pending decoder `output_format` event ở khoảng 176,81 ms trước presentation đó.
3. New source arrival/latch, switch commit, rồi presentation; commit cách presentation khoảng 14,92 ms.

`switch_request`→`preload_start` của lượt này chỉ **1,237 ms** tại switch gây gap; cả năm lần setup chỉ **1,237–3,996 ms**. Do đó khoảng setup đồng bộ ~128 ms từng thấy trong Phase 6 **không giải thích được gap Phase 7 này**. Không gộp các gap khác nhau thành một lỗi đã biết.

### Vì sao chưa quy được sub-call hoặc hệ thống

- Trace 32 MiB ghi đè **62.758.912 byte**, giữ scheduling khoảng 18 s cuối; đoạn 191,926 ms nằm ngoài coverage. JSONL còn nguyên, nhưng CPU/thread-state/GPU/GC trace tại thời điểm đó đã mất. [Trace nguyên bản](trace-run/pipeline.pftrace), [stats và coverage](trace-run/analysis.json).
- Trace 128 MiB có **0 overrun/overwritten/dropped/data-loss counter >0** và giữ đủ khoảng 30 s; không có gap >100 ms. [Trace đầy đủ](trace-complete/pipeline.pftrace).
- Trong trace đầy đủ, EGL thread `eglSwapBuffers` max **13,354 ms**, `queueBuffer` max **12,780 ms**; app suspend slices lớn nhất `FullSuspendCheck` khoảng **1,767 ms**. Những số này không chứng minh điều gì ở đoạn đã mất của lượt 1.
- Concurrent GC duration không phải stop-the-world pause duration; không kết luận “GC gây 192 ms” chỉ từ một GC dài ở thread khác.
- Logger hiện ghi synchronous trên EGL thread. Timestamp row là thời điểm event được xử lý ở logger; timestamp emit gốc trên decoder worker **không được giữ riêng**. Không phân biệt đầy đủ decoder delay, callback queue delay, log write stall hoặc renderer scheduling bằng JSONL hiện tại.
- Code lặp lại texture cũ khi chưa có source mới. Chậm có frame mới một mình không đủ giải thích vì sao output producer không present trong 191,9 ms; cần timing bên trong tick/latch/draw/swap/log và scheduler đúng lúc sự cố.

**Không áp dụng fix engine:** component gây stall chưa được chứng minh. Không rebuild/patch/reinstall để lén bổ sung instrumentation. Các thay đổi chỉ thuộc harness/analyzer: config qua stdin, buffer đủ lớn và chọn tab theo UI thực tế.

## Ngân sách và các lượt dừng sớm

Tổng thiết bị **153,203 giây = 2 phút 33,203 giây**, 10 switch thực thi; sau đó dừng. [Budget journal](device-budget.jsonl). Tất cả block tương tác đều HOME trong cleanup; thời gian offline giữa block không giữ preview mở. Không endurance hoặc unrelated regression.

Hai lần dừng trước switch được giữ bằng chứng và tính thời gian:

1. **9,840 s**, Perfetto không đọc được config tại `/data/local/tmp/...` (errno 13). Chuyển sang stdin `-c -`, không chmod, root hoặc đổi SELinux. [Run record](run.json).
2. **10,122 s**, TikTok restore tab khác vị trí cũ; UI guard không thấy nút Phát LIVE và dừng. Sau đó harness đọc bounds của đúng tab `LIVE` thay tọa độ cố định. [Run record](trace-full/run.json). File trace được kéo trong cleanup của attempt lỗi là bản **stale** từ lượt trước, được đánh dấu và **không dùng để phân tích**; harness đã sửa chỉ pull khi trace thật sự start.

Native tool dùng offline: Perfetto trace processor **v58.2**, SHA-256 `adfa6bad3d72be3ba9b83fa2b17b69fa13b3ab1cad0f42e52b86188bd5f0f997`, tải theo manifest wrapper chính thức. [Android tracing](https://perfetto.dev/docs/quickstart/android-tracing), [Trace Processor](https://perfetto.dev/docs/analysis/trace-processor). Config từng lượt được lưu [32 MiB/45 s](trace-run/trace-config.pbtxt), [128 MiB/30 s](trace-complete/trace-config.pbtxt).

Không fatal Java/native hoặc pipeline error được ghi trong các lượt thu. [Log lượt 1](trace-run/relevant-logcat.txt), [lượt 2](trace-complete/relevant-logcat.txt), [crash buffer](trace-complete/crash-buffer.txt), [exit-info](trace-complete/exit-info.txt). Đây là phạm vi quan sát ngắn, không đảm bảo dài hạn; native private tombstones không đọc được bằng quyền app thường.

## Phân tích lại và bước tiếp theo

Không cần thiết bị để chạy lại:

```powershell
cd D:\Android_Programming\sarae_tool_live_studio\android\sarae-android-camera
./scripts/sample_phase7_motion.ps1
python scripts/analyze_phase7.py
```

Analyzer trả **exit 1 có chủ ý** vì vẫn còn gap >100 ms. [Summary](summary.json), [analysis output](analysis-output.txt), [artifact inventory](artifact-inventory.json), [evidence hashes](evidence-sha256.json). SQLite export dùng phân tích giữ tại `.tools/phase7/`; native trace gốc không bị thay. Báo cáo và đối soát bằng chứng hoàn tất ngày 24/10/2026; không chạy thêm trên thiết bị khi hoàn tất báo cáo.

Nếu tiếp tục ở execution mới: cần trace không mất dữ liệu đúng lúc tái hiện, hoặc timing instrumentation ở scheduled/start tick, slot construction, latch/updateTexImage, draw/swap, log write/flush và decoder emit/handler receipt. Instrumented embedded module cần update/repack TikTok; **chưa được ủy quyền trong Phase 7**, nên chưa thực hiện. Sai rotation cần đối chiếu transform camera/source riêng, không hardcode 90° cho mọi mode/lens.

Hiện không có bằng chứng đã tìm được hoặc sửa root cause. Kết quả đúng là **normal playback PASS trong mẫu; switching cadence FAIL; root cause còn UNKNOWN**, cùng giới hạn rotation đã được nhìn thấy. LIVE broadcasting/audio/encoding/receiver vẫn chưa được kiểm chứng.
