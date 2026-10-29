# Playback policy: video order, automatic product rotation (spec §6)

> `ADVANCED_LEGACY` sessions only. A Single Video Timeline session plays one main video and has no rotation policy
> (the endpoints answer 409, nothing is pushed to its Agent): see [SINGLE_VIDEO_TIMELINE.md](SINGLE_VIDEO_TIMELINE.md).

Random product and random video are two independent settings. The Windows Agent applies them itself (it keeps going
if the Cloud is unreachable); the Cloud stores them per LIVE session and pushes them to the Agent.

## Policy

```json
{
  "video_order": "sequential",
  "product_rotation": "off",
  "rotate_after_videos": 3,
  "rotate_after_minutes": null,
  "switch_mode": "after_current",
  "auto": true
}
```

| Field | Values | Meaning |
|---|---|---|
| `video_order` | `sequential` · `random` · `shuffle` | Order of the videos of the product on air. `random`: any other video (never the same twice in a row when there are 2+). `shuffle`: every video once in random order, then a new shuffle (the first of a new round is never the last of the previous one). |
| `product_rotation` | `off` · `sequential` · `random` · `shuffle` | Automatic change of product, same algorithms over the session's products that have playable videos. |
| `rotate_after_videos` | 1–50 or null | Change product after this many videos of the current product have **started**. |
| `rotate_after_minutes` | whole minutes 1–240 or null | Change product after this long on the current product. |
| `switch_mode` | `after_current` · `immediate` | When the minutes limit is reached: wait for the current video to end, or cut now. (The videos limit is always at a video boundary.) |
| `auto` | bool | Rotation runs only when true. The Cloud sets it to false when the operator switches product by hand; the operator turns it back on. |

When `product_rotation` is not `off`, at least one of `rotate_after_videos` / `rotate_after_minutes` is set. Both set:
whichever comes first.

## Agent command `playback_policy` (lane "live")

```json
{"session_id": 12, "policy": {...}, "lineup": [{"product_id": 5, "media_ids": ["<32 hex>", "..."]}]}
```
- `lineup`: the session's products in order with their videos (0–300 products, at most 3000 media ids in total). The
  Agent stores them as the product lineups (`product_media`) and rotates among those with at least one cached video.
- Applies to the running product session only if `session_id` matches; otherwise it is stored and used when that
  session starts (`product_play` with the same session id).
- Returns the playback status.

## Agent status (heartbeat `playback`)
Adds `"policy": {...} | null` and
`"rotation": {"auto": bool, "videos_in_product": int, "product_seconds": int, "next_product_id": int | null}`.

## Agent events
- `PRODUCT_SWITCHED` with `detail.trigger = "auto_rotation"` when the Agent changes product by itself.
- `ERROR` with `detail.code = "video_skipped"`, `detail.media_id`, `detail.message` when a video cannot be played and
  is skipped.

## Cloud API (web)
- `GET /api/live/sessions/{id}/playback-policy` → `{"policy": {...}}` (the session's, else the owner's last used, else
  the defaults above).
- `PUT /api/live/sessions/{id}/playback-policy` body = policy → `{"policy": {...}, "sent": bool, "agent_error": str|null}`.
  Also remembered as the owner's default for new sessions. Sent to the Agent when the session is RUNNING.
- `POST /api/live/sessions/{id}/playback-policy/resume` → same response with `auto` true.
- A product switch by the operator (`POST /api/live/sessions/{id}/switch`) sets `auto` to false (if rotation is on)
  and sends the policy; the session events log `AUTO_PAUSED`.
