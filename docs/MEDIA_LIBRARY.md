# Media Library (local-first)

Implemented: upload MP4 to FastAPI, store under `backend/data/media`, save metadata and references in SQLite, list/search, preview, update product/presenter/tags, and delete stored files.

## Install and run

From the repository root:

```powershell
git pull origin dev
git submodule update --init --recursive
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Then launch Web in another terminal:

```powershell
cd web
npm.cmd run build
npm.cmd run dev
```

Open **Thư viện video**, select **Import MP4**, upload a small video, optionally link a Product and Presenter, preview the result, edit links/tags, restart the backend to confirm persistence, then delete it.

## API

- `GET /api/media?limit=100&offset=0&q=`
- `POST /api/media/upload` (multipart file, optional product_id/presenter_id/tags)
- `GET /api/media/{id}`
- `GET /api/media/{id}/file` (HTML video preview)
- `PATCH /api/media/{id}` (product_id, presenter_id, tags)
- `DELETE /api/media/{id}` (also removes stored MP4)

## Known limitations

- Max 500 MiB per uploaded MP4.
- FFprobe is optional. Install FFmpeg/FFprobe on PATH to extract accurate duration, dimensions, FPS and codec and to reject invalid MP4 data. Without it, file extension and size are checked, but technical metadata is empty.
- Files are stored on the machine hosting FastAPI, not in browser local storage.
- This release does not yet link arbitrary folders without uploading, generate thumbnail images, support multipart resume, create playlists or control OBS.
- No authentication yet; run on trusted local development network only.
- API and Web edits were made remotely. The full build and user workflow are not verified on the developer's Windows machine.
