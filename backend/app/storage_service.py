"""StorageService: uploads, reads and provider migration for the Media Library, independent of the provider.

Ownership is enforced here and in the routers (owner_id on every row); the storage layout (per-owner folders) is
organisation only. Upload chunks are bounded in memory (≤ MAX_CHUNK); whole videos are never buffered.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import credentials
from app.config import settings
from app.storage_errors import StorageError, fail
from app.storage_models import MediaItem, MediaStorageObject, MediaUpload, new_id, now
from app.storage_providers import ReadStream, StoredObject, provider, upload_provider

log = logging.getLogger("sarae.storage.service")

VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v"}
CHUNK_SIZE = 8 * 1024 * 1024  # recommended client chunk
CHUNK_ALIGN = 256 * 1024  # Google resumable uploads need multiples of 256 KiB (except the last chunk)
MAX_CHUNK = 32 * 1024 * 1024
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_RANGE = re.compile(r"^bytes=\d*-\d*$")

# Running SHA-256 + MD5 per upload while chunks arrive in order. Lost on restart: the media then keeps no sha256
# (computed on the next provider copy) and the provider's md5 is trusted.
_hashers: dict[str, tuple[int, "hashlib._Hash", "hashlib._Hash"]] = {}
_hash_lock = threading.Lock()


# ----- uploads ---------------------------------------------------------------------------------

def _validate(filename: str, size: int) -> tuple[str, str]:
    name = os.path.basename(filename.replace("\\", "/")).strip()
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in VIDEO_TYPES:
        raise fail(422, "MEDIA_TYPE_NOT_ALLOWED", "Chỉ hỗ trợ video MP4, MOV hoặc M4V")
    if size <= 0:
        raise fail(422, "MEDIA_EMPTY", "File rỗng")
    if size > settings.sarae_media_max_upload_bytes:
        raise fail(413, "MEDIA_TOO_LARGE", f"Video vượt giới hạn {settings.sarae_media_max_upload_bytes // 1024 ** 2} MB")
    return name[:255], VIDEO_TYPES[ext]


def _check_quota(db: Session, owner_id: str, size: int):
    quota = settings.sarae_media_owner_quota_bytes
    if quota <= 0:
        return
    used = db.scalar(select(func.coalesce(func.sum(MediaItem.size_bytes), 0)).where(
        MediaItem.owner_id == owner_id, MediaItem.status == "AVAILABLE")) or 0
    pending = db.scalar(select(func.coalesce(func.sum(MediaUpload.size_bytes), 0)).where(
        MediaUpload.owner_id == owner_id, MediaUpload.status == "UPLOADING")) or 0
    if used + pending + size > quota:
        raise fail(507, "MEDIA_QUOTA_EXCEEDED", "Đã hết dung lượng lưu trữ của tài khoản")


def create_upload(db: Session, owner_id: str, client_upload_id: str, filename: str, size: int) -> MediaUpload:
    existing = db.scalar(select(MediaUpload).where(MediaUpload.owner_id == owner_id,
                                                   MediaUpload.client_upload_id == client_upload_id))
    if existing is not None:
        if existing.size_bytes != size:
            raise fail(409, "UPLOAD_ID_REUSED", "Mã upload đã được dùng cho file khác")
        return existing  # retried create → same upload, no second file
    name, mime = _validate(filename, size)
    _check_quota(db, owner_id, size)
    store = upload_provider(db)
    media_id = new_id()
    session = store.begin_upload(owner_id, media_id, name, mime, size)
    row = MediaUpload(owner_id=owner_id, client_upload_id=client_upload_id, provider=store.name, filename=name,
                      mime_type=mime, size_bytes=size, session_enc=credentials.encrypt(session), media_id=media_id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # the same client id raced in parallel: keep the first, drop this session
        db.rollback()
        store.cancel_upload(session)
        return db.scalar(select(MediaUpload).where(MediaUpload.owner_id == owner_id,
                                                   MediaUpload.client_upload_id == client_upload_id))
    with _hash_lock:
        _hashers[row.id] = (0, hashlib.sha256(), hashlib.md5())
    log.info("upload created id=%s owner=%s size=%s provider=%s", row.id, owner_id, size, store.name)
    return row


def owned_upload(db: Session, owner_id: str, upload_id: str) -> MediaUpload:
    row = db.get(MediaUpload, upload_id)
    if row is None or row.owner_id != owner_id:
        raise fail(404, "UPLOAD_NOT_FOUND", "Không tìm thấy phiên upload")
    return row


def parse_content_range(value: str | None, total: int) -> tuple[int, int]:
    match = _CONTENT_RANGE.match((value or "").strip())
    if not match:
        raise fail(400, "UPLOAD_RANGE_INVALID", "Thiếu hoặc sai Content-Range (bytes start-end/total)")
    start, end, declared = map(int, match.groups())
    length = end - start + 1
    if declared != total or end < start or end >= total:
        raise fail(400, "UPLOAD_RANGE_INVALID", "Content-Range không khớp kích thước file")
    if length > MAX_CHUNK:
        raise fail(413, "UPLOAD_CHUNK_TOO_LARGE", f"Mỗi phần tối đa {MAX_CHUNK // 1024 ** 2} MB")
    if end + 1 != total and length % CHUNK_ALIGN:
        raise fail(400, "UPLOAD_CHUNK_ALIGN", "Kích thước mỗi phần (trừ phần cuối) phải là bội số của 256 KB")
    return start, end


def looks_like_video(head: bytes) -> bool:
    """MP4/MOV/M4V are ISO base media files: an 'ftyp' (or legacy QuickTime atom) box near the start."""
    return len(head) >= 12 and (head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"))


def put_chunk(db: Session, row: MediaUpload, start: int, end: int, data: bytes) -> MediaUpload:
    if row.status != "UPLOADING":
        raise fail(409, "UPLOAD_NOT_ACTIVE", "Phiên upload đã kết thúc")
    if len(data) != end - start + 1:
        raise fail(400, "UPLOAD_LENGTH_MISMATCH", "Dữ liệu nhận được không khớp Content-Range")
    if end < row.received_bytes:
        return row  # a retried chunk that is already stored: nothing to resend
    if start != row.received_bytes:
        raise fail(409, "UPLOAD_OFFSET_MISMATCH", f"Cần gửi tiếp từ byte {row.received_bytes}")
    if start == 0 and not looks_like_video(data[:64]):
        cancel_upload(db, row, code="MEDIA_CONTENT_INVALID")
        raise fail(422, "MEDIA_CONTENT_INVALID", "Nội dung không phải video MP4/MOV hợp lệ")
    with _hash_lock:
        offset, sha, md5 = _hashers.get(row.id, (-1, None, None))
        if sha is not None and offset == start:
            sha.update(data)
            md5.update(data)
            _hashers[row.id] = (end + 1, sha, md5)
        else:
            _hashers.pop(row.id, None)
    store = provider(row.provider, db)
    session = credentials.decrypt(row.session_enc)
    if not session:
        raise fail(409, "UPLOAD_SESSION_LOST", "Phiên upload không còn hợp lệ; hãy upload lại")
    try:
        progress = store.upload_chunk(session, start, data, len(data), row.size_bytes)
    except StorageError as exc:
        _hashers.pop(row.id, None)  # bytes may be partially stored; rely on the provider offset from now on
        if exc.code == "STORAGE_UPLOAD_EXPIRED":
            row.status, row.error_code, row.session_enc = "FAILED", exc.code, None
            db.commit()
        raise
    return _apply(db, row, progress.received, progress.stored, progress.session)


def refresh_upload(db: Session, row: MediaUpload) -> MediaUpload:
    """Ask the provider how much it really has (resume after a lost response or a restart)."""
    if row.status != "UPLOADING":
        return row
    session = credentials.decrypt(row.session_enc)
    if not session:
        return row
    try:
        progress = provider(row.provider, db).upload_status(session, row.size_bytes)
    except StorageError as exc:
        if exc.code == "STORAGE_UPLOAD_EXPIRED":
            row.status, row.error_code, row.session_enc = "FAILED", exc.code, None
            db.commit()
            return row
        raise
    if progress.received != row.received_bytes:
        _hashers.pop(row.id, None)
    return _apply(db, row, progress.received, progress.stored, progress.session)


def _apply(db: Session, row: MediaUpload, received: int, stored: StoredObject | None, session: str) -> MediaUpload:
    row.received_bytes = received
    row.session_enc = credentials.encrypt(session)
    if stored is None:
        db.commit()
        return row
    if stored.size_bytes and stored.size_bytes != row.size_bytes:
        log.error("upload %s size mismatch: expected %s got %s", row.id, row.size_bytes, stored.size_bytes)
        try:
            provider(row.provider, db).delete(stored.object_id)
        except StorageError:
            log.warning("could not delete mismatched object of upload %s", row.id)
        row.status, row.error_code, row.session_enc = "FAILED", "STORAGE_SIZE_MISMATCH", None
        db.commit()
        raise fail(422, "STORAGE_SIZE_MISMATCH", "Dung lượng file trên kho không khớp; hãy upload lại")
    with _hash_lock:
        offset, sha, md5 = _hashers.pop(row.id, (-1, None, None))
    complete = sha is not None and offset == row.size_bytes
    if complete and stored.md5 and md5.hexdigest() != stored.md5:
        log.error("upload %s md5 mismatch between Backend and provider", row.id)
        try:
            provider(row.provider, db).delete(stored.object_id)
        except StorageError:
            log.warning("could not delete corrupted object of upload %s", row.id)
        row.status, row.error_code, row.session_enc = "FAILED", "STORAGE_CHECKSUM_MISMATCH", None
        db.commit()
        raise fail(422, "STORAGE_CHECKSUM_MISMATCH", "File trên kho không khớp dữ liệu đã gửi; hãy upload lại")
    sha256 = sha.hexdigest() if complete else None
    media = db.get(MediaItem, row.media_id)
    if media is None:  # idempotent: a repeated completion keeps the first media row
        media = MediaItem(id=row.media_id, owner_id=row.owner_id, provider=row.provider, provider_file_id=stored.object_id,
                          filename=row.filename, display_name=os.path.splitext(row.filename)[0][:200],
                          mime_type=row.mime_type, size_bytes=row.size_bytes, sha256=sha256, md5=stored.md5,
                          duration_seconds=stored.duration_seconds, width=stored.width, height=stored.height)
        db.add(media)
        db.add(MediaStorageObject(media_id=media.id, owner_id=row.owner_id, provider=row.provider,
                                  provider_file_id=stored.object_id, sha256=sha256, size_bytes=row.size_bytes))
    row.status, row.session_enc, row.received_bytes = "COMPLETED", None, row.size_bytes
    db.commit()
    log.info("upload %s completed → media %s", row.id, media.id)
    return row


def cancel_upload(db: Session, row: MediaUpload, code: str | None = None) -> MediaUpload:
    if row.status != "UPLOADING":
        return row
    session = credentials.decrypt(row.session_enc)
    if session:
        try:
            provider(row.provider, db).cancel_upload(session)
        except StorageError:
            log.warning("provider cancel failed for upload %s (the session expires on its own)", row.id)
    _hashers.pop(row.id, None)
    row.status, row.session_enc, row.error_code = ("FAILED" if code else "CANCELLED"), None, code
    db.commit()
    return row


# ----- reads --------------------------------------------------------------------------------

def open_media(db: Session, media: MediaItem, range_header: str | None) -> ReadStream:
    if range_header and not _RANGE.match(range_header.strip()):
        range_header = None  # multi-range or malformed: serve the whole file
    return provider(media.provider, db).open_read(media.provider_file_id, range_header)


def refresh_metadata(db: Session, media: MediaItem) -> MediaItem:
    """Fill duration/size reported by the provider after processing; never touches Sarae fields."""
    if media.duration_seconds is not None:
        return media
    info = provider(media.provider, db).describe(media.provider_file_id)
    if info is not None and info.duration_seconds is not None:
        media.duration_seconds, media.width, media.height = info.duration_seconds, info.width, info.height
        db.commit()
    return media


# ----- provider migration (Drive → S3/R2 later) ------------------------------------------------

@dataclass
class MigrationResult:
    media_id: str
    status: str  # MIGRATED / SKIPPED / FAILED
    error_code: str | None = None


def _hashing(chunks: Iterator[bytes], hasher) -> Iterator[bytes]:
    for chunk in chunks:
        hasher.update(chunk)
        yield chunk


def migrate_media(db: Session, media: MediaItem, target: str) -> MigrationResult:
    """Copy one media to `target`, verify SHA-256 by reading the copy back, then switch the mapping.

    The source object is kept (RETIRED) so a rollback is a mapping change; nothing is deleted here.
    """
    if media.provider == target:
        return MigrationResult(media.id, "SKIPPED")
    source, dest = provider(media.provider, db), provider(target, db)
    copy = MediaStorageObject(media_id=media.id, owner_id=media.owner_id, provider=target,
                              provider_file_id=f"pending-{new_id()}", size_bytes=media.size_bytes, status="COPYING")
    db.add(copy)
    db.commit()
    stored = None
    try:
        source_hash = hashlib.sha256()
        stored = dest.put(media.owner_id, media.id, media.filename, media.mime_type, media.size_bytes,
                          _hashing(source.open_read(media.provider_file_id).iter(), source_hash))
        if stored.size_bytes and stored.size_bytes != media.size_bytes:
            raise StorageError("STORAGE_SIZE_MISMATCH", "Dung lượng bản sao không khớp")
        if media.sha256 and media.sha256 != source_hash.hexdigest():
            raise StorageError("STORAGE_CHECKSUM_MISMATCH", "File nguồn không còn khớp checksum đã lưu")
        copy_hash = hashlib.sha256()
        for chunk in dest.open_read(stored.object_id).iter():
            copy_hash.update(chunk)
        if copy_hash.hexdigest() != source_hash.hexdigest():
            raise StorageError("STORAGE_CHECKSUM_MISMATCH", "Checksum bản sao khác file nguồn")
    except StorageError as exc:
        if stored is not None:
            try:
                dest.delete(stored.object_id)
            except StorageError:
                log.warning("could not delete failed copy of media %s", media.id)
        copy.status, copy.error_code = "FAILED", exc.code
        db.commit()
        log.error("migration of media %s to %s failed: %s", media.id, target, exc.code)
        return MigrationResult(media.id, "FAILED", exc.code)
    for old in db.scalars(select(MediaStorageObject).where(MediaStorageObject.media_id == media.id,
                                                           MediaStorageObject.status == "ACTIVE")):
        old.status, old.retired_at = "RETIRED", now()
    copy.provider_file_id, copy.sha256, copy.status = stored.object_id, source_hash.hexdigest(), "ACTIVE"
    media.provider, media.provider_file_id, media.md5 = target, stored.object_id, stored.md5
    media.sha256 = media.sha256 or source_hash.hexdigest()
    db.commit()
    log.info("media %s migrated to %s", media.id, target)
    return MigrationResult(media.id, "MIGRATED")
