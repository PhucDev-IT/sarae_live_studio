# LIVE Overlays — Phase 1 (data model & API) and Phase 2 (editor & renderer)

Overlay events extend the [Single Video Timeline](SINGLE_VIDEO_TIMELINE.md): besides the pin markers, a timeline
version holds what is drawn over the LIVE video, on which video, when, and where. Phase 1 is the data and API;
Phase 2 (§8) adds the visual editor and the one shared renderer. Nothing is sent to OBS or the Agent yet (Phase 3).

Code: `backend/app/live_overlays.py`. Migration: `20261031_0019_live_overlays`. Tests: `backend/tests/test_live_overlays.py`.

## 1. No fixed overlay types

An overlay is **a box made of building blocks**, not a member of a closed list of types.

```
Overlay event
├─ media_id, start_ms, duration_ms      when, on which video of the session
├─ template: "SIZE_CHART"               free label (A–Z, 0–9, _ -, ≤ 40), not checked against a list
├─ product_id                           product the blocks read by default (optional)
├─ layout: x, y, width, height          box on the 1080×1920 canvas, fractions 0–1
│          z_index (0–100), opacity
├─ animation_in / animation_out         {name, duration_ms}
└─ elements: [ … ]                      1–20 blocks, drawn in list order
     ├─ text           text, style
     ├─ product_field  field (name | price | origin_price | sku), product_id?, prefix, suffix, style
     ├─ image          source asset (asset_id) | product (product_id?), fit contain|cover, radius
     └─ shape          rect | ellipse, fill, stroke, stroke_width, radius
```

A "size chart", "colour options" or "compare looks" card is one arrangement of blocks. `GET /api/live/overlay-templates`
returns built-in presets (SIZE_CHART, PRODUCT_IMAGE, COLOR_OPTIONS, PRODUCT_INFO, COMPARE_LOOKS, TEXT_CARD) as a starting
point; any other arrangement and any other label is equally valid. A renderer only needs to know the four blocks, so a
new kind of overlay needs no Backend, renderer or Agent change.

| What changes | Fixed types | Building blocks (this design) |
|---|---|---|
| New overlay kind | Code + migration + renderer + Agent release | Data only (new arrangement) |
| Renderer must know | Every type | 4 blocks |
| Validation | Per type | Per block (strict schemas, `extra` forbidden) |

## 2. Geometry

- Canvas: **1080×1920** (9:16). Every position and size is a fraction (0–1) of it, never pixels of a user's screen.
- `x + width ≤ 1` and `y + height ≤ 1` (the box is inside the canvas); `width`, `height` > 0.
- Each block has a `frame` (fractions of its overlay box, same rule) and an `opacity`.
- Text sizes (`font_size`, `stroke_width`) are pixels of the 1080×1920 canvas: a renderer scales them with the canvas.
- Colours: `#RRGGBB` or `#RRGGBBAA`.

## 3. Lifecycle

Overlays share their version's lifecycle with the pin markers: **DRAFT → APPROVED → ACTIVE** (and back to APPROVED when
another version is activated).

- Only a DRAFT's overlays can be saved (`PUT`: 409 otherwise). To change an active version, create a new DRAFT with
  `copy_from`: markers **and** overlays are copied.
- An AI pin draft (`/timeline-ai/.../draft`) copies the base version's overlays unchanged.
- Markers (`PUT …/markers`) and overlays (`PUT …/overlays`) are separate tables: saving one never touches the other.
- Approve and activate are refused (409) while the overlays have errors (see §4); the timeline view reports them as a
  single `OVERLAY_ERRORS` issue, details in the overlays API.
- Deleting a DRAFT or a session deletes its overlays.

## 4. Validation

| Check | When | Result |
|---|---|---|
| Video belongs to the session | save | 422 for a new video; a video later removed from the session → warning `VIDEO_NOT_IN_SESSION` (skipped on LIVE) |
| `start_ms ≥ 0`, `500 ≤ duration_ms ≤ 3 600 000` | save | 422 |
| Overlay ends before its video ends | save | 422 (`AFTER_END` error if the duration becomes known later) |
| Animations in + out ≤ duration, known animation | save | 422 |
| Box and block frames inside their container | save | 422 |
| Product blocks have a product (own or overlay's) of the owner | save | 422; product later removed → `PRODUCT_MISSING` error |
| Images are the owner's, not deleted | save | 422; asset later deleted → `ASSET_MISSING` error |
| ≤ **4** enabled overlays of one video at the same moment | approve | `TOO_MANY_AT_ONCE` error (saving a draft is allowed) |

An overlay ending exactly where another starts does not overlap it. Disabled overlays are not counted.

## 5. Images (overlay assets)

- Upload: PNG, JPEG or WebP only, ≤ 5 MB, ≤ 4096 px a side. The declared type must match the content (magic bytes,
  and the pixel size is read from the image header); otherwise 415 / 413 / 422.
- Stored in the database (`live_overlay_assets`), per owner. Another owner's asset answers 404 and cannot be used in an
  overlay (422).
- Served only by `GET /api/live/overlay-assets/{id}/file?exp=…&sig=…`: an HMAC URL signed for the asset's real owner
  (same scheme as product photos, `app.media_access`), 1 h. Unsigned, edited or expired → 403; deleted → 410.
  Responses carry `X-Content-Type-Options: nosniff` and `Content-Security-Policy: default-src 'none'`.
- An overlay never holds a URL: images are `asset_id` or `product_id`. Product photos in a snapshot are our own signed
  photos or the https URL from the TikTok catalog sync; anything else is dropped.
- Delete is a soft delete; refused (409) while an APPROVED or ACTIVE version shows the image.

## 6. API

All routes need the owner's login, except the signed asset file.

| Method | Path | |
|---|---|---|
| GET | `/api/live/sessions/{sid}/timelines/{tid}/overlays` | `{timeline_id, version, status, canvas, overlays[], issues[]}` |
| PUT | `/api/live/sessions/{sid}/timelines/{tid}/overlays` | Body `{overlays: [...]}` (≤ 500): replaces every overlay of the DRAFT, keeps the given order |
| GET | `/api/live/sessions/{sid}/timelines/{tid}/overlays/snapshot` | Snapshot of one version |
| GET | `/api/live/sessions/{sid}/overlay-snapshot` | `{session_id, snapshot}`: the ACTIVE version's snapshot, `null` without one |
| GET | `/api/live/overlay-templates` | Canvas, blocks, product fields, animations, limit, presets |
| POST | `/api/live/overlay-assets` | Multipart `file`: upload an image → asset with a signed `url` |
| GET | `/api/live/overlay-assets` | The owner's images |
| DELETE | `/api/live/overlay-assets/{id}` | Soft delete (409 while used by an approved/active version) |
| GET | `/api/live/overlay-assets/{id}/file` | Image bytes, signed URL only |

The timeline list/detail also return `overlay_count`.

Example overlay (PUT body item):

```json
{
  "media_id": "a898cc0ea6804a4faa9a898d3657418a",
  "template": "PRODUCT_INFO",
  "start_ms": 12000, "duration_ms": 6000, "product_id": 42,
  "layout": {"x": 0.05, "y": 0.72, "width": 0.9, "height": 0.14, "z_index": 1, "opacity": 1},
  "animation_in": {"name": "slide_up", "duration_ms": 300},
  "animation_out": {"name": "fade", "duration_ms": 300},
  "elements": [
    {"kind": "shape", "fill": "#000000A0", "radius": 0.15},
    {"kind": "product_field", "field": "name", "frame": {"x": 0.05, "y": 0.08, "width": 0.9, "height": 0.5},
     "style": {"font_size": 40, "max_lines": 2}},
    {"kind": "product_field", "field": "price", "suffix": "đ", "frame": {"x": 0.05, "y": 0.6, "width": 0.9, "height": 0.35},
     "style": {"font_size": 52, "font_weight": 800, "color": "#FFD400"}}
  ],
  "enabled": true
}
```

### Snapshot (for the renderer / Agent sync)

```
{revision, session_id, timeline_id, version, canvas,
 overlays: [enabled overlays of the session's videos],
 products: {id: {name, sku, price, origin_price, image_url}},
 assets:   {id: {mime_type, sha256, width, height, size_bytes, url}}}
```

`revision` is a hash of the content without the signed URLs: it changes exactly when what is shown changes, so a
renderer re-syncs only then and caches images by `sha256`. Phase 1 does **not** send overlays to the Agent: the
`video_play` / `timeline_set` commands keep their shape (older Agents refuse unknown fields).

## 7. Deployment & rollback

- `alembic upgrade head` (or startup `shop_schema.upgrade`) creates the two tables; nothing existing is altered.
- Rollback: `alembic downgrade 20261030_0018` drops the two tables; pins and timelines are untouched.
- LIVE sessions running now are unaffected: the Agent commands and the pin scheduler did not change.

## 8. Phase 2 — visual editor and shared renderer

Code: `web/src/overlay/` (`overlayModel.ts`, `OverlayRenderer.tsx` + `overlay-renderer.css`, `OverlayStage.tsx`,
`OverlayPanel.tsx`, `overlayDraft.ts`, `overlay-editor.css`), API client `web/src/overlaysApi.ts`, wired into
`web/src/TimelineEditor.tsx` (`#/timeline?session=ID`). Backend: unchanged except `origin_price` in
`GET /api/live/catalog-products` (so `origin_price` blocks preview).

```
TimelineEditor (one version, every video of the session)
├─ Preview 9:16 ─ <video> (untouched) ─ OverlayRenderer (scene, video, position) ─ editor layer (box, handles, zones)
│                 mode: OBS Preview | TikTok Viewer Preview (simulated UI, approximate)
├─ Pin timeline (unchanged) + overlay tracks (one track per `template` label, drag bar / edges = start / end)
└─ Tabs: Mốc ghim (markers + AI, unchanged) | Overlay (list, "new from preset", inspector: blocks, box, timing, animations)
```

### Renderer contract (`OverlayRenderer`, reused as the OBS browser source in Phase 3)

| Input | Meaning |
|---|---|
| `scene` | Snapshot-shaped: `{overlays, products: {id: {name, sku, price, origin_price, image_url}}, assets: {id: {url}}}` — `GET …/overlays/snapshot` fits as is |
| `mediaId`, `positionMs` | Shows the overlays where `enabled !== false`, `media_id` matches and `start_ms ≤ position < start_ms + duration_ms` |
| `apiBase` | Origin the signed `/api/…` URLs resolve against |
| `animate` | `true`: in/out animation from the clock (`animationState`, no CSS keyframes: the same position always looks the same, after a seek too). The editor passes `false` while paused so a box is fully visible while it is edited |
| `placeholders` | Editor only: dashed box for a missing image / empty product field. OBS leaves them empty |

- Fixed logical canvas **1080×1920**, scaled to the container with one CSS `transform: scale()` (ResizeObserver); every
  size inside is a canvas pixel, so the picture does not depend on the viewport or the page CSS (`all: initial` on the
  canvas). Font stack fixed (`--ovr-font`: Segoe UI, Helvetica Neue, Arial, Noto Sans), `line-height 1.2`, no
  letter-spacing; text is vertically centred in its frame, `max_lines` clamps. Phase 3 may bundle one web font to make
  it pixel-identical on every machine.
- Drawing order: `z_index`, then list order; blocks in list order. Shape radius / image radius are fractions of the
  block's shorter side, `stroke_width` canvas px. Product prices: `vi-VN` thousands separators + prefix/suffix.
- Animations: `fade`; `slide_*` move 80 canvas px and fade; `zoom` scales from 85 %; ease-out cubic; `none` = instant.
- Images: only asset URLs and product photos present in the scene, and only `/api/…` (signed) or `https://`
  (`safeImageUrl`), same rule as the snapshot.

### Editor behaviour

| Feature | How |
|---|---|
| Overlay kinds | No closed list. "Thêm overlay" starts from `GET /api/live/overlay-templates` presets (plus the web-side data preset `PRODUCT_SPOTLIGHT` when the API lacks it) or a blank box; any block can be added / removed / reordered; the `template` label is free text (A–Z 0–9 _ -) and names the track |
| Box | Drag to move, 8 handles to resize, or numeric X / Y / W / H in canvas px; stored as fractions rounded to 4 decimals and clamped inside the canvas; z-index, opacity |
| Time | Start / duration fields (0.1 s), "start here", drag the track bar (0.1 s steps) or its edges; clamped to the video |
| Edit history | Undo / redo (Ctrl+Z, Ctrl+Shift+Z / Ctrl+Y, 100 steps; one drag or one field = one step); copy / paste (Ctrl+C / Ctrl+V at the playhead, also into another video or draft of the tab), duplicate (Ctrl+D), Delete, enable / disable |
| Animations | Select in / out + duration; ▶ plays the video from just before the entry (or exit) and pauses after it |
| Product default | A new overlay reads the product pinned at the playhead (last pin marker of that video) |
| Saving | "Lưu nháp" saves markers (`PUT …/markers`) and overlays (`PUT …/overlays`, whole list) — each only when changed. Only DRAFT is editable; APPROVED / ACTIVE are read-only with "Sửa thành bản nháp mới" (`copy_from`, the active version never changes) |
| Issues | Local checks before saving (no image chosen, no product, after the end, animations longer than the overlay, empty text, deleted image, too many at once) plus the API `issues` of the saved list, next to each overlay; errors that would be 422 block saving, `TOO_MANY_AT_ONCE` only blocks approval |
| Safe zones | "Vùng an toàn" draws the TikTok zones and a safe area; an overlay covered ≥ 20 % by a zone gets a warning. TikTok Viewer Preview draws a simulated viewer UI and says it is approximate |
| Images | "Chọn ảnh" opens the owner's overlay image library: upload (`POST /api/live/overlay-assets`, PNG/JPEG/WebP ≤ 5 MB checked in the browser and again by the Backend), pick, delete |

## 9. Phase 3 — the overlay in OBS

Code: `agent-core/src/sarae_agent/overlay/` (`service.py`, `obs_source.py`, `server.py`, `cache.py`), renderer page
`web/overlay.html` + `web/src/overlay/obs/ObsOverlayApp.tsx` built by `npm run build:overlay` into
`agent-core/src/sarae_agent/overlay/renderer/` (committed; shipped by the PyInstaller spec). Backend:
`GET /api/agent-media/overlay-snapshot?agent_id&session_id` (Agent key) returns the active version's snapshot.

```
Cloud ── snapshot (15 s poll) ──> Agent OverlayService ── images cached on this PC (sha256, ≤ 5 MB each, ≤ 60 MB)
                                   │  local server 127.0.0.1:17891 (token + Host check)
PlaybackEngine ── position ──────> │  /overlay/state  {revision, show, clock}
 (media id, ms, state, loop)       │  /overlay/scene  /overlay/ready  /api/overlay-files/…
                                   ▼
OBS ── Browser Source "Sarae Overlay" (1080×1920, transparent) ── same OverlayRenderer as the web preview
```

| Rule | How |
|---|---|
| One Browser Source | `browser_source` named `overlay.source_name`; ours = UUID remembered per scene collection (`overlay.input_uuids`), or our URL marker `sarae_overlay=1` on 127.0.0.1. A source of that name made by the operator is never touched (`OVERLAY_NAME_TAKEN`) |
| Scene items | In every scene that holds the video Media Source(s), one item of our source, tracked by scene item ID per scene; a removed item is added back, a deleted source created again |
| Transform lock | Position (0,0), rotation 0, crop 0, bounds none, top-left alignment, scale = OBS canvas / 1080×1920; read back after setting (`OVERLAY_TRANSFORM_REJECTED`) |
| Order | Above the video sources (moved back on top when dragged under) |
| OBS settings | Read only: FPS, base and output resolution are never changed. A canvas that is not 9:16 keeps the overlay hidden (`OVERLAY_CANVAS_NOT_9_16`) |
| Apply | New revision → images cached → renderer loads the scene, preloads images (`onload`, 8 s each) and fonts → `POST ready` → Agent shows the scene item. The previous revision stays on screen meanwhile |
| Failure | Renderer not ready or no answer in 20 s → the new revision is dropped (rollback). Renderer silent 6 s → overlay hidden, page reloaded (`refreshnocache`, every 30 s at most). OBS disconnected → nothing to do until it is back; visibility is applied again on reconnect. Cloud unreachable → the loaded revision keeps running |
| Never | Restart OBS or the video, change scenes, delete a source, or block the pin schedule / voice: the service runs on its own thread and only ever hides the overlay |
| Time | The renderer draws at the PlaybackEngine's last reported position (pause holds; between two reports it advances only while PLAYING, at most 1.5 s) |

Rehearsal on a real OBS (no TikTok LIVE): `agent-core/scripts/overlay_obs_rehearsal.py <out>` (refuses to run while
streaming). It checks: READY before showing, one item above the video, transform read back, pixel positions, OBS vs
headless Chrome of the same page, re-lock after a drag, back on top, re-create after a delete, OBS reconnect, page
reload, version switch without hiding, video kept playing.

## 10. Phase 4 — in step with the real playback

No overlay clock: the state is reconciled from the PlaybackEngine's reports, never from a queue of show/hide commands.

```
PlaybackEngine ── report {session, media_id, position_ms, media_state, iteration} ──> pins (unchanged) ─┐
   every watchdog tick (1 s) and at once on: video start · seek · pause · resume                       │
                                                                                                       ▼
                                             OverlayService (overlay error caught: never reaches pins)
renderer ── GET /overlay/state?rev=&seq=&build= (250 ms) ──> {revision, version, show, clock{…, seq, age_ms, playing}}
         <── draws shownAt(position) = start_ms ≤ position < start_ms + duration_ms on that media
```

| Case | Behaviour |
|---|---|
| Playback | Overlays of the reported media at the reported position (same rule as the web preview); between two reports the renderer advances only while PLAYING, ≤ 1.5 s |
| Pause | The engine reports the pause position; the engine's pause flag keeps it valid however long the pause (no stale-clock hide), `playing` false: nothing runs on |
| Seek | Reported at once; the state at the new position only (skipped overlays and pins are not replayed) |
| Video change | The new video is reported when it starts (position 0, new media id): the previous video's overlays go at once; the main video is never touched |
| Loop / random / shuffle | Every start increments the iteration and reports the media actually loaded |
| New ACTIVE version | `TIMELINE_LOADED` (pin scheduler) asks for the snapshot at once; applied only after READY, else rolled back; playback and pins untouched |
| ACK / dedupe | Each poll sends the revision and clock seq drawn and the renderer build; an older seq of the same session is ignored by the renderer; a repeated READY is idempotent; a page that outlived an Agent restart and already draws the revision counts as READY; a page of an older renderer build is reloaded |
| Reconnect | A reconnecting renderer reads the current state; nothing past is replayed. Every renderer request has a 5 s timeout |
| Limits | Backend (approval): ≤ 4 enabled overlays at once per video, ≤ 60 MB images per version, image ≤ 5 MB, animation ≤ 5 s. Agent: refuses a snapshot breaking them (`SNAPSHOT_REFUSED`, previous one kept) |

Tests: `agent-core/tests/test_overlay_sync.py` drives the real PlaybackEngine (fake OBS) into the pin scheduler and the
overlay service as `AgentRuntime` wires them: playback, pause / resume, seek both ways, A → B → A, loop, random /
shuffle, version change, renderer not ready / timeout / reload, OBS reconnect, overlay failure with pins intact, and the
pin list never duplicated. The real-OBS rehearsal also measures timing against the OBS cursor (an overlay at
cursor + 4 s for 3 s appears and disappears within ± 0.7 s).
