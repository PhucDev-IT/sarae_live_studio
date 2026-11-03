# Phase 0 summary — run `p0_20261102_2251`

## Environment and one-off timings

```json
{
  "python": "3.12.16",
  "packages": {
    "omnivoice": "0.2.1",
    "torch": "2.8.0+cu128",
    "torchaudio": "2.8.0+cu128",
    "transformers": "5.14.1",
    "huggingface-hub": "1.33.0",
    "gradio_client": "2.7.2",
    "numpy": "2.5.4",
    "soundfile": "0.14.0"
  },
  "checkpoint": "g-group-ai-lab/g-omnivoice",
  "checkpoint_revision": "f2b73211e57db389d81ab817987f90c02e0898ba",
  "asr_revision": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
  "gpu": "NVIDIA GeForce RTX 3060",
  "cuda": "12.8",
  "vram_free_gib_at_start": 10.99,
  "model_load_s": 2.16,
  "vram_after_load_gib": 1.892,
  "asr_load_s": 1.61,
  "vram_after_asr_gib": 3.399,
  "generation_peak_alloc_gib": 3.623,
  "sampling_rate": 24000,
  "checks": {
    "prompt_load_s": 0.0085,
    "prompt_file_tokens_equal": true,
    "prompt_file_text_equal": true,
    "same_seed_repeat_max_abs_diff": 0.0,
    "prompt_file_audio_max_abs_diff": 0.0,
    "asrtxt_tokens_equal": true,
    "asrtxt_text_equal": true
  }
}
```

- prompt `R-CUT`: 6.872 s (ASR=True), tokens [8, 197] ≈ 7.88 s, file 14427 B, ref_text: “trời ơi bán hàng gọi là chơi đẹp lắm. Bây giờ em cũng bảo các chị rồi đã không có giàu nghèo gì 1, 2 chùm ship cả. Thật sự luôn. Có quan trọng khách.”
- prompt `S-ASR`: 2.365 s (ASR=True), tokens [8, 172] ≈ 6.88 s, file 12763 B, ref_text: “Trời ơi bán hàng gọi là chơi đẹp lắm Bây giờ em cũng bảo các chị rồi em không có giàu nghèo gì 1-2 chùm ship cả Thật sự luôn.”
- prompt `S-ASRTXT`: 0.576 s (ASR=False), tokens [8, 172] ≈ 6.88 s, file 12848 B, ref_text: “Trời ơi bán hàng gọi là chơi đẹp lắm Bây giờ em cũng bảo các chị rồi em không có giàu nghèo gì 1-2 chùm ship cả Thật sự luôn.”

Demo upload check: `{"ref_sha256": "25b48ad4888ccd7dbd4e072095318b124e6ae8c5f9d63e63fe90e7071ecc2552", "uploads_seen": 93, "any_identical": true, "distinct_hashes": ["10688c850009721394d74cebbee982642769e3ead31c7a1ccf0ea7618f25ec1b", "170907b79ee90927fcd35d981075d3debdd4e3fc23ee756a7a5571053e28d247", "1d49df80414c6509febd1d73d356cde866eb065ac9cf96f909e9fa3d3290aef0", "21b6efb96a5534f4068f839a2d39de6fb83a77b88c42ae97fb063d07dec92c35", "25b48ad4888ccd7dbd4e072095318b124e6ae8c5f9d63e63fe90e7071ecc2552", "2a720a6ac6d7069d7b6177cae6fadbfb1bc5781ce9d56b6ddaef25bd56a997a5", "2d48c58de4d319b445f02649e2d1d9a0dad135bb62cfad0748eb6785615b6fb3", "37cae63da7400189c6fa0c3cf26d4b731c15019f9e9a8b8928ea51bd799cdbbb", "432729e3d3c801a70776aba15c8f3dbd90a2d53ce7f18c0aa24285f2339c0ea4", "4359ad9778fb78a707cea00da835b3ae2da4b309adc0f9d60b7896d327507c45"]}`

## Per arm (all short items; long reported separately)

| Arm | n | audio s | infer s (lib) / wall s (demo) | RTF warm | WER | head missing | head deleted | tail missing | lead sil ms | trail sil ms | last 100 ms dB | words/voiced s | clipped |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D-ASR | 45 | 3.3 | 3.833 | None | 0.637 | 7/45 | 0/45 | 16/45 | 386.0 | 207.111 | -180.0 | 5.054 | 0 |
| R-CUT | 45 | 3.231 | 15.767 | 4.578 | 0.302 | 24/45 | 7/45 | 18/45 | 242.667 | 206.0 | -180.0 | 4.899 | 0 |
| S-ASR | 45 | 3.302 | 9.23 | 2.564 | 0.237 | 8/45 | 0/45 | 15/45 | 378.889 | 200.667 | -180.0 | 5.024 | 0 |
| S-ASRTXT | 15 | 3.304 | 9.116 | 2.567 | 0.229 | 3/15 | 0/15 | 4/15 | 376.667 | 187.333 | -180.0 | 4.987 | 0 |

## Per category (mean WER · head missing · duration s)

| Category | D-ASR | R-CUT | S-ASR | S-ASRTXT |
|---|---|---|---|---|
| short | 1.58 · 4/12 · 2.914 | 0.165 · 7/12 · 2.859 | 0.108 · 3/12 · 2.917 | 0.111 · 1/4 · 2.92 |
| sales | 0.54 · 1/12 · 3.546 | 0.615 · 8/12 · 3.478 | 0.523 · 1/12 · 3.551 | 0.491 · 0/4 · 3.555 |
| sales_raw | 0.291 · 2/6 · 3.46 | 0.422 · 5/6 · 3.372 | 0.311 · 4/6 · 3.46 | 0.276 · 2/2 · 3.46 |
| punctuation | 0.094 · 0/6 · 4.695 | 0.128 · 3/6 · 4.578 | 0.103 · 0/6 · 4.7 | 0.103 · 0/2 · 4.7 |
| ending | 0.102 · 0/9 · 2.448 | 0.102 · 1/9 · 2.407 | 0.066 · 0/9 · 2.448 | 0.091 · 0/3 · 2.447 |
| long | 0.232 · 0/1 · 50.14 | - | 0.232 · 0/1 · 50.14 | - |

S-ASRTXT vs S-ASR (same seed) max |Δ| per file (int16): [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

## Head/tail misses heard by Whisper (check by ear: ASR itself can be wrong)

- S-ASR s01 r0: head 1, tail 1 — “Dạ, sốt chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- S-ASR s01 r1: head 1, tail 1 — “Dạ sốt chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- S-ASR s01 r2: head 1, tail 1 — “Dạ, xốp chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- S-ASR s04 r1: head 0, tail 1 — “Vâng, anh chờ sốt một chút để em kiểm tra lại sai nha”
- S-ASR p01 r0: head 0, tail 1 — “Áo thun này giá 259.000 còn size SML nha”
- S-ASR p01 r1: head 0, tail 1 — “Áo thun này giá 259.000 còn size SML nha”
- S-ASR p01 r2: head 1, tail 2 — “Áo thôn này giá 259.000 còn size SML nhé.”
- S-ASR p04 r1: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- S-ASR p04 r2: head 0, tail 1 — “Chị cao 1m6, nặng 52kg thì lấy size M nha”
- S-ASR r01 r0: head 1, tail 2 — “Áo thu này giá 259 ban đồng Còn size SML nhé”
- S-ASR r01 r1: head 0, tail 1 — “Áo thun này giá 259 ngàn đồng Còn size SML nha”
- S-ASR r01 r2: head 1, tail 2 — “Áo thu này giá 259 han đồng Còn size SML nhé”
- S-ASR r02 r0: head 1, tail 0 — “Báy này 200k, chị 50kg mặc size M là vừa ạ.”
- S-ASR r02 r1: head 1, tail 0 — “Cái này 200k, chị 50kg mặc sai mờ là vừa ạ”
- S-ASR c02 r0: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh gel Chị thích màu nào thì comment cho Sốp nha”
- S-ASR c02 r1: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh rêu Chị thích màu nào thì comment cho xốp nha”
- S-ASR c02 r2: head 0, tail 1 — “Màu trắng, màu đen, màu bê và màu xanh gel Chị thích màu nào thì comment cho sốt nha”
- S-ASRTXT s01 r0: head 1, tail 1 — “Dạ, sốt chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- S-ASRTXT p01 r0: head 0, tail 1 — “Áo thun này giá 259.000 còn size SML nha”
- S-ASRTXT r01 r0: head 1, tail 2 — “Áo thu này giá 259 ban đồng Còn size SML nhé”
- S-ASRTXT r02 r0: head 1, tail 0 — “Báy này 200k, chị 50kg mặc size M là vừa ạ.”
- S-ASRTXT c02 r0: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh gel Chị thích màu nào thì comment cho Sốp nha”
- R-CUT s01 r0: head 1, tail 1 — “Dạ, sốt chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- R-CUT s01 r1: head 1, tail 1 — “Dạ, sắp chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- R-CUT s01 r2: head 1, tail 1 — “Dạ, xốp chào cả nhà, cảm ơn mọi người đã vào xem like nha”
- R-CUT s02 r0: head 1, tail 0 — “Cái này đang còn hàng, chị đặt ngay để được giao sớm nhé”
- R-CUT s02 r1: head 1, tail 0 — “Lúc này đang còn hàng, chị đặt ngay để được giao sớm nhé.”
- R-CUT s02 r2: head 1, tail 0 — “Cũng này đang còn hàng, chị đặt ngay để được giao sớm nhé”
- R-CUT s03 r1: head 1, tail 0 — “Một tàn nhà thả tim giúp sốt một cái ạ.”
- R-CUT s04 r0: head 0, tail 2 — “Vâng anh chờ sốt một chút để em kiểm tra lại sại sai nhá”
- R-CUT s04 r1: head 0, tail 1 — “Vâng, anh chờ sốt một chút để em kiểm tra lại sai nha”
- R-CUT p01 r0: head 2, tail 1 — “Khu này giá 259.000, còn size SML nha.”
- R-CUT p01 r1: head 2, tail 2 — “Cái vun này giá 259.000 con size SML nhé.”
- R-CUT p01 r2: head 2, tail 2 — “Công này giá 259.000 còn size SML nhé.”
- R-CUT p02 r0: head 1, tail 0 — “Báy này 200.000, chị 50kg mặc sai mờ là vừa ạ.”
- R-CUT p02 r2: head 1, tail 0 — “Báy này 200 nghìn, chị 50 ký mặc xe mờ là vừa ạ”
- R-CUT p03 r0: head 2, tail 1 — “Các bạn hãy đăng ký kênh để ủng hộ kênh của mình nhé!”
- R-CUT p03 r1: head 1, tail 0 — “Nguyễn phí ship đơn từ 99.000 giao 2 đến 3 ngày nhé”
- R-CUT p04 r0: head 0, tail 1 — “Cô chị cao 1m6 nặng 52kg thì lấy size M nha”
- R-CUT p04 r1: head 1, tail 1 — “Mũi cao 1m6, mặc 52kg thì lấy size M nha”
- R-CUT p04 r2: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- R-CUT r01 r0: head 2, tail 1 — “Ngày giá 259 hàng đồng Còn size SML nha”
- R-CUT r01 r1: head 2, tail 2 — “Câu này giá 259.000 đồng Còn sai SML nhé”
- R-CUT r01 r2: head 2, tail 2 — “Các bạn hãy đăng ký kênh để ủng hộ kênh của mình nhé.”
- R-CUT r02 r1: head 1, tail 0 — “Máy này 200k chị 50kg mặc sai mò là vừa ạ”
- R-CUT r02 r2: head 1, tail 0 — “Pháy này 200k, chị 50k mặc size M là vừa ạ”
- R-CUT c01 r1: head 2, tail 0 — “Phải cô tôn mềm mát, thấm hút mồ hôi tốt, mặc đi làm, đi chơi hay ở nhà đều đẹp.”
- R-CUT c01 r2: head 2, tail 0 — “Tuổi bô tôn mềm mát thấm hút mồ hôi tốt Mặc đi làm đi chơi hay ở nhà đều đẹp”
- R-CUT c02 r0: head 0, tail 1 — “Màu trắng, màu đen, màu bê, màu xanh rêu Chị thích màu nào thì comment cho xốp nha”
- R-CUT c02 r1: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh gel Chị thích màu nào thì comment cho sốt nha”
- R-CUT c02 r2: head 1, tail 1 — “Trắng màu đen, màu be và màu xanh rêu Chị thích màu nào thì comment cho xốp nha”
- R-CUT e02 r0: head 2, tail 0 — “Đủ size rồi đó nha”
- D-ASR s01 r0: head 1, tail 1 — “Dạ, xốp chào cả nhà. Cảm ơn mọi người đã vào xem like nha.”
- D-ASR s01 r1: head 1, tail 1 — “Dạ, xốp chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- D-ASR s01 r2: head 1, tail 1 — “Dạ sốt chào cả nhà Cảm ơn mọi người đã vào xem like nha”
- D-ASR s03 r2: head 2, tail 2 — “Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn Cảm ơn các bạn đã theo dõi và hãy subscribe cho kênh Ghiền Mì Gõ Để không bỏ lỡ những video hấp dẫn”
- D-ASR s04 r0: head 0, tail 1 — “Vâng, anh chờ sốt một chút để em kiểm tra lại sai nha”
- D-ASR p01 r0: head 0, tail 2 — “Áo thun này giá 259.000 còn size SML nhé”
- D-ASR p01 r1: head 1, tail 1 — “Áo thôn này giá 259.000, còn size SML nha.”
- D-ASR p01 r2: head 0, tail 1 — “Áo thun này giá 259.000 của size SML nha”
- D-ASR p04 r0: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- D-ASR p04 r1: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- D-ASR p04 r2: head 0, tail 1 — “Chị cao 1m6 nặng 52kg thì lấy size M nha”
- D-ASR r01 r0: head 1, tail 2 — “Áo thu này giá 259 nghìn đồng Còn size SML nhé”
- D-ASR r01 r1: head 0, tail 2 — “Áo thun này giá 259 men đồng Còn xe SML nhé”
- D-ASR r01 r2: head 1, tail 1 — “Áo thôn này giá 259 sang đồng, có size SML nha”
- D-ASR c02 r1: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh gel Chị thích màu nào thì comment cho Sốp nha”
- D-ASR c02 r2: head 0, tail 1 — “Màu trắng, màu đen, màu be và màu xanh, gel Chị thích màu nào thì comment cho sốt nha”
