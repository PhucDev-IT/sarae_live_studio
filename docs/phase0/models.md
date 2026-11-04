# Phase 0 — Model đã tải (manifest)

> Tải ngày 2026-11-04 từ `huggingface.co` (không dùng `hf-mirror.com`), chọn lọc từng file, không dùng `download_weights.bat` chính thức (script đó tải toàn bộ repo). Nơi lưu: `F:\Coder\phase0_vendor\models\` (ngoài monorepo). Tổng **4,2 GB**.
> SHA-256 đã đối chiếu với LFS oid trên HuggingFace: ✅ khớp toàn bộ file LFS.

| File | Repo / nguồn | Revision | Byte | SHA-256 | Đối chiếu | License (card) |
|---|---|---|---|---|---|---|
| `musetalkV15/unet.pth` | HF `TMElyralab/MuseTalk` | `2bcb936e2fdd` | 3 400 074 924 | `7ebf6c98c181e208…` | ✅ LFS oid | MIT |
| `musetalkV15/musetalk.json` | HF `TMElyralab/MuseTalk` | `2bcb936e2fdd` | 748 | `5b6923aee04d7169…` | (non-LFS) | MIT |
| `sd-vae/diffusion_pytorch_model.bin` + `config.json` | HF `stabilityai/sd-vae-ft-mse` | `31f26fdeee13` | 334 707 217 | `1b4889b6b1d4ce7a…` | ✅ LFS oid | MIT |
| `whisper/pytorch_model.bin` + `config.json`, `preprocessor_config.json` | HF `openai/whisper-tiny` | `169d4a4341b3` | 151 095 027 | `9607f98a2b22d9e2…` | ✅ LFS oid | Apache-2.0 |
| `dwpose/dw-ll_ucoco_384.pth` | HF `yzd-v/DWPose` | `1a7144101628` | 406 878 486 | `0d9408b13cd863c4…` | ✅ LFS oid | Apache-2.0 (card) ⚠️ dữ liệu train COCO-WholeBody/UBody |
| `face-parse-bisent/79999_iter.pth` | HF `ManyOtherFunctions/face-parse-bisent` (bản re-upload mà `download_weights.bat` chính thức dùng) | `0073b233a5a3` | 53 289 463 | `468e13ca13a9b43c…` | ✅ LFS oid | WTFPL (card) ⚠️ train trên CelebAMask-HQ — phi thương mại |
| `face-parse-bisent/resnet18-5c106cde.pth` | `download.pytorch.org` | — | 46 827 520 | `5c106cde386e87d4…` | ✅ tiền tố tên file | BSD-3 (torchvision) |
| `s3fd/s3fd-619a316812.pth` | `adrianbulat.com` (URL nạp sẵn trong `sfd_detector.py`) | — | 89 843 225 | `619a31681264d3f7…` | ✅ tiền tố tên file | BSD-3 (face-alignment) |

| `wav2lip256.pth` | Người dùng tự tải từ Google Drive/Quark của LiveTalking (README:105-110), 2026-11-04 14:31 | — | 214 670 409 | `b22d7ac86295df66…` | ⚠️ không có hash công bố để đối chiếu; định dạng torch zip hợp lệ (382 entry) | **không ghi** |

**Chưa tải (cố ý):** `musetalk/pytorch_model.bin` (v1.0), `syncnet/latentsync_syncnet.pt` (chỉ dùng khi train).

Sự cố khi tải: lần đầu `unet.pth` và `sd-vae` lỗi trên CDN Xet của HF (`getaddrinfo failed`); tải lại với `HF_HUB_DISABLE_XET=1` và `--revision` pin cứng → thành công, hash khớp.
