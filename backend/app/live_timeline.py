"""Single Video Timeline LIVE: one main video per session, products pinned at video timestamps.

A session's `playback_mode` decides how it plays:
- SINGLE_VIDEO_TIMELINE: one Media Library video (`main_media_id`) plays continuously (optionally looped); the active
  timeline version says which product is pinned from which position of that video.
- ADVANCED_LEGACY: the product lineup / video queue / rotation behaviour of app.live_sessions and app.playback_policy.
  Every session created before this mode existed is ADVANCED_LEGACY (migration 20261029_0016 backfills it).

Timelines are versioned per session: a DRAFT is edited freely, APPROVED is frozen, exactly one version may be ACTIVE
(the one the Windows Agent runs). Positions are in milliseconds of the main video.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, delete, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.catalog import Product
from app.database import Base, SessionLocal
from app.identity import current_owner
from app.ownership import Owner

SINGLE_VIDEO_TIMELINE = "SINGLE_VIDEO_TIMELINE"
ADVANCED_LEGACY = "ADVANCED_LEGACY"
PLAYBACK_MODES = (SINGLE_VIDEO_TIMELINE, ADVANCED_LEGACY)
PLAYBACK_MODE_PATTERN = f"^({'|'.join(PLAYBACK_MODES)})$"

TIMELINE_STATUSES = ("DRAFT", "APPROVED", "ACTIVE", "ARCHIVED")
MARKER_ACTIONS = ("PIN_PRODUCT", "UNPIN_PRODUCT", "NO_ACTION")
MARKER_SOURCES = ("MANUAL", "AI")
REVIEW_STATUSES = ("APPROVED", "NEEDS_REVIEW", "REJECTED")


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LiveTimeline(Base):
    __tablename__ = "live_timelines"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_live_timeline_version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("live_sessions.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    # The main video this version was made for; markers are positions of exactly this file.
    media_id: Mapped[str] = mapped_column(String(32))
    media_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TimelineMarker(Base):
    __tablename__ = "live_timeline_markers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timeline_id: Mapped[int] = mapped_column(ForeignKey("live_timelines.id", ondelete="CASCADE"), index=True)
    at_ms: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Order of markers at the same position: the higher seq is applied last (and wins).
    seq: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(10), default="MANUAL")
    review_status: Mapped[str] = mapped_column(String(20), default="APPROVED")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AI suggestions: JSON with transcript excerpt, evidence timestamps and match reason.
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)


def ensure_schema(bind) -> None:
    """Create the timeline tables if missing (startup; Alembic 20261029_0016 does the same)."""
    LiveTimeline.__table__.create(bind=bind, checkfirst=True)
    TimelineMarker.__table__.create(bind=bind, checkfirst=True)


def is_single(item) -> bool:
    return (item.playback_mode or ADVANCED_LEGACY) == SINGLE_VIDEO_TIMELINE


def require_legacy(item, what: str = "Thao tác này"):
    """409 for the product-queue actions (switch, lineup edit, rotation policy) on a Single Video session."""
    if is_single(item):
        raise HTTPException(409, f"{what} chỉ dùng cho chế độ nâng cao (ADVANCED_LEGACY); "
                                 "phiên Single Video ghim sản phẩm theo timeline")


def main_video(db, owner_id: str, media_id: str):
    """The owner's AVAILABLE Media Library video usable as a session's main video, else 422."""
    from app.storage_models import MediaItem
    media = db.get(MediaItem, media_id)
    if (media is None or media.owner_id != owner_id or media.deleted_at is not None
            or media.status != "AVAILABLE" or (media.mime_type and not media.mime_type.startswith("video/"))):
        raise HTTPException(422, "Video chính không tồn tại hoặc chưa sẵn sàng trong Thư viện video")
    return media


# ----- pin state at a position -----------------------------------------------------------------------------------

def ordered(markers):
    """Markers in the order they take effect: by position, then by seq (the later one wins at the same position)."""
    return sorted(markers, key=lambda m: (m.at_ms, m.seq, m.id or 0))


def pin_at(markers, position_ms: int) -> int | None:
    """The product that should be pinned at `position_ms`: the effect of the last PIN/UNPIN marker at or before it.

    Positions skipped by a seek are never replayed; only the state in force at the new position counts. Markers that
    are not approved (AI suggestions under review) have no effect.
    """
    pinned = None
    for marker in ordered(markers):
        if marker.at_ms > position_ms:
            break
        if marker.review_status != "APPROVED":
            continue
        if marker.action == "PIN_PRODUCT":
            pinned = marker.product_id
        elif marker.action == "UNPIN_PRODUCT":
            pinned = None
    return pinned


# ----- web API: versioned timelines of a session ------------------------------------------------------------------

router = APIRouter(prefix="/api/live/sessions", tags=["LIVE Timeline"], dependencies=[Depends(current_owner)])
MAX_MARKERS = 2000


class MarkerInput(BaseModel):
    at_ms: int = Field(ge=0, le=86_400_000)
    action: str = Field(pattern=f"^({'|'.join(MARKER_ACTIONS)})$")
    product_id: int | None = Field(default=None, gt=0)
    label: str = Field(default="", max_length=200)
    # Kept when the editor saves AI suggestions back; a manual marker is APPROVED.
    source: str = Field(default="MANUAL", pattern=f"^({'|'.join(MARKER_SOURCES)})$")
    review_status: str = Field(default="APPROVED", pattern=f"^({'|'.join(REVIEW_STATUSES)})$")
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def product_for_pin(self):
        if self.action == "PIN_PRODUCT" and self.product_id is None:
            raise ValueError("PIN_PRODUCT cần product_id")
        if self.action != "PIN_PRODUCT":
            self.product_id = None  # one pin at a time: UNPIN removes whatever is pinned
        return self


class MarkersInput(BaseModel):
    markers: list[MarkerInput] = Field(default_factory=list, max_length=MAX_MARKERS)


class TimelineCreate(BaseModel):
    # Start from a copy of another version of this session (e.g. edit the active one); empty = no markers.
    copy_from: int | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=2000)


class TimelinePatch(BaseModel):
    note: str = Field(max_length=2000)


def session_of(db, session_id: int, owner: dict):
    from app.live_sessions import get_session
    item = get_session(db, session_id, owner)
    if not is_single(item):
        raise HTTPException(409, "Timeline chỉ dùng cho phiên Single Video")
    return item


def timeline_of(db, item, timeline_id: int) -> LiveTimeline:
    timeline = db.get(LiveTimeline, timeline_id)
    if timeline is None or timeline.session_id != item.id:
        raise HTTPException(404, "Không tìm thấy timeline")
    return timeline


def markers_of(db, timeline_id: int) -> list[TimelineMarker]:
    return ordered(db.scalars(select(TimelineMarker).where(TimelineMarker.timeline_id == timeline_id)).all())


def issues(db, owner_id: str, item, timeline: LiveTimeline, markers) -> list[dict]:
    """What blocks approval (`level: error`) or deserves a look (`warning`), per marker index where it applies."""
    found = []
    if timeline.media_id != item.main_media_id:
        found.append({"level": "error", "code": "MEDIA_CHANGED",
                      "message": "Video chính của phiên đã đổi; timeline này làm cho video khác"})
    if timeline.duration_ms is None:
        found.append({"level": "warning", "code": "DURATION_UNKNOWN",
                      "message": "Chưa biết thời lượng video; chưa kiểm tra được mốc vượt quá cuối video"})
    ids = {m.product_id for m in markers if m.product_id is not None}
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids), Product.owner_id == owner_id))} if ids else {}
    seen = {}
    for index, m in enumerate(markers):
        if timeline.duration_ms is not None and m.at_ms > timeline.duration_ms:
            found.append({"level": "error", "code": "AFTER_END", "marker": index, "message": "Mốc nằm sau cuối video"})
        if m.action == "PIN_PRODUCT":
            product = products.get(m.product_id)
            if product is None:
                found.append({"level": "error", "code": "PRODUCT_MISSING", "marker": index,
                              "message": f"Sản phẩm #{m.product_id} không còn trong catalog"})
            elif not product.tiktok_product_id:
                found.append({"level": "warning", "code": "PRODUCT_NOT_SYNCED", "marker": index,
                              "message": f"“{product.name}” chưa đồng bộ từ TikTok; Agent ghim theo tên/ảnh/giá"})
            elif product.status != "ACTIVE":
                found.append({"level": "warning", "code": "PRODUCT_INACTIVE", "marker": index,
                              "message": f"“{product.name}” đang ở trạng thái {product.status}"})
        if m.review_status == "NEEDS_REVIEW":
            found.append({"level": "error", "code": "NEEDS_REVIEW", "marker": index, "message": "Đề xuất AI chưa được duyệt"})
        if m.review_status == "APPROVED":
            if m.at_ms in seen:
                found.append({"level": "warning", "code": "SAME_TIME", "marker": index,
                              "message": f"Trùng thời điểm với mốc #{seen[m.at_ms] + 1}; mốc sau được áp dụng"})
            seen.setdefault(m.at_ms, index)
    return found


def marker_view(m: TimelineMarker) -> dict:
    return {"id": m.id, "at_ms": m.at_ms, "action": m.action, "product_id": m.product_id, "seq": m.seq, "label": m.label,
            "source": m.source, "review_status": m.review_status, "confidence": m.confidence, "evidence": m.evidence}


def view(db, owner_id: str, item, timeline: LiveTimeline, detail: bool = True) -> dict:
    markers = markers_of(db, timeline.id)
    out = {"id": timeline.id, "session_id": timeline.session_id, "version": timeline.version, "status": timeline.status,
           "media_id": timeline.media_id, "duration_ms": timeline.duration_ms, "note": timeline.note,
           "marker_count": len(markers), "created_at": timeline.created_at, "updated_at": timeline.updated_at,
           "approved_at": timeline.approved_at, "activated_at": timeline.activated_at}
    if detail:
        out["markers"] = [marker_view(m) for m in markers]
        out["issues"] = issues(db, owner_id, item, timeline, markers)
    return out


def new_version(db, item, owner_id: str, note: str = "") -> LiveTimeline:
    from app.storage_models import MediaItem
    if not item.main_media_id:
        raise HTTPException(409, "Chọn video chính trước khi tạo timeline")
    media = db.get(MediaItem, item.main_media_id)
    latest = db.scalar(select(func.max(LiveTimeline.version)).where(LiveTimeline.session_id == item.id)) or 0
    timeline = LiveTimeline(session_id=item.id, owner_id=owner_id, version=latest + 1, status="DRAFT",
                            media_id=item.main_media_id, media_sha256=media.sha256 if media else None,
                            duration_ms=round(media.duration_seconds * 1000) if media and media.duration_seconds else None,
                            note=note)
    db.add(timeline)
    db.flush()
    return timeline


def copy_markers(db, source_id: int, target_id: int) -> None:
    db.add_all(TimelineMarker(timeline_id=target_id, at_ms=m.at_ms, action=m.action, product_id=m.product_id, seq=m.seq,
                              label=m.label, source=m.source, review_status=m.review_status, confidence=m.confidence,
                              evidence=m.evidence) for m in markers_of(db, source_id))


@router.get("/{session_id}/timelines")
def list_timelines(session_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        rows = db.scalars(select(LiveTimeline).where(LiveTimeline.session_id == session_id)
                          .order_by(LiveTimeline.version.desc())).all()
        return [view(db, owner["id"], item, t, detail=False) for t in rows]


@router.post("/{session_id}/timelines", status_code=201)
def create_timeline(session_id: int, body: TimelineCreate, owner: Owner):
    from app.live_sessions import event
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Phiên đã kết thúc")
        source = timeline_of(db, item, body.copy_from) if body.copy_from else None
        timeline = new_version(db, item, owner["id"], body.note)
        if source is not None:
            copy_markers(db, source.id, timeline.id)
        copied = f";copy_of={source.version}" if source else ""
        event(db, session_id, "TIMELINE_CREATED", detail=f"version={timeline.version}{copied}")
        db.commit()
        return view(db, owner["id"], item, timeline)


@router.get("/{session_id}/timelines/{timeline_id}")
def timeline_detail(session_id: int, timeline_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        return view(db, owner["id"], item, timeline_of(db, item, timeline_id))


@router.patch("/{session_id}/timelines/{timeline_id}")
def update_timeline(session_id: int, timeline_id: int, body: TimelinePatch, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        timeline.note = body.note
        db.commit()
        return view(db, owner["id"], item, timeline)


@router.put("/{session_id}/timelines/{timeline_id}/markers")
def save_markers(session_id: int, timeline_id: int, body: MarkersInput, owner: Owner):
    """Replace every marker of a DRAFT (the editor saves the whole draft). A PIN needs a product of the owner's catalog;
    markers at the same position keep the order given (the later one wins)."""
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Chỉ sửa được timeline DRAFT; hãy tạo bản nháp mới từ phiên bản này")
        ids = {m.product_id for m in body.markers if m.product_id is not None}
        known = set(db.scalars(select(Product.id).where(Product.id.in_(ids), Product.owner_id == owner["id"]))) if ids else set()
        if ids - known:
            raise HTTPException(422, "Sản phẩm không có trong catalog: " + ", ".join(f"#{i}" for i in sorted(ids - known)))
        if timeline.duration_ms is not None:
            late = [m.at_ms for m in body.markers if m.at_ms > timeline.duration_ms]
            if late:
                raise HTTPException(422, f"Có {len(late)} mốc nằm sau cuối video")
        db.execute(delete(TimelineMarker).where(TimelineMarker.timeline_id == timeline.id))
        position_seq = {}
        rows = []
        for m in sorted(body.markers, key=lambda m: m.at_ms):  # stable: the same position keeps the given order
            seq = position_seq.get(m.at_ms, 0)
            position_seq[m.at_ms] = seq + 1
            rows.append(TimelineMarker(timeline_id=timeline.id, seq=seq, **m.model_dump()))
        db.add_all(rows)
        timeline.updated_at = now()
        db.commit()
        return view(db, owner["id"], item, timeline)


def blocking(db, owner_id: str, item, timeline: LiveTimeline, markers, verb: str):
    errors = [i for i in issues(db, owner_id, item, timeline, markers) if i["level"] == "error"]
    if errors:
        raise HTTPException(409, f"Chưa {verb} được: " + "; ".join(i["message"] for i in errors[:10]))


@router.post("/{session_id}/timelines/{timeline_id}/approve")
def approve_timeline(session_id: int, timeline_id: int, owner: Owner):
    """Freeze a DRAFT once nothing blocks it (missing products, AI suggestions not reviewed, video changed)."""
    from app.live_sessions import event
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Timeline này đã được duyệt")
        blocking(db, owner["id"], item, timeline, markers_of(db, timeline.id), "duyệt")
        timeline.status, timeline.approved_at = "APPROVED", now()
        event(db, session_id, "TIMELINE_APPROVED", detail=f"version={timeline.version}")
        db.commit()
        return view(db, owner["id"], item, timeline)


@router.post("/{session_id}/timelines/{timeline_id}/activate")
def activate_timeline(session_id: int, timeline_id: int, owner: Owner):
    """Make an approved version the one the session runs; the previous active version goes back to APPROVED (so it can
    be activated again). The session's lineup becomes the products the timeline pins. Allowed during LIVE: playback and
    OBS are not touched, the Agent recomputes the pin at its current position."""
    from app.live_sessions import event, replace_products
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        if item.status == "ENDED":
            raise HTTPException(409, "Phiên đã kết thúc")
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status not in ("APPROVED", "ACTIVE"):
            raise HTTPException(409, "Chỉ kích hoạt được timeline đã duyệt")
        markers = markers_of(db, timeline.id)
        blocking(db, owner["id"], item, timeline, markers, "kích hoạt")
        for other in db.scalars(select(LiveTimeline).where(LiveTimeline.session_id == session_id,
                                                           LiveTimeline.status == "ACTIVE", LiveTimeline.id != timeline.id)):
            other.status = "APPROVED"
        timeline.status, timeline.activated_at = "ACTIVE", now()
        item.active_timeline_id = timeline.id
        lineup = list(dict.fromkeys(m.product_id for m in markers
                                    if m.action == "PIN_PRODUCT" and m.review_status == "APPROVED"))
        replace_products(db, session_id, lineup)
        running = item.status == "RUNNING"
        event(db, session_id, "TIMELINE_ACTIVATED", detail=f"version={timeline.version};running={running}")
        db.commit()
        return view(db, owner["id"], item, timeline)


@router.delete("/{session_id}/timelines/{timeline_id}", status_code=204)
def delete_timeline(session_id: int, timeline_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Chỉ xóa được timeline DRAFT")
        db.execute(delete(TimelineMarker).where(TimelineMarker.timeline_id == timeline.id))
        db.delete(timeline)
        db.commit()
