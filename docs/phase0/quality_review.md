# Phase 0.6 — Đánh giá chất lượng hình

> 2026-11-04. **Trạng thái: `PENDING_USER_REVIEW`.** Mục 3 là quan sát sơ bộ của Claude trên ảnh tĩnh — **không phải kết luận**. Quyết định chất lượng thuộc về người dùng xem video chạy (mục 4).
> Không có postprocessing, frame interpolation hay làm nét trên bất kỳ output nào; video so sánh chỉ crop/scale và dùng chung một track audio.

## 1. File cần xem

| File | Nội dung | Ghi chú |
|---|---|---|
| `outputs/phase0/comparison_b_q_main_face.mp4` | **3 engine cạnh nhau, phóng to mặt**, cùng audio `q_main` | xem đầu tiên |
| `outputs/phase0/comparison_b_q_main_full.mp4` | 3 engine cạnh nhau, toàn khung | chuyển động cơ thể, viền dán mặt |
| `outputs/phase0/livetalking_wav2lip256.mp4` | LT-W2L256 **realtime thật** (đường idle thật giữa các câu, có trễ ~1 s đầu câu) | đúng như khi livestream |
| `outputs/phase0/musetalk15.mp4` | MuseTalk 1.5 chính thức (script gốc) | |
| `outputs/phase0/runs/lt_w2l256_{a,c}_q_main.mp4` | LT-W2L256 trên clip A (đứng yên) và C (stress test chuyển động) | |
| `outputs/phase0/runs/lt_mt15h_b_q_main.mp4` | LT-MuseTalk realtime thật — **có đứng hình/trễ dồn** (không đạt realtime) | để thấy hậu quả khi không đủ FPS |

Video so sánh dùng bản render "whole-stream" của LiveTalking (`--offline --whole`: đưa nguyên file `q_main.wav` vào một luồng, giống cách MuseTalk chính thức xử lý — model chạy cả trong đoạn im lặng), cắt bù 0,88 s trễ cố định để khớp audio. Mỗi engine bắt đầu chu kỳ video gốc ở frame khác nhau nên **tư thế cơ thể cùng thời điểm có thể khác nhau** — không phải lỗi chất lượng.

## 2. Phương pháp quan sát sơ bộ

- Lưới ảnh vùng miệng 300×220 px ở độ phân giải gốc, cùng mốc audio, 2 mốc im lặng (7,8 s; 19,0 s) + 4 mốc đang nói.
- Dải frame quanh chuyển nói→im và im→nói của bản realtime LT-W2L256.
- Không có chỉ số đồng bộ tự động (SyncNet/LSE) — checkpoint syncnet không được tải (chỉ dùng cho train, 1,4 GB). Có thể bổ sung nếu cần số khách quan.

## 3. Quan sát sơ bộ (Claude, ảnh tĩnh — cần xác nhận bằng mắt người)

| Tiêu chí | LT-W2L256 | LT-MT15H | MT15 |
|---|---|---|---|
| Danh tính khuôn mặt | giữ nguyên (chỉ nửa dưới mặt bị thay) | giữ nguyên | giữ nguyên |
| Độ nét vùng miệng | mềm/nhòe hơn phần mặt gốc; lòng miệng tối, môi dưới đôi khi mờ | môi rõ, màu tự nhiên, thấy răng | môi rõ, màu tự nhiên |
| Khẩu hình khi nói | có mở/đóng theo âm; biên độ hợp lý | mở/đóng rõ, hình môi tròn khi nguyên âm tròn | tương tự LT-MT15H |
| Im lặng (khi model chạy trên đoạn im) | miệng khép gọn | **môi hé, có lúc lộ răng** | hé nhẹ ở một mốc, khép ở mốc khác |
| Im lặng ở đường idle thật của LiveTalking | hiện frame gốc → miệng khép tự nhiên | (như W2L — cùng cơ chế) | không có cơ chế idle |
| Chuyển nói → im (realtime LT-W2L256) | miệng khép dần 3–4 frame rồi sang frame gốc; không thấy nhảy vị trí; **độ nét môi đổi nhẹ** (sinh ra mềm hơn gốc) | — | — |
| Méo môi/răng/cằm | không thấy méo nặng trong mẫu xem | không thấy | không thấy |
| Chuyển động cơ thể | giữ nguyên video gốc (ping-pong) | giữ nguyên | giữ nguyên (ping-pong) |
| Watermark | chữ "LiveTalking" nhỏ góc trên trái (code upstream) | có | không |

## 4. Phiếu đánh giá của người dùng (điền sau khi xem)

Thang 1–5 (5 = như người thật).

| Tiêu chí | LT-W2L256 | LT-MT15H | MT15 | Ghi chú |
|---|---|---|---|---|
| Face identity | | | | |
| Lip-sync (khớp tiếng Việt) | | | | |
| Facial artifacts (môi/răng/cằm) | | | | |
| Motion continuity | | | | |
| Video looping (nhảy tư thế khi lặp) | | | | |
| Silence handling | | | | |
| A/V consistency | | | | |
| **Chấp nhận được cho livestream?** (Có/Không) | | | | |

## 5. Những điều chưa đánh giá

- Clip C (stress test) và clip A mới chạy với LT-W2L256; MuseTalk mới chạy clip B (chuẩn bị avatar MuseTalk cho C ước tính 30–40 phút/engine).
- Chưa có chỉ số sync khách quan (LSE-C/LSE-D).
