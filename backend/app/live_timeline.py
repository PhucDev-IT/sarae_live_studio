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

from fastapi import HTTPException
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

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
