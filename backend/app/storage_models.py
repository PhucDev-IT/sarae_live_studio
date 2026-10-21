"""Tables of the centralized Media Library. Additive only: Agent-local media (agent_media, product_live_videos,
video_tags) is untouched and is what the docs call LOCAL_LEGACY.

Storage is system-owned (one Google Drive today, S3/R2 later); a Media belongs to one Sarae User (owner_id) and
points to its current storage object through (provider, provider_file_id). Access control lives in the Backend,
never in the storage layout.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

PROVIDER_GOOGLE_DRIVE = "GOOGLE_DRIVE"
PROVIDER_LOCAL_LEGACY = "LOCAL_LEGACY"


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything here is stored in UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class StorageCredential(Base):
    """System storage account per provider (connected by a SUPER_ADMIN; never by a customer).

    Tokens are encrypted (app.credentials.encrypt). status: CONNECTED / REVOKED / DISCONNECTED / ERROR.
    """
    __tablename__ = "storage_credentials"
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(100), default="")
    account_email: Mapped[str] = mapped_column(String(320), default="")
    scopes: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    root_container_id: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="CONNECTED")
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    connected_by: Mapped[str] = mapped_column(String(64), default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class StorageOAuthState(Base):
    """A pending admin consent: state stored hashed, single use, short-lived; PKCE verifier encrypted."""
    __tablename__ = "storage_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[str] = mapped_column(String(64))
    verifier_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StorageOwnerContainer(Base):
    """Where a User's files live inside a provider (Drive folder id / S3 prefix). Organisation only, not security."""
    __tablename__ = "storage_owner_containers"
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    container_id: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MediaItem(Base):
    """A video of a User's Media Library. `id` is Sarae's own and never changes when the file moves provider.

    status: AVAILABLE / DELETED (hidden by the User; storage object kept for history and LIVE audit).
    Storage fields (provider, provider_file_id, md5) describe the CURRENT object; earlier copies are in
    media_storage_objects. Sarae fields (display_name, notes, tags) are only changed by the User.
    """
    __tablename__ = "media_items"
    __table_args__ = (UniqueConstraint("provider", "provider_file_id", name="uq_media_items_provider_file"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_file_id: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255), default="")
    mime_type: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MediaStorageObject(Base):
    """Every copy of a Media in a provider (ACTIVE / COPYING / RETIRED / FAILED). A provider migration copies,
    verifies the checksum and only then switches MediaItem to the new object; the old one becomes RETIRED."""
    __tablename__ = "media_storage_objects"
    __table_args__ = (UniqueConstraint("provider", "provider_file_id", name="uq_media_objects_provider_file"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    provider_file_id: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaUpload(Base):
    """A resumable upload from Sarae Web. (owner_id, client_upload_id) makes a retried create return the same
    upload instead of a second file. session_enc is the provider's upload session (it authorizes writes → encrypted).
    status: UPLOADING / COMPLETED / CANCELLED / FAILED."""
    __tablename__ = "media_uploads"
    __table_args__ = (UniqueConstraint("owner_id", "client_upload_id", name="uq_media_uploads_client"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    client_upload_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    session_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="UPLOADING")
    media_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ProductMedia(Base):
    """Product ↔ Media (N:N), ordered per product. Not tied to any Agent (unlike product_live_videos)."""
    __tablename__ = "product_media"
    __table_args__ = (UniqueConstraint("product_id", "media_id", name="uq_product_media"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class MediaThumbnail(Base):
    """Cover image of a media (a frame the browser captured from the video). Small (≤ 512 KB), kept in the DB so it
    does not depend on the storage provider or on provider-side thumbnail processing."""
    __tablename__ = "media_thumbnails"
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MediaItemPresenter(Base):
    """Presenters appearing in a Media Library video (N:N), independent of any Agent."""
    __tablename__ = "media_item_presenters"
    media_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True)
    presenter_id: Mapped[int] = mapped_column(ForeignKey("presenters.id", ondelete="CASCADE"), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)


TABLES = [StorageCredential.__table__, StorageOAuthState.__table__, StorageOwnerContainer.__table__,
          MediaItem.__table__, MediaStorageObject.__table__, MediaUpload.__table__, ProductMedia.__table__,
          MediaThumbnail.__table__, MediaItemPresenter.__table__]


def ensure_schema(bind=None):
    """Create the Media Library tables when missing (startup safety net next to the Alembic migration)."""
    from app.database import engine
    Base.metadata.create_all(bind=bind or engine, tables=TABLES, checkfirst=True)
