# LIVE stability phase: Loop + bounded recovery

## What's new
- LIVE Control has a **Lặp playlist liên tục** toggle (enabled by default).
- Last video automatically moves to the first when Loop is enabled, both for manual Next and OBS-ended monitoring.
- If the next media file was removed, automatic advancement skips it and tries another video.
- OBS status failures are retried with increasing wait times, max five consecutive failures; after that playback monitoring stops and reports an error instead of retrying infinitely.
- Status displays Loop and retry counter. Manual Stop still wins.
- Settings remain process-local for the current run; persisted playlists remain in SQLite.
- OBS is **not** automatically started, streaming is never started, and the prototype does not restart a dead OBS process.

## Test cases
1. Start 3 MP4 playlist with Loop **off**. Confirm video 3 ends and playback stops.
2. Start same playlist with Loop **on**. Confirm video 3 returns to video 1.
3. Click Next on video 3 in Loop mode; it should jump to video 1.
4. Remove an MP4 file from backend/data/media **only in a throwaway test playlist**; when automatic Next encounters it, the monitor should skip it. Preserve your real media files.
5. While playback runs, stop OBS WebSocket server for several seconds. Check that retry counter/status advances without excessive requests. Restart the server quickly and verify recovery if the currently loaded source is still playable; long disconnections (five failures) deliberately stop monitoring.
6. Check manually Stop still stops the OBS media source and prevents auto-next.

## Remaining limitations
- Retry is for short transient OBS WebSocket failures, not automatic recovery of a deleted/recreated OBS scene, crashed application, or streaming outage.
- The one-second monitor polls OBS. If OBS media input gets permanently stuck without reaching ENDED or throwing an error, stall detection is not yet implemented.
- A/B switching may still briefly flash; deferred to visual quality phase.
- Do not use multiworker uvicorn. This controller has one process-local state and monitor.
- Keep API/WebSocket localhost-only; no authentication has been implemented.
- These changes were committed remotely and require Windows/OBS acceptance testing.
