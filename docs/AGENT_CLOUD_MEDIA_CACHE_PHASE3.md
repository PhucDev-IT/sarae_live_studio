# Phase 3 — Windows Agent Cloud Media Cache (first integration)

Branch: `feature/agent-cloud-media-cache`. Scope: **download/verify/cache only**. Phase 4 will connect Start LIVE readiness and OBS / Scheduler. No change to existing LOCAL_LEGACY library.

## Flow

1. OWNER links a Sarae Media Library video to products and adds the products to a LIVE Session.
2. On the activated Windows Agent use IPC `media.cloud_cache.prepare` with `{"session_id": 123}`.
3. Agent fetches `GET /api/agent-media/manifest` with its own `X-Sarae-Agent-Key`. Server enforces owner/session/Agent binding.
4. Agent downloads each file via `GET /api/agent-media/{media_id}/content?agent_id=...` in `%LOCALAPPDATA%/SaraeAgent/cloud-media-cache/<agent_id>/`. Only files with matching length and SHA256 (or MD5 if SHA256 absent) are promoted from `.part`.
5. `media.cloud_cache.status` returns `PREPARING` / `READY` / `FAILED`, per-file local paths and failures; the Agent emits `media.cache_changed` when finished. Downloads are asynchronous and a second prepare cannot overlap in the same Agent runtime.
6. Cached complete files are reused across sessions; truncated `.part` files resume with HTTP Range. No cache deletion or migration of existing local video is performed.

## Tests / boundaries

`PYTHONPATH=agent-core/src python -m unittest discover -s agent-core/tests -p test_cloud_media_cache.py`.

These offline tests exercise download, checksum failure, Range resume, cache reuse and manifest identity. Before proceeding with Phase 4, run a real Windows + Google Drive end-to-end test for a real session; test interruption/resume with a large MOV/MP4, verify disk capacity and inspect downloaded file in OBS Preview.

**Known constraints:** There is not yet an automatic Session Start preparation trigger, scheduler consumption of the Cloud cached media IDs, durable job persistence or Cloud-visible readiness ACK. A `READY` IPC status is only Agent-local; it does **not** mean the LIVE Session start is gated. Cache entries are per Agent identity; deleted/revoked media may remain on disk and must never be served without a new authorized manifest. Any missing checksum is deliberately considered FAILED.
