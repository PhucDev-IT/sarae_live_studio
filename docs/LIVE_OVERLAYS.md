# LIVE Overlays — Phase 1 (data model & API)

Overlay events extend the [Single Video Timeline](SINGLE_VIDEO_TIMELINE.md): besides the pin markers, a timeline
version holds what is drawn over the LIVE video, on which video, when, and where. Phase 1 is the data and API only:
no editor, no OBS, nothing is drawn yet.

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
