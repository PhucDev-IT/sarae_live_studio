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
- `POST /api/live/sessions/{id}/switch` — body `{"product_id":2,"mode":"immediate"}` or `"after_current"`.
- `POST /api/live/sessions/{id}/finish` — stop OBS and mark ENDED.
- `GET /api/live/sessions/{id}/events` — recorded events.
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
- This is Backend-first. The reworked frontend console has not been changed; AGENTS can integrate these APIs using existing design.
- In-memory OBS playback cannot resume across FastAPI reloads. A RUNNING session row from a crash/reload is historical stale state until recovery/finalization is developed.
- Events cover operator actions and product changes, not individual automatic next-video transitions.
- No official TikTok pinning or comment APIs have been integrated.
- GitHub changes are not verified against a real Windows OBS instance yet.
