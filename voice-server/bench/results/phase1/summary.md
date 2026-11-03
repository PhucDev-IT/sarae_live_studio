# Phase 0 summary — run `p1_http_20261103_0832`

## Per arm (all short items; long reported separately)

| Arm | n | audio s | infer s (lib) / wall s (demo) | RTF warm | WER | head missing | head deleted | tail missing | lead sil ms | trail sil ms | last 100 ms dB | words/voiced s | clipped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HTTP | 45 | 3.302 | None | None | 0.237 | 8/45 | 0/45 | 15/45 | 378.889 | 200.667 | -180.0 | 5.024 | 0 |

## Per category (mean WER · head missing · duration s)

| Category | HTTP |
|---|---|
| short | 0.108 · 3/12 · 2.917 |
| sales | 0.523 · 1/12 · 3.551 |
| sales_raw | 0.311 · 4/6 · 3.46 |
| punctuation | 0.103 · 0/6 · 4.7 |
| ending | 0.066 · 0/9 · 2.448 |
| long | - |

## Head/tail misses heard by Whisper (check by ear: ASR itself can be wrong)

- HTTP s01 r0: head 1, tail 1 — “Dạ, sốt chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- HTTP s01 r1: head 1, tail 1 — “Dạ sốt chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- HTTP s01 r2: head 1, tail 1 — “Dạ, xốp chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- HTTP s04 r1: head 0, tail 1 — “Vâng, anh chờ sốt một chút để em kiểm tra lại sai nha”
- HTTP p01 r0: head 0, tail 1 — “Áo thun này giá 259.000 còn size SML nha”
- HTTP p01 r1: head 0, tail 1 — “Áo thun này giá 259.000 còn size SML nha”
- HTTP p01 r2: head 1, tail 2 — “Áo thôn này giá 259.000 còn size SML nhé.”
- HTTP p04 r1: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- HTTP p04 r2: head 0, tail 1 — “Chị cao 1m6, nặng 52kg thì lấy size M nha”
- HTTP r01 r0: head 1, tail 2 — “Áo thu này giá 259 ban đồng Còn size SML nhé”
- HTTP r01 r1: head 0, tail 1 — “Áo thun này giá 259 ngàn đồng Còn size SML nha”
- HTTP r01 r2: head 1, tail 2 — “Áo thu này giá 259 han đồng Còn size SML nhé”
- HTTP r02 r0: head 1, tail 0 — “Báy này 200k, chị 50kg mặc size M là vừa ạ.”
- HTTP r02 r1: head 1, tail 0 — “Cái này 200k, chị 50kg mặc sai mờ là vừa ạ”
- HTTP c02 r0: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh gel Chị thích màu nào thì comment cho Sốp nha”
- HTTP c02 r1: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh rêu Chị thích màu nào thì comment cho xốp nha”
- HTTP c02 r2: head 0, tail 1 — “Màu trắng, màu đen, màu bê và màu xanh gel Chị thích màu nào thì comment cho sốt nha”
