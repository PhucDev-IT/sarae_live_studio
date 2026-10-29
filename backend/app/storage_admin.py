"""Admin-only private R2 configuration and legacy Google Drive OAuth.

Customers never see this: every endpoint requires SUPER_ADMIN, except the OAuth callback, which Google calls in the
admin's browser and which is bound to the admin by a single-use, hashed, short-lived `state` + PKCE.
The refresh token is encrypted at rest and never leaves the Backend (no API returns it, no log prints it).
"""
import hashlib
import json
import logging
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Header
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import credentials, google_api, storage_service
from app.config import settings
from app.identity import require_admin
from app.database import get_db
from app.storage_errors import StorageError, fail, http_error
from app.storage_models import (PROVIDER_GOOGLE_DRIVE, MediaItem, MediaStorageObject, StorageCredential, StorageOAuthState, aware, now)
from app.storage_providers import GoogleDriveStorageProvider

log = logging.getLogger("sarae.storage.admin")
DB = Annotated[Session, Depends(get_db)]
STATE_TTL = timedelta(minutes=10)


def admin(authorization: str | None = Header(None)) -> dict:
    return require_admin(authorization)


Admin = Annotated[dict, Depends(admin)]
router = APIRouter(prefix="/api/storage-admin", tags=["Storage admin"])


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def status_view(db: Session) -> dict:
    row = db.get(StorageCredential, PROVIDER_GOOGLE_DRIVE)
    media = db.execute(select(func.count(MediaItem.id), func.coalesce(func.sum(MediaItem.size_bytes), 0))
                       .where(MediaItem.status == "AVAILABLE")).one()
    pending = db.scalar(select(func.count(MediaItem.id)).where(
        MediaItem.status == "DELETED",
        MediaItem.id.in_(select(MediaStorageObject.media_id).where(
            MediaStorageObject.status.in_(("ACTIVE", "RETIRED"))))))
    return {
        "r2": r2_status(db),
        "upload_provider": settings.sarae_storage_provider,
        "google": {
            "configured": google_api.configured(),
            "status": row.status if row else "NOT_CONNECTED",
            "account_email": row.account_email if row else "",
            "scopes": row.scopes.split() if row and row.scopes else [],
            "root_container_id": row.root_container_id if row else "",
            "last_error_code": row.last_error_code if row else None,
            "connected_at": aware(row.connected_at).isoformat() if row else None,
        },
        "media_available": media[0],
        "media_bytes": int(media[1]),
        "deleted_media_pending_cleanup": pending,
    }


def r2_status(db: Session) -> dict:
    from app.r2_storage import configuration
    row = db.get(StorageCredential, "R2")
    try:
        config, source = configuration(db)
    except StorageError:
        config, source = {}, "DATABASE"
    return {"source": source, "status": row.status if row else "DISCONNECTED",
            "account_id": config.get("account_id", ""), "bucket": config.get("bucket", ""),
            "endpoint": config.get("endpoint", ""), "region": config.get("region", "auto"),
            "configured": bool(config.get("secret_access_key")),
            "last_error_code": row.last_error_code if row else None}


class R2Configuration(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    bucket: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
    access_key_id: str = Field(repr=False)
    secret_access_key: str = Field(repr=False)
    endpoint: str = Field(default="", max_length=300)
    region: str = Field(default="auto", max_length=40)


def _no_r2_uploads(db: Session):
    from app.storage_models import MediaUpload
    if db.scalar(select(func.count()).select_from(MediaUpload).where(
            MediaUpload.provider == "R2", MediaUpload.status == "UPLOADING")):
        raise fail(409, "STORAGE_UPLOAD_ACTIVE", "Hủy hoặc hoàn tất upload R2 trước khi thay cấu hình")


@router.put("/r2/config")
def save_r2(body: R2Configuration, user: Admin, db: DB):
    from app.r2_storage import configuration, validate_config
    try:
        value = validate_config(body.model_dump())
    except StorageError as exc:
        raise http_error(exc)
    _no_r2_uploads(db)
    try:
        previous, _source = configuration(db)
        previous = validate_config(previous)
    except StorageError:
        previous = None
    has_media = db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.provider == "R2"))
    if has_media and (previous is None or any(previous[k] != value[k] for k in ("bucket", "endpoint"))):
        raise fail(409, "STORAGE_ACCOUNT_MISMATCH", "Kho còn metadata video; reset Cloud Media trước khi đổi bucket")
    row = db.get(StorageCredential, "R2")
    if row is None:
        row = StorageCredential(provider="R2")
        db.add(row)
    row.account_id, row.root_container_id = value["account_id"], value["bucket"]
    row.refresh_token_enc = credentials.encrypt(json.dumps(value))
    row.scopes = ""
    row.status, row.last_error_code, row.connected_by = "DISCONNECTED", None, user["id"]
    db.commit()
    return r2_status(db)


@router.post("/r2/check")
def check_r2(_: Admin, db: DB):
    from app.r2_storage import CloudflareR2StorageProvider
    row = db.get(StorageCredential, "R2")
    if row is None:
        row = StorageCredential(provider="R2", scopes="ENV", status="DISCONNECTED")
        db.add(row)
        db.flush()
    try:
        store = CloudflareR2StorageProvider(db, require_connected=False)
        health = store.health()
    except StorageError as exc:
        if row is not None:
            row.status, row.last_error_code = "ERROR", exc.code
            db.commit()
        raise http_error(exc)
    row.status, row.last_error_code = "CONNECTED", None
    db.commit()
    return {"ok": True, **health}


@router.delete("/r2/connection")
def disconnect_r2(_: Admin, db: DB):
    _no_r2_uploads(db)
    row = db.get(StorageCredential, "R2")
    if row is None:
        row = StorageCredential(provider="R2", scopes="ENV")
        db.add(row)
    row.status = "DISCONNECTED"
    db.commit()
    return {"status": "DISCONNECTED"}


@router.get("/status")
def get_status(_: Admin, db: DB):
    return status_view(db)


@router.post("/deleted-media/cleanup")
def cleanup_deleted_media(_: Admin, db: DB):
    """Retry provider deletion for media hidden by the old metadata-only behavior."""
    ids = list(db.scalars(select(MediaItem.id).where(
        MediaItem.status == "DELETED",
        MediaItem.id.in_(select(MediaStorageObject.media_id).where(
            MediaStorageObject.status.in_(("ACTIVE", "RETIRED")))))
        .order_by(MediaItem.deleted_at, MediaItem.id).limit(100)))
    cleaned = 0
    failed = 0
    for media_id in ids:
        media = db.get(MediaItem, media_id)
        try:
            storage_service.delete_media_objects(db, media)
            cleaned += 1
        except StorageError as exc:
            db.rollback()
            failed += 1
            log.warning("cleanup of deleted media %s failed: %s", media_id, exc.code)
    return {"cleaned": cleaned, "failed": failed, "remaining": status_view(db)["deleted_media_pending_cleanup"]}


@router.post("/google/authorize")
def authorize(user: Admin, db: DB):
    if not google_api.configured():
        raise fail(503, "STORAGE_NOT_CONFIGURED", "Thiếu SARAE_GOOGLE_CLIENT_ID / SECRET / REDIRECT_URI trên máy chủ")
    state = secrets.token_urlsafe(32)
    verifier, challenge = google_api.pkce_pair()
    db.query(StorageOAuthState).filter(StorageOAuthState.expires_at < now()).delete()
    db.add(StorageOAuthState(state_hash=_hash(state), admin_id=user["id"], verifier_enc=credentials.encrypt(verifier),
                             expires_at=now() + STATE_TTL))
    db.commit()
    return {"url": google_api.authorize_url(state, challenge), "expires_in": int(STATE_TTL.total_seconds())}


def _back(**query: str) -> RedirectResponse:
    return RedirectResponse(settings.sarae_web_app_url.rstrip("/") + "/#/admin/storage?" + urlencode(query), status_code=303)


@router.get("/google/callback", include_in_schema=False)
def callback(db: DB, state: str | None = None, code: str | None = None, error: str | None = None):
    """Google redirects the admin's browser here. Result goes back to Sarae Web as a code, never as a token."""
    if not state:
        return _back(storage_error="STORAGE_STATE_INVALID")
    row = db.get(StorageOAuthState, _hash(state))
    if row is None or row.used_at is not None:
        log.warning("storage oauth callback with unknown or replayed state")
        return _back(storage_error="STORAGE_STATE_INVALID")
    # Single use, even when the rest fails (atomic: a parallel replay loses).
    claimed = db.execute(update(StorageOAuthState).where(StorageOAuthState.state_hash == row.state_hash,
                                                         StorageOAuthState.used_at.is_(None)).values(used_at=now()))
    db.commit()
    if claimed.rowcount != 1:
        return _back(storage_error="STORAGE_STATE_INVALID")
    if aware(row.expires_at) < now():
        return _back(storage_error="STORAGE_STATE_EXPIRED")
    if error or not code:
        return _back(storage_error="STORAGE_CONSENT_DENIED")
    verifier = credentials.decrypt(row.verifier_enc)
    try:
        grant = google_api.exchange_code(code, verifier or "")
    except StorageError as exc:
        log.warning("storage oauth exchange failed: %s", exc.code)
        return _back(storage_error=exc.code)
    if google_api.SCOPE_FILE not in grant.scopes:
        google_api.revoke(grant.access_token)
        return _back(storage_error="STORAGE_SCOPE_MISSING")
    try:
        user = google_api.Drive(grant.access_token).about().get("user") or {}
    except StorageError as exc:
        return _back(storage_error=exc.code)
    existing = db.get(StorageCredential, PROVIDER_GOOGLE_DRIVE)
    has_media = db.scalar(select(func.count(MediaItem.id)).where(MediaItem.provider == PROVIDER_GOOGLE_DRIVE)) > 0
    if existing and existing.account_id and existing.account_id != user.get("permissionId") and has_media:
        # Existing videos live in the old account's Drive; switching would orphan them. Migrate first.
        google_api.revoke(grant.refresh_token or grant.access_token)
        return _back(storage_error="STORAGE_ACCOUNT_MISMATCH")
    refresh_token = grant.refresh_token or (credentials.decrypt(existing.refresh_token_enc) if existing else None)
    if not refresh_token:
        return _back(storage_error="STORAGE_NO_REFRESH_TOKEN")
    if existing is None or existing.account_id != user.get("permissionId"):
        if existing is not None:
            db.delete(existing)
            db.flush()
        existing = StorageCredential(provider=PROVIDER_GOOGLE_DRIVE)
        db.add(existing)
    existing.account_id = user.get("permissionId", "")
    existing.account_email = user.get("emailAddress", "")
    existing.scopes = " ".join(grant.scopes)
    existing.refresh_token_enc = credentials.encrypt(refresh_token)
    existing.access_token_enc = credentials.encrypt(grant.access_token)
    existing.access_expires_at = now() + timedelta(seconds=grant.expires_in)
    existing.status, existing.last_error_code = "CONNECTED", None
    existing.connected_by, existing.connected_at = row.admin_id, now()
    db.commit()
    try:
        GoogleDriveStorageProvider(db).ensure_root()
    except StorageError as exc:
        existing.last_error_code = exc.code
        db.commit()
        return _back(storage_error=exc.code)
    log.info("system storage connected (provider=%s, account=%s)", PROVIDER_GOOGLE_DRIVE, existing.account_email)
    return _back(storage="connected")


@router.post("/google/check")
def check(_: Admin, db: DB):
    """Live check: refresh the token if needed, read the account and the root folder."""
    try:
        health = GoogleDriveStorageProvider(db).health()
    except StorageError as exc:
        row = db.get(StorageCredential, PROVIDER_GOOGLE_DRIVE)
        if row is not None and row.status == "CONNECTED":
            row.last_error_code = exc.code
            db.commit()
        raise http_error(exc)
    return {"ok": True, **health}


@router.delete("/google/connection")
def disconnect(_: Admin, db: DB):
    """Revoke at Google and forget the tokens. Files stay in Drive; reconnecting the SAME account restores access."""
    row = db.get(StorageCredential, PROVIDER_GOOGLE_DRIVE)
    if row is None:
        raise fail(404, "STORAGE_NOT_CONNECTED", "Chưa kết nối kho lưu trữ")
    token = credentials.decrypt(row.refresh_token_enc) or credentials.decrypt(row.access_token_enc)
    revoked = google_api.revoke(token) if token else True
    row.refresh_token_enc = row.access_token_enc = None
    row.access_expires_at = None
    row.status = "DISCONNECTED"
    db.commit()
    log.info("system storage disconnected (revoked_at_google=%s)", revoked)
    return {"status": "DISCONNECTED", "revoked_at_google": revoked}


class MigrationRequest(BaseModel):
    target_provider: str = Field(min_length=2, max_length=20)
    media_ids: list[str] | None = Field(default=None, max_length=500)
    limit: int = Field(default=20, ge=1, le=500)


@router.post("/migrations")
def migrate(body: MigrationRequest, _: Admin, db: DB):
    """Copy media to another provider one by one: copy → verify SHA-256 → switch mapping. Old objects are kept."""
    from app.storage_providers import provider
    from app.storage_service import migrate_media
    try:
        provider(body.target_provider, db)
    except StorageError as exc:
        raise http_error(exc)
    query = select(MediaItem).where(MediaItem.provider != body.target_provider, MediaItem.status == "AVAILABLE")
    if body.media_ids:
        query = query.where(MediaItem.id.in_(body.media_ids))
    results = [migrate_media(db, media, body.target_provider) for media in db.scalars(query.limit(body.limit)).all()]
    return {"target_provider": body.target_provider,
            "results": [{"media_id": r.media_id, "status": r.status, "error_code": r.error_code} for r in results],
            "migrated": sum(r.status == "MIGRATED" for r in results), "failed": sum(r.status == "FAILED" for r in results)}
