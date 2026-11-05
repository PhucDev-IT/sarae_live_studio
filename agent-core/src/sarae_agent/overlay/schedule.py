"""Overlay schedules: an exact port of web/src/overlay/overlaySchedule.ts (docs/LIVE_OVERLAYS.md §13–14).

A timeline version runs its overlays in one of three modes: `fixed` (an overlay shows from `start_ms` for
`duration_ms`), `window` (`start_ms`..`start_ms + duration_ms` is the window it MAY show in, `show_ms` at a time) or
`random` (a list: an overlay with a product may show while that product is pinned, `pin_spans`; one without a product
anywhere in the video). `window` and `random` draw a plan per loop of a video with the same rules, only the windows
differ. Each plan is seeded by `timeline_id:media_id:iteration` (iteration = the engine's loop count), so the web
editor, the OBS renderer and the Agent agree on the same loop. Integer arithmetic only, 32-bit masking where JS works
on uint32 (both are checked against tests/fixtures/overlay_schedule_vectors.json). Unknown modes count as fixed.
"""
from __future__ import annotations

from typing import Callable

DEFAULT_SCHEDULE = {"mode": "fixed", "every_ms": 120_000, "min_gap_ms": 20_000}
DEFAULT_SHOW_MS = 6000
STEP_MS = 100
PLANNED_MODES = ("window", "random")
_U32 = 0xFFFFFFFF


def _imul(a: int, b: int) -> int:
    """Math.imul, as uint32 (callers only use it modulo 2^32)."""
    return (a * b) & _U32


def seed_of(key: str) -> int:
    """FNV-1a 32-bit of a string (seeds are ASCII, so code points equal UTF-16 code units)."""
    h = 0x811C9DC5
    for ch in key:
        h = _imul((h ^ ord(ch)) & _U32, 0x01000193)
    return h


def generator(seed: int) -> Callable[[], int]:
    """mulberry32: a uint32 per call."""
    a = seed & _U32

    def next_() -> int:
        nonlocal a
        a = (a + 0x6D2B79F5) & _U32
        t = a
        t = _imul(t ^ (t >> 15), t | 1)
        t = (t ^ ((t + _imul(t ^ (t >> 7), t | 61)) & _U32)) & _U32
        return (t ^ (t >> 14)) & _U32
    return next_


def show_ms_of(overlay: dict) -> int:
    show = overlay.get("show_ms")
    return DEFAULT_SHOW_MS if show is None else show


def _js(value) -> str:
    """`${value}` of JS for the seed key (null → "null")."""
    return "null" if value is None else str(value)


def schedule_of(scene: dict) -> dict:
    return {**DEFAULT_SCHEDULE, **(scene.get("schedule") or {})}


def is_planned(mode: str | None) -> bool:
    return mode in PLANNED_MODES


def pin_spans(markers: list[dict], duration_ms: int) -> list[dict]:
    """Where each product is pinned in one video, from its markers (Backend: live_timeline.pin_at): approved PIN/UNPIN
    markers by position, then seq, then id; a pin lasts until the next PIN of another product, an UNPIN or the end of
    the video. Before the first marker nothing counts as pinned. Adjacent spans of the same product are merged."""
    ordered = sorted((m for m in markers if m.get("review_status", "APPROVED") in ("APPROVED", None)
                      and m.get("action") in ("PIN_PRODUCT", "UNPIN_PRODUCT")),
                     key=lambda m: (m["at_ms"], m.get("seq") or 0, m.get("id") or 0))
    spans: list[dict] = []
    product, from_ = None, 0

    def close(at: int) -> None:
        end = min(at, duration_ms)
        if product is None or end <= from_:
            return
        if spans and spans[-1]["product_id"] == product and spans[-1]["end_ms"] == from_:
            spans[-1]["end_ms"] = end
        else:
            spans.append({"start_ms": from_, "end_ms": end, "product_id": product})
    for m in ordered:
        if m["at_ms"] >= duration_ms:
            break
        next_ = m.get("product_id") if m["action"] == "PIN_PRODUCT" else None
        if next_ == product:
            continue
        close(m["at_ms"])
        product, from_ = next_, m["at_ms"]
    close(duration_ms)
    return spans


def windows_of(overlay: dict, mode: str | None, video: dict | None) -> list[tuple[int, int]]:
    """The windows an overlay may show in under `mode` (sorted, non-overlapping). `video`: {pins, duration_ms}."""
    if mode != "random":
        return [(overlay["start_ms"], overlay["start_ms"] + overlay["duration_ms"])]
    pins = (video or {}).get("pins") or []
    end = (video or {}).get("duration_ms")
    if end is None:
        end = max([0, *(p["end_ms"] for p in pins)])
    if overlay.get("product_id") is None:
        return [(0, end)] if end > 0 else []
    return [(p["start_ms"], p["end_ms"]) for p in pins if p.get("product_id") == overlay["product_id"]]


def video_of(scene: dict, media_id: str | None) -> dict:
    """The video context of `media_id` from a snapshot's top-level `pins` / `durations` (keyed by media id)."""
    if media_id is None:
        return {}
    return {"pins": (scene.get("pins") or {}).get(media_id), "duration_ms": (scene.get("durations") or {}).get(media_id)}


def random_plan(overlays: list[dict], media_id: str | None, schedule: dict, timeline_id: int, iteration: int,
                video: dict | None = None) -> list[dict]:
    """The appearances {index, start_ms, end_ms} of one loop of `media_id` (`window` or `random` mode): enabled overlays
    of that video, in list order (index = position in `overlays`). Pure: same input, same plan."""
    mode = schedule.get("mode")
    own = []
    for index, o in enumerate(overlays):
        if o.get("enabled", True) is not False and o.get("media_id") == media_id:
            windows = windows_of(o, mode, video)
            if windows:
                own.append((index, show_ms_of(o), windows))
    every = schedule.get("every_ms", DEFAULT_SCHEDULE["every_ms"])
    gap = schedule.get("min_gap_ms", DEFAULT_SCHEDULE["min_gap_ms"])
    if not own or every <= 0:
        return []
    next_ = generator(seed_of(f"{_js(timeline_id)}:{_js(media_id)}:{_js(iteration)}"))
    end = max(w[1] for _, _, windows in own for w in windows)
    shown: dict[int, int] = {}
    plan: list[dict] = []
    last, free = -1, None
    slot = 0
    while slot < end:
        after = slot + every
        fits = []
        for index, show, windows in own:
            for w0, w1 in windows:
                lo = max(slot, w0) if free is None else max(slot, w0, free)
                hi = min(after, w1) - show
                if lo <= hi:
                    # Last chance: no window lets this overlay start in a later slot.
                    fits.append((index, show, lo, hi, all(w[1] - show < after for w in windows)))
                    break
        if fits:
            fewest = min(shown.get(f[0], 0) for f in fits)
            pool = [f for f in fits if shown.get(f[0], 0) == fewest]
            pool = [f for f in pool if f[4]] or pool
            if len(pool) > 1:
                pool = [f for f in pool if f[0] != last]
            index, show, lo, hi, _ = pool[next_() % len(pool)]
            start = lo + (next_() % ((hi - lo) // STEP_MS + 1)) * STEP_MS
            plan.append({"index": index, "start_ms": start, "end_ms": start + show})
            shown[index] = shown.get(index, 0) + 1
            last, free = index, start + show + gap
        slot = after
    return plan


def plan_of(scene: dict, media_id: str | None, iteration: int) -> list[dict]:
    """`random_plan` of a snapshot/scene for one loop of `media_id` ([] in fixed mode)."""
    sched = schedule_of(scene)
    if not is_planned(sched.get("mode")):
        return []
    return random_plan(scene.get("overlays") or [], media_id, sched, scene.get("timeline_id") or 0, iteration,
                       video_of(scene, media_id))


def is_planned_scene(scene: dict) -> bool:
    return is_planned((scene.get("schedule") or {}).get("mode"))
