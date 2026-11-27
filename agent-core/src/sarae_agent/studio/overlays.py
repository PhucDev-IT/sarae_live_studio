"""LIVE overlays of the local studio, like the dev branch (docs/LIVE_OVERLAYS.md).

An overlay is a box on the 1080×1920 canvas made of building blocks (text, product_field, image, shape), shown on one
video from ``start_ms`` for ``duration_ms``. Like the pin markers, Studio keeps overlays on the library video itself,
so every session reuses them and they autosave (no draft → approve → activate). The schemas and the built-in presets
are the dev Backend's (backend/app/live_overlays.py), with Studio ids: products are UUIDs and overlay images are local
assets (32 hex). Showing them in OBS is the Agent's OverlayService and renderer page, unchanged.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, ValidationError, model_validator

from sarae_agent.overlay.schedule import windows_of

from .models import StudioError


def image_info(data: bytes) -> tuple[str | None, int, int]:
    """(mime type from the content, width, height) of a PNG / JPEG / WebP, read from its header only."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data.startswith(b"\xff\xd8"):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            length = int.from_bytes(data[index + 2:index + 4], "big")
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return ("image/jpeg", int.from_bytes(data[index + 7:index + 9], "big"),
                        int.from_bytes(data[index + 5:index + 7], "big"))
            index += 2 + length
        return "image/jpeg", 0, 0
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8 ":
            return "image/webp", int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return "image/webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return "image/webp", int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None, 0, 0

CANVAS_WIDTH, CANVAS_HEIGHT = 1080, 1920
MAX_OVERLAYS = 200          # per video
MAX_ELEMENTS = 20
MAX_CONCURRENT = 4
MIN_DURATION_MS = 500
ANIMATIONS = ("none", "fade", "slide_up", "slide_down", "slide_left", "slide_right", "zoom")
PRODUCT_FIELDS = ("name", "price", "origin_price", "sku")
COLOR = r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$"
TEMPLATE = r"^[A-Za-z0-9_-]{0,40}$"
ASSET_ID = r"^[0-9a-f]{32}$"
PRODUCT_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
EPSILON = 1e-6
# Window / random schedules (dev LIVE_OVERLAYS.md §13–14), one per LIVE session: the dev Backend's defaults and limits
# (ScheduleInput, OverlayInput.show_ms). The plan itself is the Agent's overlay/schedule.py.
SCHEDULE_MODES = ("fixed", "window", "random")
DEFAULT_SCHEDULE = {"mode": "fixed", "every_ms": 120_000, "min_gap_ms": 20_000}
DEFAULT_SHOW_MS = 6000
MIN_SHOW_MS, MAX_SHOW_MS = 1000, 600_000
MIN_EVERY_MS, MAX_EVERY_MS = 10_000, 3_600_000
MAX_GAP_MS = 600_000
# Pin spans of a video whose duration is unknown run up to this position (dev: the longest stored time).
UNKNOWN_DURATION_MS = 86_400_000


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
    font_size: float = Field(default=48, ge=8, le=400)
    font_weight: int = Field(default=600, ge=100, le=900)
    font_family: str = Field(default="", max_length=60, pattern=r"^[A-Za-z0-9 _-]*$")
    color: str = Field(default="#FFFFFF", pattern=COLOR)
    background: str | None = Field(default=None, pattern=COLOR)
    align: Literal["left", "center", "right"] = "left"
    max_lines: int = Field(default=0, ge=0, le=20)
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
    product_id: str | None = Field(default=None, pattern=PRODUCT_ID)
    prefix: str = Field(default="", max_length=60)
    suffix: str = Field(default="", max_length=60)
    style: TextStyle = Field(default_factory=TextStyle)


class ImageElement(ElementBase):
    kind: Literal["image"]
    source: Literal["asset", "product"]
    asset_id: str | None = Field(default=None, pattern=ASSET_ID)
    product_id: str | None = Field(default=None, pattern=PRODUCT_ID)
    fit: Literal["contain", "cover"] = "contain"
    radius: float = Field(default=0, ge=0, le=0.5)

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
    stroke_width: float = Field(default=0, ge=0, le=40)
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
    template: str = Field(default="", pattern=TEMPLATE)
    label: str = Field(default="", max_length=200)
    # The owning video's length is checked by StudioService, rather than a fixed hourly cap.
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=MIN_DURATION_MS)
    # Window / random schedules only: how long one appearance lasts.
    show_ms: int = Field(default=DEFAULT_SHOW_MS, ge=MIN_SHOW_MS, le=MAX_SHOW_MS)
    product_id: str | None = Field(default=None, pattern=PRODUCT_ID)
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
                raise ValueError("khối lấy dữ liệu sản phẩm cần chọn sản phẩm (của khối hoặc của overlay)")
        if self.animation_in.duration_ms + self.animation_out.duration_ms > self.duration_ms:
            raise ValueError("thời gian hiệu ứng vào + ra dài hơn thời lượng overlay")
        return self


def validate_overlays(raw: object) -> list[dict]:
    """A video's whole overlay list, checked like the dev Backend's PUT …/overlays."""
    if not isinstance(raw, list) or len(raw) > MAX_OVERLAYS:
        raise StudioError("VALIDATION_ERROR", f"overlays must list at most {MAX_OVERLAYS} overlays.", {"field": "overlays"})
    clean = []
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            item = {key: value for key, value in item.items() if key not in ("id", "media_id")}
        try:
            clean.append(OverlayInput.model_validate(item).model_dump())
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(x) for x in first.get("loc", ()))
            raise StudioError("VALIDATION_ERROR", f"Overlay {index + 1}: {first.get('msg')}",
                              {"field": "overlays", "index": index, "path": where}) from exc
    return clean


def product_refs(overlay: dict) -> set[str]:
    refs = {overlay["product_id"]} if overlay.get("product_id") else set()
    for element in overlay["elements"]:
        if element["kind"] in ("product_field", "image") and element.get("product_id"):
            refs.add(element["product_id"])
    return refs


def asset_refs(overlay: dict) -> set[str]:
    return {e["asset_id"] for e in overlay["elements"] if e["kind"] == "image" and e.get("asset_id")}


def busiest(overlays: list[dict]) -> tuple[int, int]:
    """(most enabled overlays shown at once, where): an overlay ending where another starts does not overlap it."""
    edges = sorted([(o["start_ms"], 1) for o in overlays if o.get("enabled", True)]
                   + [(o["start_ms"] + o["duration_ms"], -1) for o in overlays if o.get("enabled", True)],
                   key=lambda e: (e[0], e[1]))
    peak = at = shown = 0
    for position, step in edges:
        shown += step
        if shown > peak:
            peak, at = shown, position
    return peak, at


# --- window / random schedules: checks of the dev Backend (planned_issues), windows from the Agent's schedule.py ---

def show_ms_of(overlay: dict) -> int:
    """`show_ms` of an overlay (overlays saved before it existed: the default)."""
    show = overlay.get("show_ms")
    return DEFAULT_SHOW_MS if show is None else show


def empty_slots(entries, every_ms: int) -> int:
    """Slots [k·every, (k+1)·every) of one video, up to its last window end, where no overlay can fit a whole
    appearance inside slot ∩ one of its windows (they stay empty on every loop). `entries`: (show_ms, windows)."""
    end = max([0] + [w1 for _, windows in entries for _, w1 in windows])
    empty = 0
    for slot in range(0, end, every_ms):
        if not any(max(slot, w0) + show <= min(slot + every_ms, w1) for show, windows in entries for w0, w1 in windows):
            empty += 1
    return empty


def planned_issues(overlays: list[dict], schedule: dict, pins: dict, durations: dict) -> list[dict]:
    """What the window and random schedules add (dev live_overlays.planned_issues). `overlays`: every overlay of the
    session's videos, with `media_id`; `overlay` in an issue is its position in its own video's list. Window: an
    appearance must fit its window. Random: an overlay with a product needs a pin span of that product in its video
    long enough for one appearance. Fixed mode adds nothing."""
    mode, every, gap = schedule["mode"], schedule["every_ms"], schedule["min_gap_ms"]
    if mode not in ("window", "random"):
        return []
    found = []
    positions: dict[str, int] = {}
    for o in overlays:
        index = positions.get(o["media_id"], 0)
        positions[o["media_id"]] = index + 1
        where = {"overlay": index, "media_id": o["media_id"]}
        show = show_ms_of(o)
        if mode == "window" and show > o["duration_ms"]:
            found.append({"level": "error", "code": "SHOW_LONGER_THAN_WINDOW", **where,
                          "message": "Thời lượng mỗi lần hiện dài hơn khoảng cho phép của overlay"})
        if show > every:
            found.append({"level": "error", "code": "SHOW_LONGER_THAN_SLOT", **where,
                          "message": f"Thời lượng mỗi lần hiện dài hơn mật độ (1 lần mỗi {every // 1000} giây): "
                                     "overlay không bao giờ hiện"})
        if o["animation_in"]["duration_ms"] + o["animation_out"]["duration_ms"] > show:
            found.append({"level": "error", "code": "ANIMATION_LONGER_THAN_SHOW", **where,
                          "message": "Thời gian hiệu ứng vào + ra dài hơn thời lượng mỗi lần hiện"})
        if mode == "random" and o.get("product_id") is not None:
            spans = [p for p in pins.get(o["media_id"], []) if p["product_id"] == o["product_id"]]
            if not spans:
                found.append({"level": "warning", "code": "PRODUCT_NEVER_PINNED", **where,
                              "message": "Sản phẩm của overlay này không được ghim trong video: overlay sẽ không bao giờ hiện"})
            elif all(p["end_ms"] - p["start_ms"] < show for p in spans):
                found.append({"level": "warning", "code": "PIN_TOO_SHORT", **where,
                              "message": "Sản phẩm của overlay này chỉ được ghim trong khoảng ngắn hơn thời lượng mỗi lần "
                                         "hiện: overlay sẽ không bao giờ hiện"})
    enabled = [o for o in overlays if o.get("enabled", True)]
    if enabled and every < max(show_ms_of(o) for o in enabled) + gap:
        found.append({"level": "warning", "code": "DENSITY_TOO_HIGH",
                      "message": "Mật độ dày hơn thời lượng hiện + khoảng nghỉ: một số ô sẽ trống"})
    for media_id in dict.fromkeys(o["media_id"] for o in enabled):
        # Random mode: only videos of known duration (the windows of an overlay without a product need it).
        if mode == "random" and durations.get(media_id) is None:
            continue
        video = {"pins": pins.get(media_id, []), "duration_ms": durations.get(media_id)}
        empty = empty_slots([(show_ms_of(o), windows_of(o, mode, video)) for o in enabled if o["media_id"] == media_id],
                            every)
        if empty:
            found.append({"level": "warning", "code": "NO_OVERLAY_IN_SLOTS", "media_id": media_id, "slots": empty,
                          "message": f"{empty} ô không có overlay nào hiện được"})
    return found


# --- presets: copied from the dev Backend (backend/app/live_overlays.py) ---

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


def templates() -> dict:
    """Built-in presets: starting points to copy (``template`` is only a label)."""
    return {"canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}, "blocks": ["text", "product_field", "image", "shape"],
            "product_fields": list(PRODUCT_FIELDS), "animations": list(ANIMATIONS), "max_concurrent": MAX_CONCURRENT,
            "templates": TEMPLATES}
