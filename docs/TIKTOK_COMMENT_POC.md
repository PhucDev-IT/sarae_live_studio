# TikTokLive comment reader — feasibility POC

This is **a separate test**. It does not alter FastAPI, OBS, playlists, sessions, or the database.

Source library: https://github.com/isaackogan/TikTokLive (unofficial reverse-engineered TikTok LIVE connection, release/project metadata currently 7.0.1). The library's default signing endpoint uses Euler Stream infrastructure; availability, limits, and compatibility can change. Check the library's modified AGPL-3.0 license before incorporating it into a distributed application.

## Windows (PowerShell)

From the repository root:

```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe tools\tiktok_chat_poc.py @YOUR_TIKTOK_USERNAME
```

Replace YOUR_TIKTOK_USERNAME with the broadcaster's **TikTok username** (not a display name, not an @viewer username). The broadcaster must be LIVE. This test may fail if a room is offline or TikTok changes its connection behavior.

To see the full library connection log:

```powershell
.venv\Scripts\python.exe tools\tiktok_chat_poc.py @YOUR_TIKTOK_USERNAME --debug
```

## Acceptance criteria

1. Start an actual LIVE using the broadcaster account. Run the script and verify the `CONNECTED` line containing a room ID.
2. Use a **different** TikTok account to post Vietnamese comments. Verify each comment appears in the terminal with time, username and text.
3. Repeat with short comments, emojis and multiple comments; compare with LIVE Manager for missing or duplicated events.
4. Interrupt the connection and run again. This proof of concept **does not implement automatic reconnect yet**. Note any errors for follow-up.
5. Stop LIVE and check disconnect / live ended behavior.

Stop the reader with Ctrl+C. It does not post chat or control pinning. A successful comment POC is **not proof of TikTok Shop pin automation**.

## Next stage (only after test succeeds)

Add an isolated connector service behind a `CommentSource` interface. Send normalized comment events to FastAPI and live console; add reconnection/backoff, dedupe, lifecycle handling, and optional AI processing later. Pin selection and OBS changes remain manual during Phase 1.
