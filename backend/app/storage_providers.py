"""StorageProvider: what the Media Library needs from a blob store, independent of Google Drive / S3 / R2.

The Media Library, Product–Video links, Agent downloads and LIVE only know Sarae media ids. Each media points to a
(provider, provider_file_id); a provider turns those into bytes. Adding S3/R2 = one new subclass + register().

Upload model (shared by every provider): a session opened with the total size, then ordered chunks at byte offsets.
The provider keeps whatever it needs in an opaque `session` string (Drive: resumable URI; S3: upload id + part
ETags), returned updated after each chunk. Sessions authorize writes, so the service stores them encrypted.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import credentials, google_api
from app.config import settings
from app.storage_errors import StorageError
from app.storage_models import (PROVIDER_GOOGLE_DRIVE, StorageCredential, StorageOwnerContainer, aware, now)

log = logging.getLogger("sarae.storage")
T = TypeVar("T")


@dataclass
class StoredObject:
    object_id: str
    size_bytes: int
    md5: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None


@dataclass
class UploadProgress:
    session: str  # possibly updated opaque provider state
    received: int  # bytes persisted by the provider
    stored: StoredObject | None = None  # set once the object is complete


class ReadStream:
    """Bytes of an object (optionally a Range); iterate once, or close() when not consumed."""

    def __init__(self, status: int, headers: dict[str, str], chunks: Iterator[bytes], close: Callable[[], None]):
        self.status = status
        self.headers = headers
        self._chunks = chunks
        self._close = close

    def iter(self) -> Iterator[bytes]:
        try:
            yield from self._chunks
        finally:
            self._close()

    def close(self):
        self._close()


class StorageProvider(ABC):
    name: str

    def __init__(self, db: Session):
        self.db = db

    # -- per-owner organisation (a folder / key prefix; access control is NOT based on it) --
    def owner_container(self, owner_id: str) -> str:
        row = self.db.get(StorageOwnerContainer, (owner_id, self.name))
        if row is not None:
            return row.container_id
        container = self.create_container(owner_id)
        self.db.add(StorageOwnerContainer(owner_id=owner_id, provider=self.name, container_id=container))
        try:
            self.db.commit()
        except IntegrityError:  # another request created it first; keep theirs
            self.db.rollback()
            container = self.db.get(StorageOwnerContainer, (owner_id, self.name)).container_id
        return container

    @abstractmethod
    def create_container(self, owner_id: str) -> str: ...

    @abstractmethod
    def begin_upload(self, owner_id: str, media_id: str, filename: str, mime_type: str, size: int) -> str: ...

    @abstractmethod
    def upload_chunk(self, session: str, start: int, data: bytes | Iterable[bytes], length: int, total: int) -> UploadProgress: ...

    @abstractmethod
    def upload_status(self, session: str, total: int) -> UploadProgress: ...

    @abstractmethod
    def cancel_upload(self, session: str): ...

    @abstractmethod
    def open_read(self, object_id: str, range_header: str | None = None) -> ReadStream: ...

    @abstractmethod
    def delete(self, object_id: str): ...

    def describe(self, object_id: str) -> StoredObject | None:
        """Latest metadata (duration/size may appear after provider-side processing)."""
        return None

    def thumbnail(self, object_id: str) -> tuple[bytes, str] | None:
        return None

    def put(self, owner_id: str, media_id: str, filename: str, mime_type: str, size: int,
            chunks: Iterable[bytes], chunk_size: int = 8 * 1024 * 1024) -> StoredObject:
        """Write a whole object from a stream (used by provider migration). Never holds more than one chunk."""
        session = self.begin_upload(owner_id, media_id, filename, mime_type, size)
        offset = 0
        buffer = b""
        try:
            for piece in chunks:
                buffer += piece
                while len(buffer) >= chunk_size and offset + chunk_size < size:
                    progress = self.upload_chunk(session, offset, buffer[:chunk_size], chunk_size, size)
                    session, offset, buffer = progress.session, offset + chunk_size, buffer[chunk_size:]
            if offset + len(buffer) != size:
                raise StorageError("STORAGE_SIZE_MISMATCH", "Dung lượng dữ liệu không khớp khi sao chép", 500)
            progress = self.upload_chunk(session, offset, buffer, len(buffer), size)
        except BaseException:
            self.cancel_upload(session)
            raise
        if progress.stored is None:
            raise StorageError("STORAGE_PROVIDER_ERROR", "Kho lưu trữ chưa xác nhận file sau khi sao chép", 502)
        return progress.stored

    def health(self) -> dict:
        return {"provider": self.name}


# ----- Google Drive (system account, drive.file) --------------------------------------------------

def _stored(file: dict) -> StoredObject:
    video = file.get("videoMediaMetadata") or {}
    millis = video.get("durationMillis")
    return StoredObject(object_id=file["id"], size_bytes=int(file.get("size") or 0), md5=file.get("md5Checksum"),
                        duration_seconds=int(millis) / 1000 if millis else None,
                        width=video.get("width"), height=video.get("height"))


class GoogleDriveStorageProvider(StorageProvider):
    """Files live in one Drive owned by the system account: <root>/owner-<owner_id>/<media_id>.<ext>."""
    name = PROVIDER_GOOGLE_DRIVE

    def credential(self) -> StorageCredential:
        if not google_api.configured():
            raise StorageError("STORAGE_NOT_CONFIGURED", "Kho lưu trữ chưa được cấu hình trên máy chủ")
        row = self.db.get(StorageCredential, self.name)
        if row is None or row.status != "CONNECTED":
            code = "STORAGE_TOKEN_REVOKED" if row is not None and row.status == "REVOKED" else "STORAGE_NOT_CONNECTED"
            raise StorageError(code, "Kho lưu trữ hệ thống chưa sẵn sàng; vui lòng thử lại sau hoặc liên hệ quản trị")
        return row

    def access_token(self, force_refresh: bool = False) -> str:
        row = self.credential()
        cached = credentials.decrypt(row.access_token_enc)
        if cached and not force_refresh and row.access_expires_at and aware(row.access_expires_at) > now() + timedelta(seconds=60):
            return cached
        refresh_token = credentials.decrypt(row.refresh_token_enc)
        if not refresh_token:
            row.status, row.last_error_code = "ERROR", "STORAGE_TOKEN_UNREADABLE"
            self.db.commit()
            raise StorageError("STORAGE_NOT_CONNECTED", "Kho lưu trữ hệ thống cần được kết nối lại")
        try:
            grant = google_api.refresh(refresh_token)
        except google_api.GoogleError as exc:
            if exc.code == "STORAGE_TOKEN_REVOKED":
                row.status, row.last_error_code = "REVOKED", exc.code
                row.access_token_enc = row.refresh_token_enc = None
                self.db.commit()
                log.error("storage credential revoked by Google; admin must reconnect")
            raise
        row.access_token_enc = credentials.encrypt(grant.access_token)
        row.access_expires_at = now() + timedelta(seconds=grant.expires_in)
        if grant.refresh_token:
            row.refresh_token_enc = credentials.encrypt(grant.refresh_token)
        row.last_error_code = None
        self.db.commit()
        return grant.access_token

    def _call(self, action: Callable[[google_api.Drive], T]) -> T:
        try:
            return action(google_api.Drive(self.access_token()))
        except google_api.GoogleError as exc:
            if exc.code != "STORAGE_TOKEN_INVALID":
                raise
        return action(google_api.Drive(self.access_token(force_refresh=True)))

    def ensure_root(self) -> str:
        row = self.credential()
        if row.root_container_id:
            return row.root_container_id
        folder = self._call(lambda d: d.find_folder("sarae_role", "root")) or self._call(
            lambda d: d.create_folder(settings.sarae_google_root_folder_name, None, {"sarae_role": "root"}))
        row.root_container_id = folder["id"]
        self.db.commit()
        return folder["id"]

    def create_container(self, owner_id: str) -> str:
        root = self.ensure_root()
        props = google_api.app_properties(sarae_owner=owner_id)
        folder = self._call(lambda d: d.find_folder("sarae_owner", props["sarae_owner"], root)) or self._call(
            lambda d: d.create_folder(f"owner-{owner_id}", root, props))
        return folder["id"]

    def begin_upload(self, owner_id, media_id, filename, mime_type, size):
        parent = self.owner_container(owner_id)
        ext = os.path.splitext(filename)[1].lower()[:10]
        props = google_api.app_properties(sarae_media=media_id, sarae_owner=owner_id)
        return self._call(lambda d: d.begin_upload(f"{media_id}{ext}", parent, mime_type, size, props))

    def upload_chunk(self, session, start, data, length, total):
        # The session URI carries its own authorization; no bearer token needed.
        state = google_api.Drive("").upload_chunk(session, start, data, length, total)
        return UploadProgress(session, state.received, _stored(state.file) if state.file else None)

    def upload_status(self, session, total):
        state = google_api.Drive("").upload_status(session, total)
        return UploadProgress(session, state.received, _stored(state.file) if state.file else None)

    def cancel_upload(self, session):
        google_api.Drive("").cancel_upload(session)

    def open_read(self, object_id, range_header=None):
        stream = self._call(lambda d: d.open_media(object_id, range_header))
        return ReadStream(stream.status, stream.headers(), stream.iter(), stream.close)

    def delete(self, object_id):
        self._call(lambda d: d.delete_file(object_id))

    def describe(self, object_id):
        return _stored(self._call(lambda d: d.get_file(object_id)))

    def thumbnail(self, object_id):
        return self._call(lambda d: d.thumbnail(object_id))

    def health(self):
        about = self._call(lambda d: d.about())
        quota = about.get("storageQuota") or {}
        return {"provider": self.name, "account_email": (about.get("user") or {}).get("emailAddress", ""),
                "root_container_id": self.ensure_root(),
                "quota_limit_bytes": int(quota["limit"]) if quota.get("limit") else None,
                "quota_usage_bytes": int(quota["usage"]) if quota.get("usage") else None}


# ----- registry ---------------------------------------------------------------------------------

_FACTORIES: dict[str, Callable[[Session], StorageProvider]] = {PROVIDER_GOOGLE_DRIVE: GoogleDriveStorageProvider}


def register(name: str, factory: Callable[[Session], StorageProvider]):
    """Add a provider (S3/R2 later; tests register an in-memory one)."""
    _FACTORIES[name] = factory


def unregister(name: str):
    _FACTORIES.pop(name, None)


def provider(name: str, db: Session) -> StorageProvider:
    if name == "R2":
        from app.r2_storage import CloudflareR2StorageProvider
        return CloudflareR2StorageProvider(db)
    factory = _FACTORIES.get(name)
    if factory is None:
        raise StorageError("STORAGE_PROVIDER_UNKNOWN", f"Kho lưu trữ {name} chưa được hỗ trợ")
    return factory(db)


def upload_provider(db: Session) -> StorageProvider:
    """Provider for NEW uploads (setting); existing media keep reading from their own provider."""
    return provider(settings.sarae_storage_provider, db)
