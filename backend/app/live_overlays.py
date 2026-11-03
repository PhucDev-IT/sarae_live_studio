"""Overlay events of a Single Video Timeline version: what is drawn over the LIVE video, when, and where.

An overlay event belongs to one timeline version (same DRAFT → APPROVED → ACTIVE lifecycle as the pin markers, edited
only while DRAFT) and to one video of the session (`media_id`): it shows from `start_ms` of that video for
`duration_ms`. Pin markers and overlays live in separate tables of the same version, so saving one never touches the
other.

There is no fixed list of overlay types. An overlay is a box on the canvas made of a few building blocks
(`elements`): text, a product field, an image, a shape. A "size chart" or a "colour options" card is just one
arrangement of those blocks; `template` is a free label naming the arrangement (the built-in presets of
`GET /api/live/overlay-templates` are a starting point, nothing more). A renderer only needs to know the blocks, so a
new kind of overlay never needs a new renderer or Agent.

Geometry is normalized: the canvas is 1080×1920 (9:16) and every position or size is a fraction (0–1) of it, so it
does not depend on the screen the editor runs on. Element frames are fractions of their overlay box. Text sizes are
pixels of the 1080×1920 canvas.

Images are never loaded from a URL given by the client: an image element points to an overlay asset (an image the
owner uploaded here: MIME type and content checked, size limited, served only through a URL signed for its owner) or
to a product of the owner's catalog.

Each version also has an overlay schedule (`live_overlay_schedules`, no row = defaults). `fixed` is the model above.
In `random` mode an overlay's `start_ms`..`start_ms + duration_ms` is the window it MAY show in, `show_ms` at a time:
the video is cut into slots of `every_ms`, each slot holds at most one appearance, appearances are at least
`min_gap_ms` apart and only one overlay is on screen. Every loop of the video draws a new plan seeded by
`timeline_id:media_id:iteration`; the renderers compute it (web/src/overlay/overlaySchedule.ts is the spec), the
Backend only stores, validates and snapshots the schedule.
"""
import hashlib
import json
import struct
from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app import media_access
from app.catalog import Product
from app.database import Base, SessionLocal
from app.identity import current_owner
from app.live_timeline import (MEDIA_ID_PATTERN, LiveTimeline, now, session_of, session_videos, timeline_of,
                               video_durations)
from app.ownership import Owner

CANVAS_WIDTH, CANVAS_HEIGHT = 1080, 1920
MAX_OVERLAYS = 500
MAX_ELEMENTS = 20
# Enabled overlays of one video shown at the same moment (more blocks approval: the picture would be unreadable).
MAX_CONCURRENT = 4
MIN_DURATION_MS, MAX_DURATION_MS = 500, 3_600_000
ANIMATIONS = ("none", "fade", "slide_up", "slide_down", "slide_left", "slide_right", "zoom")
PRODUCT_FIELDS = ("name", "price", "origin_price", "sku")
COLOR = r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"
TEMPLATE = r"^[A-Za-z0-9_-]{0,40}$"
ASSET_ID = r"^[0-9a-f]{32}$"
# Overlay images: ≤ 5 MB, ≤ 4096 px a side; content must match the declared type.
ASSET_MAX_BYTES = 5 * 1024 * 1024
ASSET_MAX_SIDE = 4096
# All the distinct images one version shows (the Agent keeps them on the LIVE PC; it refuses more as well).
MAX_VERSION_ASSET_BYTES = 60 * 1024 * 1024
ASSET_TYPES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
SIGN_KIND = "overlay_asset"
EPSILON = 1e-6
# Random schedule (see the module docstring).
SCHEDULE_MODES = ("fixed", "random")
DEFAULT_SCHEDULE = {"mode": "fixed", "every_ms": 120_000, "min_gap_ms": 20_000}
DEFAULT_SHOW_MS = 6000
MIN_SHOW_MS, MAX_SHOW_MS = 1000, 600_000
MIN_EVERY_MS, MAX_EVERY_MS = 10_000, 3_600_000
MAX_GAP_MS = 600_000


class OverlayEvent(Base):
    __tablename__ = "live_overlay_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    timeline_id: Mapped[int] = mapped_column(ForeignKey("live_timelines.id", ondelete="CASCADE"), index=True)
    # The video this overlay belongs to; `start_ms` is a position of exactly this file.
    media_id: Mapped[str] = mapped_column(String(32))
    # Free label of the arrangement (e.g. SIZE_CHART); not a closed list.
    template: Mapped[str] = mapped_column(String(40), default="")
    label: Mapped[str] = mapped_column(String(200), default="")
    start_ms: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    # Random schedule only: how long one appearance lasts (the start..start+duration window is where it may show).
    show_ms: Mapped[int] = mapped_column(Integer, default=DEFAULT_SHOW_MS, server_default=str(DEFAULT_SHOW_MS))
    # The product the overlay is about (product fields / images without their own product use it).
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Box on the 1080×1920 canvas, fractions 0–1.
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    z_index: Mapped[int] = mapped_column(Integer, default=0)
    opacity: Mapped[float] = mapped_column(Float, default=1.0)
    animation_in: Mapped[str] = mapped_column(String(20), default="fade")
    animation_in_ms: Mapped[int] = mapped_column(Integer, default=300)
    animation_out: Mapped[str] = mapped_column(String(20), default="fade")
    animation_out_ms: Mapped[int] = mapped_column(Integer, default=300)
    # JSON list of building blocks (see Element).
    elements: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Order given by the editor (stable listing).
    seq: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class OverlayAsset(Base):
    """An image an owner uploaded for overlays. Kept in the database (small, checked) so it never depends on a URL."""
    __tablename__ = "live_overlay_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OverlaySchedule(Base):
    """How the overlays of one version are scheduled (no row = DEFAULT_SCHEDULE)."""
    __tablename__ = "live_overlay_schedules"
    timeline_id: Mapped[int] = mapped_column(ForeignKey("live_timelines.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(10), default="fixed")
    every_ms: Mapped[int] = mapped_column(Integer, default=DEFAULT_SCHEDULE["every_ms"])
    min_gap_ms: Mapped[int] = mapped_column(Integer, default=DEFAULT_SCHEDULE["min_gap_ms"])
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


def ensure_schema(bind) -> None:
    """Create the overlay tables and columns if missing (startup; Alembic 20261031_0019, 20261101_0020 and
    20261104_0022 do the same)."""
    from sqlalchemy import inspect, text
    OverlayAsset.__table__.create(bind=bind, checkfirst=True)
    OverlayEvent.__table__.create(bind=bind, checkfirst=True)
    OverlayTemplate.__table__.create(bind=bind, checkfirst=True)
    OverlaySchedule.__table__.create(bind=bind, checkfirst=True)
    if "show_ms" not in {c["name"] for c in inspect(bind).get_columns("live_overlay_events")}:
        with bind.begin() as conn:
            conn.execute(text(f"ALTER TABLE live_overlay_events ADD COLUMN show_ms INTEGER NOT NULL DEFAULT {DEFAULT_SHOW_MS}"))


# ----- building blocks -------------------------------------------------------------------------------------------

class Box(BaseModel):
    """A rectangle as fractions (0–1) of its container, fully inside it."""
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.width > 1 + EPSILON or self.y + self.height > 1 + EPSILON:
            raise ValueError("khung nằm ngoài vùng chứa (x + width và y + height phải ≤ 1)")
        return self


class TextStyle(BaseModel):
    font_size: float = Field(default=48, ge=8, le=400)  # px of the 1080×1920 canvas
    font_weight: int = Field(default=600, ge=100, le=900)
    font_family: str = Field(default="", max_length=60, pattern=r"^[A-Za-z0-9 _-]*$")
    color: str = Field(default="#FFFFFF", pattern=COLOR)
    background: str | None = Field(default=None, pattern=COLOR)
    align: Literal["left", "center", "right"] = "left"
    max_lines: int = Field(default=0, ge=0, le=20)  # 0 = no limit
    # Soft dark shadow under the letters: readable on the video without a background block.
    shadow: bool = False


class ElementBase(BaseModel):
    model_config = {"extra": "forbid"}
    frame: Box = Field(default_factory=lambda: Box(x=0, y=0, width=1, height=1))
    opacity: float = Field(default=1, ge=0, le=1)


class TextElement(ElementBase):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=500)
    style: TextStyle = Field(default_factory=TextStyle)


class ProductFieldElement(ElementBase):
    kind: Literal["product_field"]
    field: Literal[PRODUCT_FIELDS]
    product_id: int | None = Field(default=None, gt=0)  # empty = the overlay's product
    prefix: str = Field(default="", max_length=60)
    suffix: str = Field(default="", max_length=60)
    style: TextStyle = Field(default_factory=TextStyle)


class ImageElement(ElementBase):
    kind: Literal["image"]
    source: Literal["asset", "product"]
    asset_id: str | None = Field(default=None, pattern=ASSET_ID)
    product_id: int | None = Field(default=None, gt=0)  # source product; empty = the overlay's product
    fit: Literal["contain", "cover"] = "contain"
    radius: float = Field(default=0, ge=0, le=0.5)  # corner radius, fraction of the shorter side

    @model_validator(mode="after")
    def reference(self):
        if self.source == "asset" and self.asset_id is None:
            raise ValueError("ảnh từ kho overlay cần asset_id")
        if self.source == "product":
            self.asset_id = None
        else:
            self.product_id = None
        return self


class ShapeElement(ElementBase):
    kind: Literal["shape"]
    shape: Literal["rect", "ellipse"] = "rect"
    fill: str | None = Field(default="#000000A0", pattern=COLOR)
    stroke: str | None = Field(default=None, pattern=COLOR)
    stroke_width: float = Field(default=0, ge=0, le=40)  # px of the canvas
    radius: float = Field(default=0, ge=0, le=0.5)


Element = Annotated[Union[TextElement, ProductFieldElement, ImageElement, ShapeElement], Field(discriminator="kind")]


class Animation(BaseModel):
    name: Literal[ANIMATIONS] = "fade"
    duration_ms: int = Field(default=300, ge=0, le=5000)


class Layout(Box):
    z_index: int = Field(default=0, ge=0, le=100)
    opacity: float = Field(default=1, ge=0, le=1)


class OverlayInput(BaseModel):
    model_config = {"extra": "forbid"}
    # The session video the overlay belongs to; omitted = the session's first video.
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    template: str = Field(default="", pattern=TEMPLATE)
    label: str = Field(default="", max_length=200)
    start_ms: int = Field(ge=0, le=86_400_000)
    duration_ms: int = Field(ge=MIN_DURATION_MS, le=MAX_DURATION_MS)
    show_ms: int = Field(default=DEFAULT_SHOW_MS, ge=MIN_SHOW_MS, le=MAX_SHOW_MS)
    product_id: int | None = Field(default=None, gt=0)
    layout: Layout
    animation_in: Animation = Field(default_factory=Animation)
    animation_out: Animation = Field(default_factory=Animation)
    elements: list[Element] = Field(min_length=1, max_length=MAX_ELEMENTS)
    enabled: bool = True

    @model_validator(mode="after")
    def coherent(self):
        for element in self.elements:
            if element.kind in ("product_field", "image") and getattr(element, "source", "product") == "product" \
                    and element.product_id is None and self.product_id is None:
                raise ValueError("khối lấy dữ liệu sản phẩm cần product_id (của khối hoặc của overlay)")
        if self.animation_in.duration_ms + self.animation_out.duration_ms > self.duration_ms:
            raise ValueError("thời gian hiệu ứng vào + ra dài hơn thời lượng overlay")
        return self


class ScheduleInput(BaseModel):
    model_config = {"extra": "forbid"}
    mode: Literal[SCHEDULE_MODES] = "fixed"
    every_ms: int = Field(default=DEFAULT_SCHEDULE["every_ms"], ge=MIN_EVERY_MS, le=MAX_EVERY_MS)
    min_gap_ms: int = Field(default=DEFAULT_SCHEDULE["min_gap_ms"], ge=0, le=MAX_GAP_MS)


class OverlaysInput(BaseModel):
    overlays: list[OverlayInput] = Field(default_factory=list, max_length=MAX_OVERLAYS)
    # None = keep the version's stored schedule.
    schedule: ScheduleInput | None = None


# ----- references -------------------------------------------------------------------------------------------------

def element_list(event: OverlayEvent) -> list[dict]:
    try:
        found = json.loads(event.elements or "[]")
    except ValueError:
        return []
    return found if isinstance(found, list) else []


def product_refs(event_product: int | None, elements: list[dict]) -> set[int]:
    """Products an overlay reads: its own and those of its product blocks."""
    ids = {event_product} if event_product is not None else set()
    for e in elements:
        if e.get("kind") in ("product_field", "image") and e.get("product_id") is not None:
            ids.add(e["product_id"])
    return ids


def asset_refs(elements: list[dict]) -> set[str]:
    return {e["asset_id"] for e in elements if e.get("kind") == "image" and e.get("asset_id")}


def owned_products(db, owner_id: str, ids) -> dict[int, Product]:
    ids = set(ids)
    return {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids), Product.owner_id == owner_id))} if ids else {}


def owned_assets(db, owner_id: str, ids) -> dict[str, OverlayAsset]:
    """The owner's assets among `ids` that are not deleted."""
    ids = set(ids)
    return {a.id: a for a in db.scalars(select(OverlayAsset).where(OverlayAsset.id.in_(ids), OverlayAsset.owner_id == owner_id,
                                                                    OverlayAsset.deleted_at.is_(None)))} if ids else {}


# ----- validation -------------------------------------------------------------------------------------------------

def events_of(db, timeline_id: int) -> list[OverlayEvent]:
    """Overlays of a version in the order the editor saved them."""
    return list(db.scalars(select(OverlayEvent).where(OverlayEvent.timeline_id == timeline_id)
                           .order_by(OverlayEvent.seq, OverlayEvent.id)))


def busiest(events) -> tuple[int, int]:
    """(most overlays shown at once, first position where that happens) of overlays of one video."""
    edges = sorted([(e.start_ms, 1) for e in events] + [(e.start_ms + e.duration_ms, -1) for e in events],
                   key=lambda edge: (edge[0], edge[1]))  # an overlay ending where another starts does not overlap it
    peak, at, shown = 0, 0, 0
    for position, step in edges:
        shown += step
        if shown > peak:
            peak, at = shown, position
    return peak, at


def schedule_of(db, timeline_id: int | None) -> dict:
    """The overlay schedule of a version (DEFAULT_SCHEDULE without a stored row)."""
    row = db.get(OverlaySchedule, timeline_id) if timeline_id is not None else None
    return {"mode": row.mode, "every_ms": row.every_ms, "min_gap_ms": row.min_gap_ms} if row else dict(DEFAULT_SCHEDULE)


def empty_slots(events, every_ms: int) -> int:
    """Slots [k·every, (k+1)·every) of one video, up to its last window end, where no overlay can fit a whole
    appearance inside slot ∩ window (they stay empty on every loop)."""
    end = max(e.start_ms + e.duration_ms for e in events)
    empty = 0
    for slot in range(0, end, every_ms):
        if not any(max(slot, e.start_ms) + e.show_ms <= min(slot + every_ms, e.start_ms + e.duration_ms) for e in events):
            empty += 1
    return empty


def random_issues(events, schedule: dict) -> list[dict]:
    """What the random schedule adds: an appearance must fit its window, a slot and its own animations."""
    found = []
    every, gap = schedule["every_ms"], schedule["min_gap_ms"]
    for index, e in enumerate(events):
        if e.show_ms > e.duration_ms:
            found.append({"level": "error", "code": "SHOW_LONGER_THAN_WINDOW", "overlay": index,
                          "message": "Thời lượng mỗi lần hiện dài hơn khoảng cho phép của overlay"})
        if e.show_ms > every:
            found.append({"level": "error", "code": "SHOW_LONGER_THAN_SLOT", "overlay": index,
                          "message": f"Thời lượng mỗi lần hiện dài hơn mật độ (1 lần mỗi {every // 1000} giây): "
                                     "overlay không bao giờ hiện"})
        if e.animation_in_ms + e.animation_out_ms > e.show_ms:
            found.append({"level": "error", "code": "ANIMATION_LONGER_THAN_SHOW", "overlay": index,
                          "message": "Thời gian hiệu ứng vào + ra dài hơn thời lượng mỗi lần hiện"})
    enabled = [e for e in events if e.enabled]
    if enabled and every < max(e.show_ms for e in enabled) + gap:
        found.append({"level": "warning", "code": "DENSITY_TOO_HIGH",
                      "message": "Mật độ dày hơn thời lượng hiện + khoảng nghỉ: một số ô sẽ trống"})
    for media_id in dict.fromkeys(e.media_id for e in enabled):
        empty = empty_slots([e for e in enabled if e.media_id == media_id], every)
        if empty:
            found.append({"level": "warning", "code": "NO_OVERLAY_IN_SLOTS", "media_id": media_id, "slots": empty,
                          "message": f"{empty} ô không có overlay nào hiện được"})
    return found


def overlay_issues(db, owner_id: str, item, events, schedule: dict | None = None) -> list[dict]:
    """What blocks approval (`level: error`) or deserves a look (`warning`), per overlay index where it applies.
    `schedule` defaults to the stored schedule of the events' version."""
    if schedule is None:
        schedule = schedule_of(db, events[0].timeline_id if events else None)
    random = schedule["mode"] == "random"
    found = []
    videos = session_videos(item)
    durations = video_durations(db, videos + [e.media_id for e in events])
    elements = [element_list(e) for e in events]
    products = owned_products(db, owner_id, set().union(*[product_refs(e.product_id, els) for e, els in zip(events, elements)]))
    assets = owned_assets(db, owner_id, set().union(*[asset_refs(els) for els in elements]))
    for index, (e, els) in enumerate(zip(events, elements)):
        if e.media_id not in videos:
            found.append({"level": "warning", "code": "VIDEO_NOT_IN_SESSION", "overlay": index,
                          "message": "Video của overlay này không còn trong phiên; overlay bị bỏ qua khi LIVE"})
        duration = durations.get(e.media_id)
        if duration is not None and e.start_ms + e.duration_ms > duration:
            found.append({"level": "error", "code": "AFTER_END", "overlay": index, "message": "Overlay kéo dài quá cuối video"})
        elif duration is None and e.media_id in videos:
            found.append({"level": "warning", "code": "DURATION_UNKNOWN", "overlay": index,
                          "message": "Chưa biết thời lượng video; chưa kiểm tra được overlay vượt quá cuối video"})
        missing = sorted(product_refs(e.product_id, els) - set(products))
        if missing:
            found.append({"level": "error", "code": "PRODUCT_MISSING", "overlay": index,
                          "message": "Sản phẩm không còn trong catalog: " + ", ".join(f"#{i}" for i in missing)})
        if asset_refs(els) - set(assets):
            found.append({"level": "error", "code": "ASSET_MISSING", "overlay": index, "message": "Ảnh của overlay đã bị xóa khỏi kho"})
    total = sum(a.size_bytes for a in assets.values())
    if total > MAX_VERSION_ASSET_BYTES:
        found.append({"level": "error", "code": "ASSETS_TOO_LARGE",
                      "message": f"Tổng ảnh overlay {total // (1024 * 1024)} MB (tối đa {MAX_VERSION_ASSET_BYTES // (1024 * 1024)} MB)"})
    if random:  # one overlay on screen at a time: TOO_MANY_AT_ONCE cannot happen
        return found + random_issues(events, schedule)
    for media_id in dict.fromkeys(e.media_id for e in events):
        peak, at = busiest([e for e in events if e.media_id == media_id and e.enabled])
        if peak > MAX_CONCURRENT:
            found.append({"level": "error", "code": "TOO_MANY_AT_ONCE", "media_id": media_id, "at_ms": at,
                          "message": f"{peak} overlay hiện cùng lúc ở {at // 1000}s (tối đa {MAX_CONCURRENT})"})
    return found


def summary_issue(db, owner_id: str, item, timeline_id: int) -> list[dict]:
    """One line for the timeline view when the overlays of a version have errors (details: the overlays API)."""
    errors = [i for i in overlay_issues(db, owner_id, item, events_of(db, timeline_id), schedule_of(db, timeline_id))
              if i["level"] == "error"]
    if not errors:
        return []
    return [{"level": "error", "code": "OVERLAY_ERRORS",
             "message": f"Overlay có {len(errors)} lỗi: " + "; ".join(i["message"] for i in errors[:3])}]


# ----- views and versions ----------------------------------------------------------------------------------------

def event_view(e: OverlayEvent) -> dict:
    return {"id": e.id, "media_id": e.media_id, "template": e.template, "label": e.label, "start_ms": e.start_ms,
            "duration_ms": e.duration_ms, "show_ms": e.show_ms, "product_id": e.product_id,
            "layout": {"x": e.x, "y": e.y, "width": e.width, "height": e.height, "z_index": e.z_index, "opacity": e.opacity},
            "animation_in": {"name": e.animation_in, "duration_ms": e.animation_in_ms},
            "animation_out": {"name": e.animation_out, "duration_ms": e.animation_out_ms},
            "elements": element_list(e), "enabled": e.enabled, "created_at": e.created_at, "updated_at": e.updated_at}


def overlays_view(db, owner_id: str, item, timeline: LiveTimeline) -> dict:
    events = events_of(db, timeline.id)
    schedule = schedule_of(db, timeline.id)
    return {"timeline_id": timeline.id, "version": timeline.version, "status": timeline.status,
            "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, "schedule": schedule,
            "overlays": [event_view(e) for e in events], "issues": overlay_issues(db, owner_id, item, events, schedule)}


def save_schedule(db, owner_id: str, timeline_id: int, schedule: "ScheduleInput") -> None:
    row = db.get(OverlaySchedule, timeline_id)
    if row is None:
        row = OverlaySchedule(timeline_id=timeline_id, owner_id=owner_id)
        db.add(row)
    row.mode, row.every_ms, row.min_gap_ms, row.updated_at = schedule.mode, schedule.every_ms, schedule.min_gap_ms, now()


def copy_events(db, source_id: int, target_id: int) -> None:
    """Copy every overlay of a version, and its schedule, into a new version (a new DRAFT starts from what the source
    shows)."""
    source = db.get(OverlaySchedule, source_id)
    if source is not None:
        db.add(OverlaySchedule(timeline_id=target_id, owner_id=source.owner_id, mode=source.mode,
                               every_ms=source.every_ms, min_gap_ms=source.min_gap_ms))
    db.add_all(OverlayEvent(owner_id=e.owner_id, timeline_id=target_id, media_id=e.media_id, template=e.template,
                            label=e.label, start_ms=e.start_ms, duration_ms=e.duration_ms, show_ms=e.show_ms,
                            product_id=e.product_id,
                            x=e.x, y=e.y, width=e.width, height=e.height, z_index=e.z_index, opacity=e.opacity,
                            animation_in=e.animation_in, animation_in_ms=e.animation_in_ms,
                            animation_out=e.animation_out, animation_out_ms=e.animation_out_ms,
                            elements=e.elements, enabled=e.enabled, seq=e.seq)
               for e in events_of(db, source_id))


def delete_events(db, timeline_ids, schedule: bool = True) -> None:
    """Overlays (and, unless `schedule` is False, the schedule) of versions being deleted; SQLite does not cascade."""
    db.execute(delete(OverlayEvent).where(OverlayEvent.timeline_id.in_(timeline_ids)))
    if schedule:
        db.execute(delete(OverlaySchedule).where(OverlaySchedule.timeline_id.in_(timeline_ids)))


def count_events(db, timeline_id: int) -> int:
    from sqlalchemy import func
    return db.scalar(select(func.count()).select_from(OverlayEvent).where(OverlayEvent.timeline_id == timeline_id)) or 0


# ----- snapshot (what an Agent renderer syncs) --------------------------------------------------------------------

def safe_image_url(value: str | None, owner_id: str) -> str:
    """A catalog photo a renderer may load: our own photos (signed for their owner) or an https URL of the TikTok
    catalog sync; anything else is dropped."""
    url = media_access.public_url(value, owner_id)
    return url if url.startswith("/api/") or url.startswith("https://") else ""


def asset_url(asset: OverlayAsset) -> str:
    return media_access.signed(f"/api/live/overlay-assets/{asset.id}/file", SIGN_KIND, asset.id, asset.owner_id)


def snapshot(db, item, timeline: LiveTimeline) -> dict:
    """The overlays of one version as a renderer runs them: enabled overlays of the session's videos, with the
    products and assets they read. `revision` is a hash of the content (signed URLs excluded): it changes exactly when
    what is shown changes, so a renderer re-syncs only then."""
    videos = set(session_videos(item))
    events = [e for e in events_of(db, timeline.id) if e.enabled and e.media_id in videos]
    views = []
    for e in events:
        out = event_view(e)
        for key in ("created_at", "updated_at", "enabled"):
            del out[key]
        views.append(out)
    elements = [v["elements"] for v in views]
    products = owned_products(db, item.owner_id, set().union(*[product_refs(v["product_id"], els) for v, els in zip(views, elements)]))
    assets = owned_assets(db, item.owner_id, set().union(*[asset_refs(els) for els in elements]))
    content = {"session_id": item.id, "timeline_id": timeline.id, "version": timeline.version,
               "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, "schedule": schedule_of(db, timeline.id),
               "overlays": views,
               "products": {str(p.id): {"name": p.name, "sku": p.sku, "price": p.price, "origin_price": p.origin_price}
                            for p in products.values()},
               "assets": {a.id: {"mime_type": a.mime_type, "sha256": a.sha256, "width": a.width, "height": a.height,
                                 "size_bytes": a.size_bytes} for a in assets.values()}}
    revision = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]
    for pid, product in products.items():
        content["products"][str(pid)]["image_url"] = safe_image_url(product.image_url, item.owner_id)
    for aid, asset in assets.items():
        content["assets"][aid]["url"] = asset_url(asset)
    return {"revision": revision, **content}


# ----- web API: overlays of a version -----------------------------------------------------------------------------

router = APIRouter(prefix="/api/live", tags=["LIVE Overlays"], dependencies=[Depends(current_owner)])
public_router = APIRouter(prefix="/api/live/overlay-assets", tags=["LIVE Overlays"])


@router.get("/sessions/{session_id}/timelines/{timeline_id}/overlays")
def list_overlays(session_id: int, timeline_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        return overlays_view(db, owner["id"], item, timeline_of(db, item, timeline_id))


@router.put("/sessions/{session_id}/timelines/{timeline_id}/overlays")
def save_overlays(session_id: int, timeline_id: int, body: OverlaysInput, owner: Owner):
    """Replace every overlay of a DRAFT, of every video (the editor saves the whole draft). Each overlay belongs to one
    of the session's videos and ends before that video does; products and images must be the owner's. `schedule`
    (fixed / random) is replaced when given, kept when omitted. What only blocks approval (too many overlays at once,
    an appearance that cannot fit its window or slot) is reported in `issues`."""
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Chỉ sửa được timeline DRAFT; hãy tạo bản nháp mới từ phiên bản này")
        videos = session_videos(item)
        for o in body.overlays:
            o.media_id = o.media_id or (videos[0] if videos else None)
        # Overlays of a video removed from the session may stay (ignored on LIVE); new ones need a session video.
        kept = {e.media_id for e in events_of(db, timeline.id)}
        if {o.media_id for o in body.overlays} - set(videos) - kept:
            raise HTTPException(422, "Có overlay thuộc video không có trong phiên")
        durations = video_durations(db, [o.media_id for o in body.overlays])
        late = [o for o in body.overlays if durations.get(o.media_id) is not None and o.start_ms + o.duration_ms > durations[o.media_id]]
        if late:
            raise HTTPException(422, f"Có {len(late)} overlay kéo dài quá cuối video")
        dumped = [[el.model_dump() for el in o.elements] for o in body.overlays]
        products = set().union(*[product_refs(o.product_id, els) for o, els in zip(body.overlays, dumped)])
        missing = products - set(owned_products(db, owner["id"], products))
        if missing:
            raise HTTPException(422, "Sản phẩm không có trong catalog: " + ", ".join(f"#{i}" for i in sorted(missing)))
        assets = set().union(*[asset_refs(els) for els in dumped])
        if assets - set(owned_assets(db, owner["id"], assets)):
            raise HTTPException(422, "Có ảnh không thuộc kho overlay của tài khoản này")
        delete_events(db, [timeline.id], schedule=False)
        if body.schedule is not None:
            save_schedule(db, owner["id"], timeline.id, body.schedule)
        db.add_all(OverlayEvent(owner_id=owner["id"], timeline_id=timeline.id, media_id=o.media_id, template=o.template,
                                label=o.label, start_ms=o.start_ms, duration_ms=o.duration_ms, show_ms=o.show_ms,
                                product_id=o.product_id,
                                x=o.layout.x, y=o.layout.y, width=o.layout.width, height=o.layout.height,
                                z_index=o.layout.z_index, opacity=o.layout.opacity,
                                animation_in=o.animation_in.name, animation_in_ms=o.animation_in.duration_ms,
                                animation_out=o.animation_out.name, animation_out_ms=o.animation_out.duration_ms,
                                elements=json.dumps(els, ensure_ascii=False), enabled=o.enabled, seq=index)
                   for index, (o, els) in enumerate(zip(body.overlays, dumped)))
        timeline.updated_at = now()
        db.commit()
        return overlays_view(db, owner["id"], item, timeline)


@router.get("/sessions/{session_id}/timelines/{timeline_id}/overlays/snapshot")
def version_snapshot(session_id: int, timeline_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        return snapshot(db, item, timeline_of(db, item, timeline_id))


@router.get("/sessions/{session_id}/overlay-snapshot")
def active_snapshot(session_id: int, owner: Owner):
    """Overlays of the session's ACTIVE version (what a renderer shows now); `snapshot` is null without one."""
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = db.get(LiveTimeline, item.active_timeline_id) if item.active_timeline_id else None
        active = timeline is not None and timeline.status == "ACTIVE"
        return {"session_id": session_id, "snapshot": snapshot(db, item, timeline) if active else None}


# ----- readiness before LIVE ----------------------------------------------------------------------------------------

def readiness(db, owner_id: str, item, agent_id: str | None = None) -> dict:
    """What the Backend knows about the OBS overlay of a session: the active version, its overlays and images, and
    what the session's Agent last reported (heartbeat `playback.overlay`). The Agent-side checks (OBS canvas, Browser
    Source, transform, images on the PC, renderer READY) come from POST …/overlay-check."""
    from datetime import timezone
    from app.live import playback_of
    from app.live_sessions import agent_by_id
    checks: list[dict] = []

    def add(code: str, ok: bool, message: str, level: str = "error") -> None:
        checks.append({"code": code, "ok": bool(ok), "level": "ok" if ok else level, "message": message})

    timeline = db.get(LiveTimeline, item.active_timeline_id) if item.active_timeline_id else None
    active = timeline is not None and timeline.status == "ACTIVE"
    snap = snapshot(db, item, timeline) if active else None
    if not active:
        add("ACTIVE_VERSION", False, "Chưa kích hoạt timeline: không có overlay để phát")
    else:
        events = events_of(db, timeline.id)
        errors = [i for i in overlay_issues(db, owner_id, item, events, snap["schedule"]) if i["level"] == "error"]
        add("ACTIVE_VERSION", True, f"Phiên bản {timeline.version} đang ACTIVE: {len(snap['overlays'])} overlay bật")
        add("OVERLAYS_VALID", not errors, "Overlay hợp lệ" if not errors else "; ".join(i["message"] for i in errors[:3]))
        size = sum(a["size_bytes"] for a in snap["assets"].values())
        add("ASSETS_SIZE", size <= MAX_VERSION_ASSET_BYTES, f"{len(snap['assets'])} ảnh, {round(size / 1024 / 1024, 1)} MB")
    agent_id = item.agent_id or agent_id
    agent = agent_by_id(owner_id, agent_id) if agent_id else None
    if agent is None:
        add("AGENT_ASSIGNED", False, "Chưa chọn Agent Windows sẽ phát phiên này")
    else:
        add("AGENT_ONLINE", bool(agent.get("online")), "Agent đang online" if agent.get("online") else "Agent offline")
    reported = (playback_of(agent) or {}).get("overlay") if agent else None
    if agent is not None and not reported:
        add("AGENT_OVERLAY", False, "Agent chưa báo trạng thái overlay (bản Agent cũ hoặc chưa chạy kiểm tra)", level="warning")
    elif reported:
        if reported.get("error"):
            add("AGENT_OVERLAY", False, f"Agent: {reported['error'].get('message')}")
        if snap is not None and reported.get("session_id") == item.id:
            synced = reported.get("revision") == snap["revision"]
            add("AGENT_SYNCED", synced, "Agent đã nạp đúng phiên bản overlay đang ACTIVE" if synced else
                "Agent chưa nạp phiên bản overlay đang ACTIVE (chạy Kiểm tra OBS Overlay)", level="warning")
    ok = all(c["ok"] or c["level"] == "warning" for c in checks)
    return {"session_id": item.id, "session_name": item.name, "generated_at": datetime.now(timezone.utc).isoformat(),
            "ok": ok, "active_version": timeline.version if active else None,
            "snapshot_revision": snap["revision"] if snap else None, "checks": checks, "agent_overlay": reported}


class OverlayCheckInput(BaseModel):
    # The Agent that will play the session (before LIVE a session has none yet); omitted = the session's Agent.
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


@router.get("/sessions/{session_id}/overlay-readiness")
def overlay_readiness(session_id: int, owner: Owner, agent_id: str | None = None):
    with SessionLocal() as db:
        return readiness(db, owner["id"], session_of(db, session_id, owner), agent_id)


@router.post("/sessions/{session_id}/overlay-check", status_code=202)
def overlay_check(session_id: int, owner: Owner, body: OverlayCheckInput | None = None):
    """Ask an Agent (the session's, or the one that will play it) to check the OBS overlay before LIVE: it prepares
    the overlay hidden and never shows it. The result is the command's result:
    GET /api/agents/{agent_id}/commands/{command_id}."""
    from app.cloud_agents import create_command
    from app.owner_agents import owned_agent
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        agent_id = item.agent_id or (body.agent_id if body else None)
        if not agent_id:
            raise HTTPException(409, "Chọn Agent Windows sẽ phát phiên này để kiểm tra")
        owned_agent(owner, agent_id, online=True)
    command = create_command(agent_id, owner["id"], "overlay_check", {"session_id": session_id}, session_id=session_id)
    return {"agent_id": agent_id, "command_id": command["command_id"], "status": command.get("status")}


class OverlayPreviewInput(BaseModel):
    """"Xem thử trên OBS" from the editor: show this version's overlays at the editor's position, hidden again after
    `seconds` unless renewed. Refused by the Agent while OBS streams or a LIVE session plays."""
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    position_ms: int = Field(default=0, ge=0, le=86_400_000)
    playing: bool = False
    seconds: int = Field(default=120, ge=10, le=600)
    # Random schedule: the loop whose plan is shown (seed `timeline_id:media_id:iteration`).
    iteration: int = Field(default=0, ge=0, le=100_000)
    stop: bool = False


@router.post("/sessions/{session_id}/timelines/{timeline_id}/overlay-preview", status_code=202)
def overlay_preview(session_id: int, timeline_id: int, body: OverlayPreviewInput, owner: Owner):
    """Ask an Agent (the session's, or the one chosen in the editor) to show this version's overlays in OBS at a
    position of one video, without activating it or going LIVE. Saved content only (the Agent reads the version)."""
    from app.cloud_agents import create_command
    from app.owner_agents import owned_agent
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline_of(db, item, timeline_id)
        agent_id = item.agent_id or body.agent_id
        if not agent_id:
            raise HTTPException(409, "Chọn Agent Windows để xem thử trên OBS")
        videos = session_videos(item)
        media_id = body.media_id or (videos[0] if videos else None)
        if not body.stop and media_id not in videos:
            raise HTTPException(422, "Video không thuộc phiên")
    owned_agent(owner, agent_id, online=True)
    payload = {"session_id": session_id, "stop": True} if body.stop else {
        "session_id": session_id, "timeline_id": timeline_id, "media_id": media_id, "position_ms": body.position_ms,
        "playing": body.playing, "seconds": body.seconds, "iteration": body.iteration, "stop": False}
    command = create_command(agent_id, owner["id"], "overlay_preview", payload, session_id=session_id)
    return {"agent_id": agent_id, "command_id": command["command_id"], "status": command.get("status")}


# Built-in starting points (data, not types): the editor copies one and adjusts it freely. Fashion / live-commerce look in
# the renderer's own font (Be Vietnam Pro, bundled with the web editor and the OBS renderer): soft cards, generous inner
# margins, one strong accent per overlay (price, badge), text kept to 1–2 lines.
INK, PAPER, DARK, GOLD, CORAL = "#16161A", "#FFFFFFF2", "#111114D9", "#FFC94A", "#FF4D6D"
FADE_UP = {"animation_in": {"name": "slide_up", "duration_ms": 450}, "animation_out": {"name": "fade", "duration_ms": 300}}
ZOOM_IN = {"animation_in": {"name": "zoom", "duration_ms": 400}, "animation_out": {"name": "fade", "duration_ms": 300}}


def _frame(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


def _text(text, frame, size, weight=600, color=INK, align="left", lines=1, shadow=False):
    return {"kind": "text", "text": text, "frame": frame,
            "style": {"font_size": size, "font_weight": weight, "color": color, "align": align, "max_lines": lines, "shadow": shadow}}


def _field(field, frame, size, weight=600, color=INK, align="left", lines=1, suffix=""):
    return {"kind": "product_field", "field": field, "suffix": suffix, "frame": frame,
            "style": {"font_size": size, "font_weight": weight, "color": color, "align": align, "max_lines": lines}}


TEMPLATES = [
    {"template": "PRODUCT_INFO", "label": "Thông tin sản phẩm", "duration_ms": 6000, **FADE_UP,
     "layout": {"x": 0.04, "y": 0.745, "width": 0.92, "height": 0.105},
     "elements": [{"kind": "shape", "fill": DARK, "radius": 0.28},
                  {"kind": "shape", "fill": GOLD, "radius": 0.5, "frame": _frame(0.035, 0.3, 0.008, 0.4)},
                  _field("name", _frame(0.06, 0.14, 0.6, 0.72), 30, 600, "#FFFFFF", lines=2),
                  _field("price", _frame(0.66, 0.14, 0.31, 0.72), 50, 800, GOLD, "right", suffix="đ")]},
    {"template": "PRODUCT_SPOTLIGHT", "label": "Product Spotlight", "duration_ms": 7000,
     "animation_in": {"name": "slide_left", "duration_ms": 450}, "animation_out": {"name": "fade", "duration_ms": 300},
     "layout": {"x": 0.04, "y": 0.6, "width": 0.92, "height": 0.17},
     "elements": [{"kind": "shape", "fill": PAPER, "radius": 0.14},
                  {"kind": "image", "source": "product", "fit": "cover", "radius": 0.12, "frame": _frame(0.025, 0.08, 0.25, 0.84)},
                  {"kind": "shape", "fill": CORAL, "radius": 0.5, "frame": _frame(0.31, 0.12, 0.15, 0.2)},
                  _text("HOT", _frame(0.31, 0.12, 0.15, 0.2), 24, 800, "#FFFFFF", "center"),
                  _field("name", _frame(0.31, 0.36, 0.66, 0.3), 28, 600, INK, lines=2),
                  _field("price", _frame(0.31, 0.68, 0.66, 0.24), 42, 800, CORAL, suffix="đ")]},
    {"template": "PRICE_TAG", "label": "Giá live", "duration_ms": 5000, **ZOOM_IN,
     "layout": {"x": 0.6, "y": 0.5, "width": 0.36, "height": 0.1},
     "elements": [{"kind": "shape", "fill": CORAL, "radius": 0.5},
                  _text("GIÁ LIVE", _frame(0.1, 0.1, 0.8, 0.3), 22, 700, "#FFFFFFD9", "center"),
                  _field("price", _frame(0.06, 0.38, 0.88, 0.52), 46, 800, "#FFFFFF", "center", suffix="đ")]},
    {"template": "SIZE_CHART", "label": "Bảng size", "duration_ms": 8000, **FADE_UP,
     "layout": {"x": 0.05, "y": 0.5, "width": 0.9, "height": 0.34},
     "elements": [{"kind": "shape", "fill": PAPER, "radius": 0.07},
                  _text("BẢNG SIZE", _frame(0.06, 0.04, 0.88, 0.1), 32, 800, INK, "center"),
                  {"kind": "image", "source": "asset", "asset_id": None, "fit": "contain", "radius": 0.03, "frame": _frame(0.05, 0.16, 0.9, 0.8)}]},
    {"template": "COLOR_OPTIONS", "label": "Bảng màu", "duration_ms": 6000, **FADE_UP,
     "layout": {"x": 0.08, "y": 0.45, "width": 0.84, "height": 0.13},
     "elements": [{"kind": "shape", "fill": PAPER, "radius": 0.22},
                  _text("MÀU SẮC", _frame(0.06, 0.08, 0.88, 0.24), 24, 800, INK, "center"),
                  *[e for i, (color, name) in enumerate((("#F7F5F0", "Trắng"), ("#1B1B1F", "Đen"), ("#D8C2A2", "Be"), ("#7F9171", "Rêu")))
                    for e in ({"kind": "shape", "shape": "ellipse", "fill": color, "stroke": "#16161A33", "stroke_width": 3,
                               "frame": _frame(0.1 + i * 0.215, 0.36, 0.085, 0.32)},
                              _text(name, _frame(0.05 + i * 0.215, 0.7, 0.19, 0.22), 22, 600, "#16161AB3", "center"))]]},
    {"template": "DETAIL_IMAGE", "label": "Ảnh chi tiết", "duration_ms": 6000, **ZOOM_IN,
     "layout": {"x": 0.56, "y": 0.14, "width": 0.4, "height": 0.3},
     "elements": [{"kind": "shape", "fill": PAPER, "radius": 0.08},
                  {"kind": "image", "source": "asset", "asset_id": None, "fit": "cover", "radius": 0.07, "frame": _frame(0.04, 0.03, 0.92, 0.77)},
                  _text("Chất liệu cao cấp", _frame(0.06, 0.82, 0.88, 0.15), 24, 700, INK, "center")]},
    {"template": "COMPARE_LOOKS", "label": "Compare Looks", "duration_ms": 8000, **FADE_UP,
     "layout": {"x": 0.05, "y": 0.46, "width": 0.9, "height": 0.36},
     "elements": [{"kind": "image", "source": "asset", "asset_id": None, "fit": "cover", "radius": 0.06, "frame": _frame(0, 0, 0.485, 1)},
                  {"kind": "image", "source": "asset", "asset_id": None, "fit": "cover", "radius": 0.06, "frame": _frame(0.515, 0, 0.485, 1)},
                  {"kind": "shape", "fill": DARK, "radius": 0.5, "frame": _frame(0.03, 0.88, 0.2, 0.08)},
                  _text("LOOK 1", _frame(0.03, 0.88, 0.2, 0.08), 22, 800, "#FFFFFF", "center"),
                  {"kind": "shape", "fill": DARK, "radius": 0.5, "frame": _frame(0.545, 0.88, 0.2, 0.08)},
                  _text("LOOK 2", _frame(0.545, 0.88, 0.2, 0.08), 22, 800, "#FFFFFF", "center")]},
    {"template": "PRODUCT_IMAGE", "label": "Ảnh sản phẩm", "duration_ms": 6000, **ZOOM_IN,
     "layout": {"x": 0.6, "y": 0.15, "width": 0.36, "height": 0.25},
     "elements": [{"kind": "shape", "fill": PAPER, "radius": 0.1},
                  {"kind": "image", "source": "product", "fit": "cover", "radius": 0.09, "frame": _frame(0.04, 0.03, 0.92, 0.94)}]},
    {"template": "TEXT_CARD", "label": "Thẻ chữ", "duration_ms": 5000, **FADE_UP,
     "layout": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.07},
     "elements": [{"kind": "shape", "fill": CORAL, "radius": 0.5},
                  _text("Freeship toàn quốc", _frame(0.05, 0.12, 0.9, 0.76), 36, 800, "#FFFFFF", "center")]},
    {"template": "CAPTION", "label": "Chữ trên video", "duration_ms": 5000, **FADE_UP,
     "layout": {"x": 0.06, "y": 0.3, "width": 0.88, "height": 0.07},
     "elements": [_text("Mặc là xinh, đi đâu cũng hợp", _frame(0, 0, 1, 1), 44, 800, "#FFFFFF", "center", shadow=True)]},
]


# ----- owner templates ---------------------------------------------------------------------------------------------

MAX_OWNER_TEMPLATES = 200


class OverlayTemplate(Base):
    """An overlay arrangement an owner saved to reuse in any session (no product bound: blocks read the overlay's)."""
    __tablename__ = "live_overlay_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(80))
    template: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    # JSON: layout, elements, animation_in, animation_out, duration_ms.
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class TemplateInput(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=80)
    template: str = Field(default="", pattern=TEMPLATE)
    description: str = Field(default="", max_length=300)
    layout: Layout
    elements: list[Element] = Field(min_length=1, max_length=MAX_ELEMENTS)
    animation_in: Animation = Field(default_factory=Animation)
    animation_out: Animation = Field(default_factory=Animation)
    duration_ms: int = Field(default=5000, ge=MIN_DURATION_MS, le=MAX_DURATION_MS)

    @model_validator(mode="after")
    def reusable(self):
        if self.animation_in.duration_ms + self.animation_out.duration_ms > self.duration_ms:
            raise ValueError("thời gian hiệu ứng vào + ra dài hơn thời lượng mẫu")
        for element in self.elements:  # a template is not tied to a product: product blocks read the overlay's
            if element.kind in ("product_field", "image"):
                element.product_id = None
        return self


def template_view(t: OverlayTemplate, assets: dict | None = None) -> dict:
    content = json.loads(t.content)
    used = asset_refs(content.get("elements") or [])
    return {"id": t.id, "name": t.name, "label": t.name, "template": t.template, "description": t.description, **content,
            "owner": True, "missing_assets": sorted(used - set(assets or {})) if assets is not None else [],
            "created_at": t.created_at, "updated_at": t.updated_at}


def owned_template(db, owner_id: str, template_id: int) -> OverlayTemplate:
    found = db.get(OverlayTemplate, template_id)
    if found is None or found.owner_id != owner_id:
        raise HTTPException(404, "Không tìm thấy mẫu overlay")
    return found


def template_content(db, owner_id: str, body: TemplateInput) -> str:
    elements = [e.model_dump() for e in body.elements]
    used = asset_refs(elements)
    if used - set(owned_assets(db, owner_id, used)):
        raise HTTPException(422, "Có ảnh không thuộc kho overlay của tài khoản này")
    return json.dumps({"layout": body.layout.model_dump(), "elements": elements, "animation_in": body.animation_in.model_dump(),
                       "animation_out": body.animation_out.model_dump(), "duration_ms": body.duration_ms}, ensure_ascii=False)


def owner_templates(db, owner_id: str) -> list[dict]:
    rows = db.scalars(select(OverlayTemplate).where(OverlayTemplate.owner_id == owner_id).order_by(OverlayTemplate.updated_at.desc())).all()
    assets = owned_assets(db, owner_id, set().union(*[asset_refs(json.loads(t.content).get("elements") or []) for t in rows]))
    return [template_view(t, assets) for t in rows]


@router.get("/overlay-templates")
def overlay_templates(owner: Owner):
    """Built-in presets and the owner's own templates: starting points to copy (`template` is only a label, any
    arrangement of blocks is valid)."""
    with SessionLocal() as db:
        mine = owner_templates(db, owner["id"])
    return {"canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, "blocks": ["text", "product_field", "image", "shape"],
            "product_fields": list(PRODUCT_FIELDS), "animations": list(ANIMATIONS), "max_concurrent": MAX_CONCURRENT,
            "templates": TEMPLATES, "owner_templates": mine}


@router.post("/overlay-templates", status_code=201)
def create_template(body: TemplateInput, owner: Owner):
    """Save an arrangement (usually an overlay of the editor) as a template of this owner, usable in every session."""
    with SessionLocal() as db:
        from sqlalchemy import func
        if (db.scalar(select(func.count()).select_from(OverlayTemplate).where(OverlayTemplate.owner_id == owner["id"])) or 0) >= MAX_OWNER_TEMPLATES:
            raise HTTPException(409, f"Tối đa {MAX_OWNER_TEMPLATES} mẫu overlay")
        row = OverlayTemplate(owner_id=owner["id"], name=body.name.strip(), template=body.template, description=body.description,
                              content=template_content(db, owner["id"], body))
        db.add(row)
        db.commit()
        return template_view(row, owned_assets(db, owner["id"], asset_refs(json.loads(row.content)["elements"])))


@router.put("/overlay-templates/{template_id}")
def update_template(template_id: int, body: TemplateInput, owner: Owner):
    with SessionLocal() as db:
        row = owned_template(db, owner["id"], template_id)
        row.name, row.template, row.description = body.name.strip(), body.template, body.description
        row.content, row.updated_at = template_content(db, owner["id"], body), now()
        db.commit()
        return template_view(row, owned_assets(db, owner["id"], asset_refs(json.loads(row.content)["elements"])))


@router.post("/overlay-templates/{template_id}/duplicate", status_code=201)
def duplicate_template(template_id: int, owner: Owner):
    with SessionLocal() as db:
        source = owned_template(db, owner["id"], template_id)
        row = OverlayTemplate(owner_id=owner["id"], name=(source.name + " (bản sao)")[:80], template=source.template,
                              description=source.description, content=source.content)
        db.add(row)
        db.commit()
        return template_view(row, owned_assets(db, owner["id"], asset_refs(json.loads(row.content)["elements"])))


@router.delete("/overlay-templates/{template_id}", status_code=204)
def delete_template(template_id: int, owner: Owner):
    """Overlays made from it keep their own copy."""
    with SessionLocal() as db:
        db.delete(owned_template(db, owner["id"], template_id))
        db.commit()


# ----- overlay assets (owner images) ------------------------------------------------------------------------------

def image_size(mime: str, data: bytes) -> tuple[int, int] | None:
    """Pixel size read from the image header, None when the content is not a well-formed image of that type."""
    try:
        if mime == "image/png":
            if data[12:16] != b"IHDR":
                return None
            return struct.unpack(">II", data[16:24])
        if mime == "image/webp":
            if data[8:12] != b"WEBP":
                return None
            chunk = data[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
            if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            return None
        if mime == "image/jpeg":
            at = 2
            while at + 9 < len(data):
                if data[at] != 0xFF:
                    return None
                marker = data[at + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    at += 2
                    continue
                length = int.from_bytes(data[at + 2:at + 4], "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[at + 5:at + 9])
                    return width, height
                at += 2 + length
            return None
    except struct.error:
        return None
    return None


def asset_view(asset: OverlayAsset) -> dict:
    return {"id": asset.id, "filename": asset.filename, "mime_type": asset.mime_type, "size_bytes": asset.size_bytes,
            "sha256": asset.sha256, "width": asset.width, "height": asset.height, "created_at": asset.created_at,
            "url": asset_url(asset)}


def owned_asset(db, owner_id: str, asset_id: str) -> OverlayAsset:
    asset = db.get(OverlayAsset, asset_id)
    if asset is None or asset.owner_id != owner_id or asset.deleted_at is not None:
        raise HTTPException(404, "Không tìm thấy ảnh overlay")
    return asset


@router.post("/overlay-assets", status_code=201)
def upload_asset(owner: Owner, file: UploadFile = File(...)):
    """Store an image for overlays (PNG, JPEG or WebP, ≤ 5 MB, ≤ 4096 px a side). The declared type must match the
    content; the image is served only through a URL signed for its owner."""
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ASSET_TYPES:
        raise HTTPException(415, "Ảnh overlay phải là PNG, JPEG hoặc WebP")
    data = file.file.read(ASSET_MAX_BYTES + 1)
    if len(data) > ASSET_MAX_BYTES:
        raise HTTPException(413, "Ảnh overlay tối đa 5 MB")
    size = image_size(mime, data) if data.startswith(ASSET_TYPES[mime]) else None
    if size is None or not all(size):
        raise HTTPException(422, "Nội dung không phải ảnh hợp lệ đúng định dạng đã khai báo")
    if max(size) > ASSET_MAX_SIDE:
        raise HTTPException(422, f"Ảnh overlay tối đa {ASSET_MAX_SIDE} px mỗi cạnh")
    with SessionLocal() as db:
        asset = OverlayAsset(id=uuid4().hex, owner_id=owner["id"], filename=(file.filename or "")[:255], mime_type=mime,
                             size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), width=size[0], height=size[1],
                             content=data)
        db.add(asset)
        db.commit()
        return asset_view(asset)


@router.get("/overlay-assets")
def list_assets(owner: Owner):
    with SessionLocal() as db:
        rows = db.scalars(select(OverlayAsset).where(OverlayAsset.owner_id == owner["id"], OverlayAsset.deleted_at.is_(None))
                          .order_by(OverlayAsset.created_at.desc())).all()
        return [asset_view(a) for a in rows]


@router.delete("/overlay-assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, owner: Owner):
    """Remove an image from the kho. Refused while an approved or active version shows it (those cannot change);
    drafts that use it report ASSET_MISSING until it is replaced."""
    with SessionLocal() as db:
        asset = owned_asset(db, owner["id"], asset_id)
        frozen = db.scalars(select(OverlayEvent.elements).join(LiveTimeline, LiveTimeline.id == OverlayEvent.timeline_id)
                            .where(OverlayEvent.owner_id == owner["id"], LiveTimeline.status.in_(("APPROVED", "ACTIVE")),
                                   OverlayEvent.elements.contains(asset.id))).all()
        if any(asset.id in asset_refs(json.loads(els)) for els in frozen):
            raise HTTPException(409, "Ảnh đang dùng trong timeline đã duyệt hoặc đang chạy")
        asset.deleted_at, asset.content = now(), b""
        db.commit()


@public_router.get("/{asset_id}/file", include_in_schema=False)
def asset_file(asset_id: str, exp: int | None = None, sig: str | None = None):
    """Only through a URL signed for the image's real owner (see media_access)."""
    with SessionLocal() as db:
        asset = db.get(OverlayAsset, asset_id) if len(asset_id) == 32 else None
        media_access.check(SIGN_KIND, asset_id, asset.owner_id if asset else None, exp, sig)
        if asset.deleted_at is not None:
            raise HTTPException(410, "Ảnh overlay đã bị xóa")
        return Response(asset.content, media_type=asset.mime_type,
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'"})
