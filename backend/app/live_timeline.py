"""Single Video Timeline LIVE: the session's videos play one after another, products pinned at video timestamps.

A session's `playback_mode` decides how it plays:
- SINGLE_VIDEO_TIMELINE: the session's Media Library videos (`video_ids`, in order; `main_media_id` is the first) play
  continuously in `video_order` (sequential / random / shuffle), optionally looped; the active timeline version says
  which product is pinned from which position of which video. Moving to the next video keeps the pin until that
  video's first marker.
- ADVANCED_LEGACY: the product lineup / video queue / rotation behaviour of app.live_sessions and app.playback_policy.
  Every session created before this mode existed is ADVANCED_LEGACY (migration 20261029_0016 backfills it).

Timelines are versioned per session: a DRAFT is edited freely, APPROVED is frozen, exactly one version may be ACTIVE
(the one the Windows Agent runs). A version also holds overlay events (app.live_overlays), under the same lifecycle.
Each marker belongs to one video (`media_id`); its position is in milliseconds of
that video, so reordering, adding or removing videos never shifts the other videos' markers.
"""
import json
from datetime import datetime, timezone
from typing import Annotated

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
VIDEO_ORDERS = ("sequential", "random", "shuffle")
VIDEO_ORDER_PATTERN = f"^({'|'.join(VIDEO_ORDERS)})$"
MEDIA_ID_PATTERN = r"^[0-9a-f]{32}$"
MAX_VIDEOS = 50


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
    # The session's first video when this version was made (each marker carries its own video: TimelineMarker.media_id).
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
    # The video this marker belongs to; `at_ms` is a position of exactly this file.
    media_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    """Create the timeline tables if missing (startup; Alembic 20261029_0016 and 20261030_0018 do the same)."""
    from sqlalchemy import inspect, text
    LiveTimeline.__table__.create(bind=bind, checkfirst=True)
    TimelineMarker.__table__.create(bind=bind, checkfirst=True)
    if "media_id" not in {c["name"] for c in inspect(bind).get_columns("live_timeline_markers")}:
        with bind.begin() as conn:
            conn.execute(text("ALTER TABLE live_timeline_markers ADD COLUMN media_id VARCHAR(32)"))
            conn.execute(text("""UPDATE live_timeline_markers SET media_id =
                (SELECT media_id FROM live_timelines WHERE live_timelines.id = live_timeline_markers.timeline_id)"""))


def is_single(item) -> bool:
    return (item.playback_mode or ADVANCED_LEGACY) == SINGLE_VIDEO_TIMELINE


def require_legacy(item, what: str = "Thao tác này"):
    """409 for the product-queue actions (switch, lineup edit, rotation policy) on a Single Video session."""
    if is_single(item):
        raise HTTPException(409, f"{what} chỉ dùng cho chế độ nâng cao (ADVANCED_LEGACY); "
                                 "phiên Single Video ghim sản phẩm theo timeline")


def main_video(db, owner_id: str, media_id: str):
    """The owner's AVAILABLE Media Library video usable as a session video, else 422."""
    from app.storage_models import MediaItem
    media = db.get(MediaItem, media_id)
    if (media is None or media.owner_id != owner_id or media.deleted_at is not None
            or media.status != "AVAILABLE" or (media.mime_type and not media.mime_type.startswith("video/"))):
        raise HTTPException(422, "Video không tồn tại hoặc chưa sẵn sàng trong Thư viện video")
    return media


def session_videos(item) -> list[str]:
    """The session's videos in the operator's order (sessions made before the list: their main video)."""
    try:
        ids = json.loads(item.video_ids) if item.video_ids else None
    except ValueError:
        ids = None
    if isinstance(ids, list):
        return [m for m in ids if isinstance(m, str)]
    return [item.main_media_id] if item.main_media_id else []


def set_videos(db, owner_id: str, item, media_ids: list[str]) -> None:
    """Replace the session's video list (each an AVAILABLE video of the owner, no duplicates)."""
    if len(media_ids) != len(set(media_ids)):
        raise HTTPException(422, "Mỗi video chỉ được chọn một lần trong phiên")
    if len(media_ids) > MAX_VIDEOS:
        raise HTTPException(422, f"Tối đa {MAX_VIDEOS} video mỗi phiên")
    for media_id in media_ids:
        main_video(db, owner_id, media_id)
    item.video_ids = json.dumps(media_ids)
    item.main_media_id = media_ids[0] if media_ids else None


def video_durations(db, media_ids) -> dict[str, int | None]:
    """Duration (ms) of each video, None when unknown."""
    from app.storage_models import MediaItem
    ids = [m for m in dict.fromkeys(media_ids) if m]
    rows = db.scalars(select(MediaItem).where(MediaItem.id.in_(ids))).all() if ids else []
    found = {m.id: round(m.duration_seconds * 1000) if m.duration_seconds else None for m in rows}
    return {media_id: found.get(media_id) for media_id in ids}


# ----- pin state at a position -----------------------------------------------------------------------------------

def ordered(markers):
    """Markers in the order they take effect: by position, then by seq (the later one wins at the same position)."""
    return sorted(markers, key=lambda m: (m.at_ms, m.seq, m.id or 0))


def pin_at(markers, position_ms: int, carried: int | None = None) -> int | None:
    """The product that should be pinned at `position_ms` of one video: the effect of the last PIN/UNPIN marker at or
    before it; before its first marker, `carried` (what the previous video left pinned).

    Positions skipped by a seek are never replayed; only the state in force at the new position counts. Markers that
    are not approved (AI suggestions under review) have no effect.
    """
    pinned = carried
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
    # The session video the marker belongs to; omitted by older clients = the session's first video.
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
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
    videos = session_videos(item)
    if not videos:
        found.append({"level": "error", "code": "NO_VIDEO", "message": "Phiên chưa có video nào"})
    durations = video_durations(db, videos + [m.media_id for m in markers])
    unknown = [v for v in videos if durations.get(v) is None]
    if unknown:
        found.append({"level": "warning", "code": "DURATION_UNKNOWN",
                      "message": f"Chưa biết thời lượng của {len(unknown)} video; chưa kiểm tra được mốc vượt quá cuối video"})
    ids = {m.product_id for m in markers if m.product_id is not None}
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids), Product.owner_id == owner_id))} if ids else {}
    seen = {}
    for index, m in enumerate(markers):
        if m.media_id not in videos:
            found.append({"level": "warning", "code": "VIDEO_NOT_IN_SESSION", "marker": index,
                          "message": "Video của mốc này không còn trong phiên; mốc bị bỏ qua khi LIVE"})
        duration = durations.get(m.media_id)
        if duration is not None and m.at_ms > duration:
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
            at = (m.media_id, m.at_ms)
            if at in seen:
                found.append({"level": "warning", "code": "SAME_TIME", "marker": index,
                              "message": f"Trùng thời điểm với mốc #{seen[at] + 1}; mốc sau được áp dụng"})
            seen.setdefault(at, index)
    return found


def marker_view(m: TimelineMarker) -> dict:
    return {"id": m.id, "media_id": m.media_id, "at_ms": m.at_ms, "action": m.action, "product_id": m.product_id, "seq": m.seq, "label": m.label,
            "source": m.source, "review_status": m.review_status, "confidence": m.confidence, "evidence": m.evidence}


def view(db, owner_id: str, item, timeline: LiveTimeline, detail: bool = True) -> dict:
    from app.live_overlays import count_events, summary_issue
    markers = markers_of(db, timeline.id)
    out = {"id": timeline.id, "session_id": timeline.session_id, "version": timeline.version, "status": timeline.status,
           "media_id": timeline.media_id, "duration_ms": timeline.duration_ms, "note": timeline.note,
           "marker_count": len(markers), "overlay_count": count_events(db, timeline.id),
           "created_at": timeline.created_at, "updated_at": timeline.updated_at,
           "approved_at": timeline.approved_at, "activated_at": timeline.activated_at}
    if detail:
        out["markers"] = [marker_view(m) for m in markers]
        out["issues"] = issues(db, owner_id, item, timeline, markers) + summary_issue(db, owner_id, item, timeline.id)
    return out


def new_version(db, item, owner_id: str, note: str = "") -> LiveTimeline:
    from app.storage_models import MediaItem
    videos = session_videos(item)
    if not videos:
        raise HTTPException(409, "Chọn video cho phiên trước khi tạo timeline")
    media = db.get(MediaItem, videos[0])
    latest = db.scalar(select(func.max(LiveTimeline.version)).where(LiveTimeline.session_id == item.id)) or 0
    timeline = LiveTimeline(session_id=item.id, owner_id=owner_id, version=latest + 1, status="DRAFT",
                            media_id=videos[0], media_sha256=media.sha256 if media else None,
                            duration_ms=round(media.duration_seconds * 1000) if media and media.duration_seconds else None,
                            note=note)
    db.add(timeline)
    db.flush()
    return timeline


def copy_markers(db, source_id: int, target_id: int, except_media: str | None = None) -> None:
    db.add_all(TimelineMarker(timeline_id=target_id, media_id=m.media_id, at_ms=m.at_ms, action=m.action,
                              product_id=m.product_id, seq=m.seq, label=m.label, source=m.source,
                              review_status=m.review_status, confidence=m.confidence, evidence=m.evidence)
               for m in markers_of(db, source_id) if except_media is None or m.media_id != except_media)


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
            from app.live_overlays import copy_events
            copy_markers(db, source.id, timeline.id)
            copy_events(db, source.id, timeline.id)
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
    """Replace every marker of a DRAFT, of every video (the editor saves the whole draft). A marker belongs to one of
    the session's videos; a PIN needs a product of the owner's catalog; markers at the same position of a video keep
    the order given (the later one wins)."""
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Chỉ sửa được timeline DRAFT; hãy tạo bản nháp mới từ phiên bản này")
        ids = {m.product_id for m in body.markers if m.product_id is not None}
        known = set(db.scalars(select(Product.id).where(Product.id.in_(ids), Product.owner_id == owner["id"]))) if ids else set()
        if ids - known:
            raise HTTPException(422, "Sản phẩm không có trong catalog: " + ", ".join(f"#{i}" for i in sorted(ids - known)))
        videos = session_videos(item)
        for m in body.markers:
            m.media_id = m.media_id or (videos[0] if videos else None)
        # Markers of a video removed from the session may stay (ignored on LIVE); new ones need a session video.
        kept = {m.media_id for m in markers_of(db, timeline.id)}
        if {m.media_id for m in body.markers} - set(videos) - kept:
            raise HTTPException(422, "Có mốc thuộc video không có trong phiên")
        durations = video_durations(db, [m.media_id for m in body.markers])
        late = [m for m in body.markers if durations.get(m.media_id) is not None and m.at_ms > durations[m.media_id]]
        if late:
            raise HTTPException(422, f"Có {len(late)} mốc nằm sau cuối video")
        db.execute(delete(TimelineMarker).where(TimelineMarker.timeline_id == timeline.id))
        position_seq = {}
        rows = []
        for m in sorted(body.markers, key=lambda m: m.at_ms):  # stable: the same position keeps the given order
            seq = position_seq.get((m.media_id, m.at_ms), 0)
            position_seq[(m.media_id, m.at_ms)] = seq + 1
            rows.append(TimelineMarker(timeline_id=timeline.id, seq=seq, **m.model_dump()))
        db.add_all(rows)
        timeline.updated_at = now()
        db.commit()
        return view(db, owner["id"], item, timeline)


def blocking(db, owner_id: str, item, timeline: LiveTimeline, markers, verb: str):
    """409 while the markers or the overlays of a version have errors."""
    from app.live_overlays import summary_issue
    errors = [i for i in issues(db, owner_id, item, timeline, markers) + summary_issue(db, owner_id, item, timeline.id)
              if i["level"] == "error"]
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
        out = view(db, owner["id"], item, timeline)
    if running:
        # Only the pin schedule changes on the Agent: it recomputes the pin at its current position.
        sent, error = push_timeline(owner["id"], session_id)
        out.update(sent=sent, agent_error=error)
    return out


@router.get("/{session_id}/pin-state")
def pin_state(session_id: int, owner: Owner):
    """Wanted vs actual pin of a running Single Video session, as its Agent last reported (heartbeat)."""
    from app.live import playback_of
    from app.live_sessions import agent_by_id
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        active = db.get(LiveTimeline, item.active_timeline_id) if item.active_timeline_id else None
        agent = agent_by_id(owner["id"], item.agent_id) if item.status == "RUNNING" else None
        playback = playback_of(agent) if agent else {}
        reported = playback.get("timeline") or {}
        if reported.get("session_id") != session_id:
            reported = {}
        ids = {reported.get("desired_product_id"), reported.get("actual_product_id")} - {None}
        names = {p.id: p.name for p in db.scalars(select(Product).where(Product.id.in_(ids), Product.owner_id == owner["id"]))} if ids else {}
        return {"session_id": session_id, "status": item.status,
                "active_version": active.version if active is not None and active.status == "ACTIVE" else None,
                "agent_online": bool(agent and agent["online"]), "reported": bool(reported),
                "media_id": reported.get("media_id"),
                "position_ms": reported.get("position_ms"), "media_duration_ms": playback.get("media_duration"),
                "iteration": reported.get("iteration"), "agent_version": reported.get("version"),
                "desired_product_id": reported.get("desired_product_id"),
                "desired_product_name": names.get(reported.get("desired_product_id")),
                "actual_product_id": reported.get("actual_product_id"),
                "actual_product_name": names.get(reported.get("actual_product_id")),
                "actual_known": reported.get("actual_known"),
                "actual_tiktok_product_id": reported.get("actual_tiktok_product_id"),
                "live": reported.get("live"), "in_flight": reported.get("in_flight"),
                "last_applied": reported.get("last_applied"), "last_error": reported.get("last_error"),
                "dry_run": reported.get("dry_run")}


@router.delete("/{session_id}/timelines/{timeline_id}", status_code=204)
def delete_timeline(session_id: int, timeline_id: int, owner: Owner):
    with SessionLocal() as db:
        item = session_of(db, session_id, owner)
        timeline = timeline_of(db, item, timeline_id)
        if timeline.status != "DRAFT":
            raise HTTPException(409, "Chỉ xóa được timeline DRAFT")
        from app.live_overlays import delete_events
        delete_events(db, [timeline.id])
        db.execute(delete(TimelineMarker).where(TimelineMarker.timeline_id == timeline.id))
        db.delete(timeline)
        db.commit()


# ----- what the Windows Agent runs (commands video_play / timeline_set) -------------------------------------------

class AgentMarker(BaseModel):
    model_config = {"extra": "forbid"}
    id: int = Field(ge=1)
    # Omitted for a one-video session (older Agents do not know it): the marker applies to the video playing.
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    at_ms: int = Field(ge=0, le=86_400_000)
    action: str = Field(pattern=f"^({'|'.join(MARKER_ACTIONS)})$")
    product_id: int | None = Field(default=None, ge=1)
    seq: int = Field(default=0, ge=0)


class AgentProduct(BaseModel):
    model_config = {"extra": "forbid"}
    sarae_product_id: int = Field(ge=1)
    tiktok_product_id: str | None = Field(default=None, pattern=r"^[0-9]{1,80}$")
    name: str = Field(min_length=1, max_length=200)
    image_url: str = Field(default="", max_length=1000)
    price_text: str = Field(default="", max_length=80)
    expected_user_id: str = Field(default="", max_length=80)


class AgentTimeline(BaseModel):
    model_config = {"extra": "forbid"}
    version: int = Field(ge=1)
    markers: list[AgentMarker] = Field(default_factory=list, max_length=MAX_MARKERS)
    products: list[AgentProduct] = Field(default_factory=list, max_length=300)
    expected_user_id: str = Field(default="", pattern=r"^[0-9]{0,80}$")


class VideoPlayCommand(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: int = Field(ge=1)
    # The video to start with (resume: the one to continue; omitted: the first by `video_order`). `media_ids` (the
    # whole list) and `video_order` are omitted for a one-video sequential session, so that older Agents still play it.
    media_id: str | None = Field(default=None, pattern=MEDIA_ID_PATTERN)
    media_ids: list[Annotated[str, Field(pattern=MEDIA_ID_PATTERN)]] | None = Field(default=None, min_length=1,
                                                                                     max_length=MAX_VIDEOS)
    video_order: str | None = Field(default=None, pattern=VIDEO_ORDER_PATTERN)
    loop: bool = True
    start_ms: int = Field(default=0, ge=0, le=86_400_000)
    timeline: AgentTimeline | None = None

    @model_validator(mode="after")
    def a_video(self):
        if self.media_id is None and not self.media_ids:
            raise ValueError("video_play cần media_id hoặc media_ids")
        if self.media_id is not None and self.media_ids and self.media_id not in self.media_ids:
            raise ValueError("media_id không thuộc media_ids")
        return self


class TimelineSetCommand(BaseModel):
    model_config = {"extra": "forbid"}
    session_id: int = Field(ge=1)
    timeline: AgentTimeline | None = None


def validate_command(kind: str, payload: dict) -> dict:
    """Payload check of the `video_play` / `timeline_set` Agent commands (cloud_agents.validate_payload)."""
    from pydantic import ValidationError
    model = VideoPlayCommand if kind == "video_play" else TimelineSetCommand
    try:
        out = model.model_validate(payload).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, f"{kind} không hợp lệ: {str(exc).splitlines()[0]}") from None
    # Multi-video fields stay out of a one-video session's command: older Agents refuse fields they do not know.
    for key in ("media_id", "media_ids", "video_order"):
        if key in out and out[key] is None:
            del out[key]
    for marker in (out.get("timeline") or {}).get("markers", []):
        if marker["media_id"] is None:
            del marker["media_id"]
    return out


def expected_user(db, item) -> str:
    from sqlalchemy import text
    if item.tiktok_account_id is None:
        return ""
    return str(db.execute(text("SELECT user_id FROM tiktok_accounts WHERE id=:a"),
                          {"a": item.tiktok_account_id}).scalar_one_or_none() or "")


def plays_one_video(item) -> bool:
    """A one-video sequential session: its Agent commands keep the shape older Agents understand."""
    return len(session_videos(item)) <= 1 and (item.video_order or "sequential") == "sequential"


def agent_timeline(db, item) -> dict | None:
    """The session's active version as the Agent runs it: approved PIN/UNPIN markers of the session's videos and how
    to find each product's card on the LIVE Console (same mapping as a manual pin). None when no version is active
    (the videos play alone)."""
    timeline = db.get(LiveTimeline, item.active_timeline_id) if item.active_timeline_id else None
    if timeline is None or timeline.status != "ACTIVE":
        return None
    videos = set(session_videos(item))
    markers = [m for m in markers_of(db, timeline.id) if m.media_id in videos
               and m.review_status == "APPROVED" and m.action in ("PIN_PRODUCT", "UNPIN_PRODUCT")]
    one_video = plays_one_video(item)
    user_id = expected_user(db, item)
    ids = list(dict.fromkeys(m.product_id for m in markers if m.product_id is not None))
    products = []
    for product in db.scalars(select(Product).where(Product.id.in_(ids), Product.owner_id == item.owner_id)) if ids else []:
        products.append({"sarae_product_id": product.id, "tiktok_product_id": product.tiktok_product_id,
                         "name": product.name[:200], "image_url": (product.image_url or "")[:1000],
                         "price_text": str(int(product.price)) if product.price is not None else "",
                         "expected_user_id": user_id})
    return {"version": timeline.version, "expected_user_id": user_id if user_id.isdigit() else "",
            "markers": [{"id": m.id, **({} if one_video else {"media_id": m.media_id}), "at_ms": m.at_ms,
                         "action": m.action, "product_id": m.product_id, "seq": m.seq} for m in markers],
            "products": products}


def push_timeline(owner_id: str, session_id: int) -> tuple[bool, str | None]:
    """Send the active version to the Agent of a RUNNING session (playback and OBS are not touched). Never raises."""
    from app.cloud_agents import create_command
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        item = db.get(LiveSession, session_id)
        if item is None or item.status != "RUNNING" or not item.agent_id or not is_single(item):
            return False, None
        payload = {"session_id": session_id, "timeline": agent_timeline(db, item)}
        agent_id = item.agent_id
    try:
        create_command(agent_id, owner_id, "timeline_set", payload, session_id=session_id)
        return True, None
    except HTTPException as exc:
        return False, str(exc.detail)
    except Exception as exc:  # noqa: BLE001 - activation is saved even if the Agent cannot be told right now
        return False, f"{type(exc).__name__}: {exc}"[:200]
