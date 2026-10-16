# Phase 1 — Managed LIVE Sessions (Backend)

Semi-automated LIVE: pin product manually in TikTok Shop LIVE Manager, then select the corresponding product in Sarae. TikTok integration/AI are not included.

## Installation
```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

## API

- `GET /api/live/sessions` — list sessions.
- `POST /api/live/sessions` — create DRAFT. Body `{"name":"LIVE thử nghiệm","product_ids":[1,2,3]}`.
- `GET /api/live/sessions/{id}` — session details, ordered products, status.
- `PUT /api/live/sessions/{id}/products` — body `{"product_ids":[3,1,2]}`, reorder/add/remove products; cannot remove the currently playing product.
- `POST /api/live/sessions/{id}/start` — body `{"product_id":1}`; OBS must be configured and product must have valid linked videos.
- `POST /api/live/sessions/{id}/switch` — body `{"product_id":2,"mode":"immediate"}` or `"after_current"`. The lineup is a prepared pool, not a fixed order: any catalog product with valid videos can go live; one outside the lineup is appended (`PRODUCT_ADDED`, `detail="during_live=true"`).
- `POST /api/live/sessions/{id}/finish` — stop OBS and mark ENDED.
- `GET /api/live/sessions/{id}/events` — recorded events.
- `PATCH /api/live/sessions/{id}` — body `{"name":"..."}`; rename a DRAFT/RUNNING session.
- `DELETE /api/live/sessions/{id}` — delete a DRAFT/ENDED session with its lineup and history (RUNNING → 409).
- `POST /api/live/sessions/{id}/duplicate` — new DRAFT with the same lineup (rerun an ended LIVE).
- `POST /api/live/sessions/{id}/resume` — body `{}` or `{"product_id":3}`. For a RUNNING session whose playback was lost (Backend restart), replays the product that was live (or the given one, added to the session if missing), keeps `started_at` and history, records `RESUMED`. 409 if OBS is busy or the session is not RUNNING.
- `POST /api/live/sessions/{id}/finish` on a RUNNING row no longer owned by playback (Backend restarted) closes it as ENDED without touching OBS (`detail="stale=true"`).
- A DRAFT may be created with an empty `product_ids`; `start` returns 409 until it has products.
- `GET /api/live/products` now includes `image_url`; cancelling a queued switch records `SWITCH_CANCELLED`.
- `POST /api/live/product-session/start` is legacy (no lineup, no history); the web console no longer uses it.
- Existing `POST /api/live/stop` terminates the managed session if running; existing direct product switch delegates to managed session validation.
- `GET /api/live/status` includes `session_id` for managed sessions.

## Test acceptance
1. Configure video for products 1, 2, 3, then create DRAFT with three products.
2. Reorder lineup, start session at product 1, verify OBS playback.
3. Switch immediately to 2; event history records one switch.
4. Queue product 3 after current video; OBS auto-switches at boundary; DB current product and history update.
5. Add/remove/reorder products without stopping; currently playing product is protected.
6. Finish via API or old Stop button; session state becomes ENDED.
7. Existing manual playlist features still work.
8. Confirm DB survives restart and completed history remains.

## Known limitations
- Web split: **Phiên LIVE** (`#/sessions`) plans sessions — lineup, readiness checklist, start, history. **Điều khiển LIVE** (`#/live`) operates the running session and switches only between products in its lineup; when idle it starts a prepared DRAFT session.
- Playback position is in memory: after a FastAPI restart the session must be resumed (button **Tiếp tục phiên**), which restarts the current product from its first video. The OBS connection (incl. password) is saved to `backend/data/obs_config.json` (git-ignored) and reloaded on startup.
- Events cover operator actions and product changes, not individual automatic next-video transitions.
- No official TikTok pinning or comment APIs have been integrated.
- GitHub changes are not verified against a real Windows OBS instance yet.
