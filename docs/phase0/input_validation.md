# Phase 0.2 — Kiểm tra video người mẫu (video thật của người dùng)

> 2026-11-04. Nguồn: 3 file `.MOV` người dùng cung cấp (iPhone, HEVC Main 8-bit SDR BT.709, 1920×1080 + cờ xoay 90° → hiển thị dọc 1080×1920, ~29,997 fps VFR nhẹ, có audio AAC môi trường — **không dùng**). Bản gốc giữ nguyên ở `inputs/phase0/video_{a,b,c}.mov` (không commit).
> Công cụ: `phase0/tools/validate_video.py` (YuNet, CPU). Báo cáo chi tiết + CSV từng frame: `outputs/phase0/validation/`.

## 1. Chuẩn hóa dùng chung cho mọi engine

| Clip | Nội dung (xem trực tiếp) | Cắt | Lý do cắt | File chuẩn hóa | Frame |
|---|---|---|---|---|---|
| A — Static | đứng thẳng toàn thân, nhìn camera, gần như không cử động | 0,5 → 16,8 s | giây ~17–18 bước tới tắt máy (mất đầu khỏi khung) | `inputs/phase0/norm/video_a_25fps.mp4` | 408 |
| B — Natural motion | ngồi ghế, khung nửa người, cử chỉ tay liên tục, quay đầu nhẹ | 1,0 → 17,8 s | giây 0 và 18–20 cúi sát camera bật/tắt quay | `inputs/phase0/norm/video_b_25fps.mp4` | 420 |
| C — Full sample | nhiều đoạn: đứng toàn thân + cử chỉ (0–23 s), quay đầu gần profile (15–18 s), tiến lại gần (24–35 s), lùi xa (36–44 s), ngồi sát camera (52–60 s) | 0 → 60,3 s | khung cuối với tay tắt máy | `inputs/phase0/norm/video_c_25fps.mp4` | 1508 |

Lệnh: `ffmpeg -ss S -to E -i in.mov -an -vf "fps=25,format=yuv420p" -c:v libx264 -preset slow -crf 14 -profile:v high` (xoay được ffmpeg áp dụng khi decode → file ra không còn cờ xoay). **Không** crop, không chỉnh màu, không làm nét.

## 2. Kết quả (trên bản chuẩn hóa)

| Chỉ số | A | B | C | Ghi chú |
|---|---|---|---|---|
| Verdict công cụ | WARN | WARN | **FAIL** | |
| Có mặt (presence) | 100% | 100% | 100% | |
| Track break (hộp mặt nhảy) | 0 | 0 | 0 | |
| Chiều cao mặt (median, px) | ~240 | ~300 | ~245 (173–580) | crop của engine 256 px → A/C sát ngưỡng, bị upscale nhẹ |
| Head jitter p95 (/độ rộng mặt/frame) | PASS | PASS | 0,077 WARN | |
| Yaw p95 (quay đầu) | 0,04 | 0,45 WARN | **0,57 FAIL** | C có đoạn gần profile 15–18 s, 23 s, 25 s |
| Roll p95 (nghiêng đầu) | PASS | PASS | 15,7° WARN | |
| Thay đổi kích thước mặt p95 | PASS | 0,032 WARN | 0,042 WARN | B/C tiến–lùi camera |
| Độ nét mặt (Laplacian, median) | 30 WARN | 58 WARN | 29 WARN | mặt nhỏ trong khung toàn thân, ánh sáng trong nhà |
| Che miệng (heuristic) | PASS | PASS | PASS | |

Diễn giải:
- **B là clip phù hợp nhất cho livestream bán hàng** (nửa người, mặt to nhất, nét nhất). A sạch nhất cho lip-sync nhưng mặt nhỏ và mờ. C là bài stress test chuyển động, đoạn quay đầu/tiến-lùi dự kiến gây lỗi vùng miệng — đó chính là điều cần quan sát, không loại bỏ.
- Tất cả clip đều **im miệng** (không nói) — đúng yêu cầu đề bài.
- Kết quả ở đây chỉ là dự báo; quyết định cuối cùng là preprocessing thật của engine và mắt người xem.

## 3. Sửa công cụ sau khi soi video thật

Lần chạy đầu báo B có 12 và C có 6 "track break", 27–32 frame "nhiều mặt". Xem trực tiếp frame bị đánh dấu: YuNet nhận **bàn tay đang cử chỉ** là khuôn mặt (score 0,6–0,8), và tracker chọn hộp *lớn nhất* nên nhảy sang tay. Đã sửa: chỉ đếm hộp có score ≥ 0,85, và bám theo hộp có IoU cao nhất với frame trước. Sau sửa: track break = 0 ở cả 3 clip. Đây là sửa lỗi đo lường, không phải nới ngưỡng để video qua.

## 4. Khuyến nghị quay lại (nếu cần cải thiện chất lượng)

| Vấn đề | Đề xuất |
|---|---|
| Mặt nhỏ, mờ ở khung toàn thân | Khung **nửa người** như B (mặt ≥ 300 px), thêm đèn chiếu mặt |
| Bước tới bật/tắt máy | Dùng remote/hẹn giờ, hoặc chừa 2 s đầu/cuối đứng yên để cắt |
| Quay đầu gần profile | Giữ yaw nhỏ (nhìn lệch ≤ ~30°); quay đầu mạnh nên tách thành clip "hành động" riêng không lip-sync |
| Tiến–lùi camera trong một clip | Tách thành các clip cùng khoảng cách → dễ ghép vòng lặp |
