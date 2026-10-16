# OBS LIVE feasibility test — minimal vertical slice

This is an OBS Preview proof-of-concept, **not an automated live-stream publisher**.

## Prerequisites
- OBS Studio 28+ (bundled obs-websocket v5).
- Run OBS and FastAPI on the same Windows machine.
- The MP4s already uploaded through Media Library must remain in backend/data/media.
- In OBS, Tools → WebSocket Server Settings → Enable WebSocket Server (default port 4455), and set a password.
- In the active OBS scene create a **Media Source**, name it exactly **Sarae Video**, and turn OFF Loop. It must be in the visible scene. Preview and confirm the source appears.

## Backend migration and start
```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Web: from web/, run `npm.cmd run dev`.

## Test steps
1. Upload 2–3 MP4 files in Media Library. Prefer short files with audio.
2. Open Playlist, name a playlist and add videos in order, save.
3. Open LIVE Control; enter your OBS WebSocket password, host 127.0.0.1, port 4455 and input name Sarae Video; click Connect/Check.
4. Choose the playlist and click Phát thử. Watch OBS Preview, not the browser player.
5. Confirm OBS plays video #1. Click Next and confirm video #2. Check whether OBS auto-advances when video reaches the end.
6. Click Stop. Confirm OBS media is stopped.
7. Restart FastAPI and check that the playlist remains in SQLite. Active playing state is in memory and resets, intentionally.

The backend **never calls OBS StartStream**. You control actual platform streaming in OBS manually.

## Important constraints
- It currently uses only uploaded MP4 files (no Link Local Folder yet).
- A single OBS Media Source is reused; make sure OBS loop is disabled.
- Automatic next-track works through a background OBS media-state poll every second, only while FastAPI is running.
- If OBS disconnects/restarts mid-playback, this prototype does not automatically recover.
- Starting or editing the OBS scene and authentication need to be done manually.
- All API endpoints are currently unauthenticated and intended ONLY for trusted local development; do not expose port 8000 publicly.
- Do not use `uvicorn --workers` for this prototype because state is process-local.
- LIVE playback changes only OBS Media Source contents, not the platform LIVE output or sales overlays.
- If the next-track test fails, inspect FastAPI logs and OBS Media Source state before investing in additional features.

## Endpoints
`GET /api/live/status`, `POST /api/live/config`, `GET/POST /api/live/playlists`, `DELETE /api/live/playlists/{id}`, `POST /api/live/start`, `POST /api/live/next`, `POST /api/live/stop`.

## Go/no-go criteria
Proceed to Link Local Folder and advanced scheduling only after: connection success, MP4 playback on OBS Preview, manual Next, auto Next, stop, repeatable consecutive playlist playback. UI polish and AI are intentionally deferred.
