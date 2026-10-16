# Product LIVE Video Setup

## Update
```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
```

## Usage
1. Upload hoặc Link Local video tại **Thư viện video**.
2. Từ Thư viện video, chọn **Gắn video cho sản phẩm** (hoặc sidebar **Video theo sản phẩm**).
3. Chọn sản phẩm, thêm nhiều video, sắp thứ tự bằng mũi tên và **Lưu cấu hình video**.
4. Trong **Điều khiển LIVE**, chọn sản phẩm → **Bắt đầu phiên**, chuyển ngay hoặc chuyển sau video đang phát.

## Data and behavior
- `product_live_videos` contains `product_id`, `media_id`, `sort_order`, unique pair; a media item can be selected for many products.
- Existing Media Library records and `media.product_id` are preserved for legacy metadata; LIVE engine no longer relies on that field.
- A product without configured and available video cannot start a Product LIVE session.
- Configured order defines OBS playback; updates are re-read at video boundaries. Changes to the current queue take effect on the next video.
- Manual Playlist remains unchanged.
- Deleting a configuration association does not delete the video. Deleting the video itself removes its relation when SQLite foreign keys are enforced.
- The migration does not automatically copy legacy `media.product_id` assignments into LIVE setups; choose videos explicitly.
- Restarting FastAPI with --reload resets the in-memory LIVE session; do not perform deploy/reload during a real stream.

## Test
Create 2 products and configure at least 2 videos for each. Confirm independent order, switch immediate and deferred, loop within current product, and ability to reuse the same video in both products.

This build was committed to GitHub, not yet validated by a running Windows OBS instance.
