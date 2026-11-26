"""Transport-independent local studio operations; no cloud or native runtime imports."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from sarae_agent import __version__

from .models import StudioError, identifier, product_fields, timestamp
from .storage import StudioStorage, data_root


# Validate both the suffix and FFprobe's actual demuxer. A renamed image or an
# audio-only MP4 is not a video. No playlists, URLs or external resource formats.
VIDEO_FORMATS = {
    ".mp4": ({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, "video/mp4"),
    ".m4v": ({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, "video/mp4"),
    ".mov": ({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, "video/quicktime"),
    ".3gp": ({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, "video/3gpp"),
    ".3g2": ({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}, "video/3gpp2"),
    ".mkv": ({"matroska", "webm"}, "video/x-matroska"),
    ".webm": ({"matroska", "webm"}, "video/webm"),
    ".avi": ({"avi"}, "video/x-msvideo"),
    ".wmv": ({"asf"}, "video/x-ms-wmv"),
    ".flv": ({"flv"}, "video/x-flv"),
    ".mpeg": ({"mpeg", "mpegvideo"}, "video/mpeg"),
    ".mpg": ({"mpeg", "mpegvideo"}, "video/mpeg"),
    ".ts": ({"mpegts"}, "video/mp2t"),
    ".mts": ({"mpegts"}, "video/mp2t"),
    ".m2ts": ({"mpegts"}, "video/mp2t"),
    ".ogv": ({"ogg"}, "video/ogg"),
}
COPY_CHUNK_BYTES = 1024 * 1024
log = logging.getLogger(__name__)


def probe_video(path: Path) -> None:
    """Check the copied bytes, using a bounded FFprobe query and a 15s deadline."""
    executable = None
    # Native packaging may place FFprobe beside the sidecar or in its
    # PyInstaller extraction root. Source/development runs use PATH.
    if getattr(sys, "frozen", False):
        folders = [Path(sys.executable).parent]
        if getattr(sys, "_MEIPASS", None):
            folders.insert(0, Path(sys._MEIPASS))
        for folder in folders:
            candidate = folder / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
            if candidate.is_file():
                executable = str(candidate)
                break
    executable = executable or shutil.which("ffprobe")
    if not executable:
        raise StudioError("PROBE_UNAVAILABLE", "FFprobe is required to verify imported videos. Install FFmpeg first.")
    try:
        allowed, _ = VIDEO_FORMATS[path.suffix.lower()]
        # stdout goes to a temporary file: malformed media cannot make Python
        # accumulate arbitrary probe output or stderr in memory.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [executable, "-v", "error", "-protocol_whitelist", "file,pipe",
                 "-format_whitelist", ",".join(sorted(allowed)), "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type,width,height:stream_disposition=attached_pic:format=format_name",
                 "-of", "json", str(path)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                timeout=15, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output.seek(0)
            encoded = output.read(65537)
        if result.returncode or len(encoded) > 65536:
            raise ValueError("FFprobe rejected this file")
        payload = json.loads(encoded)
        streams = payload.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"
                      and int(stream.get("width", 0)) > 0 and int(stream.get("height", 0)) > 0
                      and not stream.get("disposition", {}).get("attached_pic")), None)
        formats = set(payload.get("format", {}).get("format_name", "").split(","))
        if video is None or not formats.intersection(allowed):
            raise ValueError("No supported video stream/container")
    except subprocess.TimeoutExpired as exc:
        raise StudioError("INVALID_MEDIA", "Video verification timed out.") from exc
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise StudioError("INVALID_MEDIA", "The file is not a supported video or its container does not match its extension.") from exc


class StudioService:
    """One local SQLite owner, callable from either JSON Lines IPC or HTTP.

    The lock serializes one connection across HTTP worker threads. An import also
    holds a SQLite write transaction while staging, so recovery from another
    service cannot remove an import that is still in progress.
    """

    def __init__(self, data_dir: Path | None = None):
        self._lock = threading.RLock()
        self._closed = False
        self._storage: StudioStorage | None = None
        try:
            self.data_dir = data_root(data_dir)
            self.media_dir = self.data_dir / "media"
            self.media_dir.mkdir(exist_ok=True)
            self._managed_dir()
            self._storage = StudioStorage(self.data_dir)
            self.database_path = self._storage.database_path
            with self._lock:
                self._recover_imports()
        except (OSError, sqlite3.Error) as exc:
            if self._storage is not None:
                self._storage.close()
            raise StudioError("STORAGE_ERROR", "Cannot open the local studio data directory or database.") from exc
        except BaseException:
            if self._storage is not None:
                self._storage.close()
            raise

    def dispatch(self, method: str, params: dict) -> dict:
        with self._lock:
            self._ensure_open()
            if not isinstance(method, str) or not isinstance(params, dict):
                raise StudioError("VALIDATION_ERROR", "method must be text and params must be an object.")
            handlers = {
                "studio.status": (self._status, set()),
                "studio.products.list": (self._list_products, set()),
                "studio.products.create": (self._create_product, {"name", "description", "price"}),
                "studio.products.update": (self._update_product, {"id", "name", "description", "price"}),
                "studio.products.delete": (self._delete_product, {"id"}),
                "studio.media.list": (self._list_media, {"product_id"}),
                "studio.media.import": (self._import_media, {"source_path", "product_id"}),
                "studio.media.delete": (self._delete_media, {"id"}),
            }
            if method not in handlers:
                raise StudioError("METHOD_NOT_FOUND", f"Unknown studio method: {method}")
            handler, allowed = handlers[method]
            unknown = params.keys() - allowed
            if unknown:
                raise StudioError("VALIDATION_ERROR", "Unexpected parameters.",
                                  {"fields": sorted(str(field) for field in unknown)})
            try:
                return handler(params)
            except (OSError, sqlite3.Error) as exc:
                raise StudioError("STORAGE_ERROR", "The local storage operation failed. Check disk space and permissions.") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise StudioError("STORAGE_ERROR", "The studio service is closed.")

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._storage.close()
                self._closed = True

    def _status(self, params: dict) -> dict:
        return {"version": __version__, "data_dir": str(self.data_dir), "database_path": str(self.database_path),
                "capabilities": {"products": True, "managed_media": True,
                                 "native_dialog": False, "local_file_reference": False, "obs": False, "ai": False}}

    def _list_products(self, params: dict) -> dict:
        rows = self._storage.connection.execute("SELECT * FROM products ORDER BY created_at DESC, id").fetchall()
        return {"products": [dict(row) for row in rows]}

    def _product(self, product_id: str):
        row = self._storage.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "Product not found.", {"id": product_id})
        return row

    def _create_product(self, params: dict) -> dict:
        fields = product_fields(params)
        product_id, now = str(uuid4()), timestamp()
        with self._storage.transaction() as connection:
            connection.execute("""INSERT INTO products(id, name, description, price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                               (product_id, fields["name"], fields["description"], fields["price"], now, now))
            product = dict(self._product(product_id))
        return {"product": product}

    def _update_product(self, params: dict) -> dict:
        product_id = identifier(params.get("id"))
        fields = product_fields(params, partial=True)
        if not fields:
            raise StudioError("VALIDATION_ERROR", "Provide at least one product field to update.")
        fields["updated_at"] = timestamp()
        with self._storage.transaction() as connection:
            self._product(product_id)
            connection.execute("UPDATE products SET " + ", ".join(f"{key} = ?" for key in fields) + " WHERE id = ?",
                               (*fields.values(), product_id))
            product = dict(self._product(product_id))
        return {"product": product}

    def _delete_product(self, params: dict) -> dict:
        product_id = identifier(params.get("id"))
        with self._storage.transaction() as connection:
            self._product(product_id)
            # Media remains usable in the library when its product is deleted.
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return {"deleted": True}

    def _media(self, media_id: str):
        row = self._storage.connection.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "Media not found.", {"id": media_id})
        return row

    def _managed_dir(self) -> Path:
        # Recheck on every operation: a library directory moved/replaced since
        # startup must not redirect resolver or deletion outside the data root.
        try:
            valid = self.media_dir.resolve() == self.media_dir and self.media_dir.is_dir()
        except (OSError, RuntimeError) as exc:
            raise StudioError("UNSAFE_PATH", "Cannot validate the managed media directory.") from exc
        if not valid:
            raise StudioError("UNSAFE_PATH", "The managed media directory is missing or points outside the library.")
        return self.media_dir

    def _media_path(self, row) -> Path:
        relative = row["relative_path"]
        if not isinstance(relative, str):
            raise StudioError("UNSAFE_PATH", "Invalid managed media path.")
        suffix = Path(relative).suffix.lower()
        if (suffix not in VIDEO_FORMATS or relative != row["id"] + suffix
                or row["storage_mode"] != "managed"):
            raise StudioError("UNSAFE_PATH", "Invalid managed media path.")
        target = self._managed_dir() / relative
        try:
            valid = target.resolve() == target and not target.is_symlink()
        except (OSError, RuntimeError) as exc:
            raise StudioError("UNSAFE_PATH", "Cannot validate the managed media path.") from exc
        if not valid:
            raise StudioError("UNSAFE_PATH", "Managed media must not point outside the library.")
        return target

    def resolve_media(self, media_id: str) -> Path:
        with self._lock:
            self._ensure_open()
            try:
                row = self._media(identifier(media_id))
                target = self._media_path(row)
                try:
                    info = target.stat()
                except FileNotFoundError as exc:
                    raise StudioError("MEDIA_MISSING", "The managed video file is missing.", {"id": media_id}) from exc
                if not stat.S_ISREG(info.st_mode) or info.st_size != row["size_bytes"]:
                    raise StudioError("MEDIA_MISSING", "The managed video file is missing or has changed.", {"id": media_id})
                return target
            except (OSError, sqlite3.Error) as exc:
                raise StudioError("STORAGE_ERROR", "Cannot resolve the managed video file.") from exc

    def _serialize_media(self, row) -> dict:
        media = {key: row[key] for key in ("id", "product_id", "original_name", "size_bytes", "sha256",
                                          "storage_mode", "mime_type", "created_at")}
        try:
            self.resolve_media(row["id"])
            media["status"] = "ready"
        except StudioError as exc:
            if exc.code not in {"MEDIA_MISSING", "UNSAFE_PATH", "STORAGE_ERROR"}:
                raise
            media["status"] = "missing"
        return media

    def _list_media(self, params: dict) -> dict:
        product_id = params.get("product_id")
        if product_id is None:
            rows = self._storage.connection.execute("SELECT * FROM media ORDER BY created_at DESC, id").fetchall()
        else:
            product_id = identifier(product_id, "product_id")
            self._product(product_id)
            rows = self._storage.connection.execute("SELECT * FROM media WHERE product_id = ? ORDER BY created_at DESC, id",
                                                    (product_id,)).fetchall()
        return {"media": [self._serialize_media(row) for row in rows]}

    def _import_media(self, params: dict) -> dict:
        raw_source = params.get("source_path")
        if not isinstance(raw_source, str) or not raw_source or "\x00" in raw_source:
            raise StudioError("VALIDATION_ERROR", "source_path must be an absolute local file path.")
        source = Path(raw_source)
        if not source.is_absolute():
            raise StudioError("VALIDATION_ERROR", "source_path must be an absolute local file path.")
        suffix = source.suffix.lower()
        if suffix not in VIDEO_FORMATS:
            raise StudioError("INVALID_MEDIA", "Unsupported video extension.", {"extensions": sorted(VIDEO_FORMATS)})
        product_id = params.get("product_id")
        if product_id is not None:
            product_id = identifier(product_id, "product_id")
        if not source.is_file():
            raise StudioError("INVALID_MEDIA", "The source video does not exist or is not a regular file.")
        media_id, temporary, published = str(uuid4()), None, None
        try:
            with self._storage.transaction() as connection:
                if product_id is not None:
                    self._product(product_id)
                folder = self._managed_dir()
                target = folder / (media_id + suffix)
                digest, size = hashlib.sha256(), 0
                # Stage in the same directory/filesystem as the final file.
                with source.open("rb") as incoming, tempfile.NamedTemporaryFile(
                        mode="wb", prefix=".import-", suffix=".partial" + suffix,
                        dir=folder, delete=False) as outgoing:
                    temporary = Path(outgoing.name)
                    before = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise StudioError("INVALID_MEDIA", "The source must be a regular video file.")
                    while chunk := incoming.read(COPY_CHUNK_BYTES):
                        outgoing.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    after = os.fstat(incoming.fileno())
                    if (size != before.st_size or before.st_size != after.st_size
                            or before.st_mtime_ns != after.st_mtime_ns):
                        raise StudioError("INVALID_MEDIA", "The source file changed during import. Please try again.")
                    if size == 0:
                        raise StudioError("INVALID_MEDIA", "The source video is empty.")
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                probe_video(temporary)
                self._managed_dir()
                os.replace(temporary, target)
                published = target
                temporary = None
                connection.execute("""INSERT INTO media(id, product_id, original_name, relative_path,
                    size_bytes, sha256, storage_mode, mime_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                   (media_id, product_id, source.name, target.name, size, digest.hexdigest(),
                                    "managed", VIDEO_FORMATS[suffix][1], timestamp()))
                media = self._serialize_media(self._media(media_id))
                if media["status"] != "ready":
                    raise StudioError("STORAGE_ERROR", "The imported video became unavailable before publication.")
            # Return only after COMMIT. A DB publication failure removes the copy.
            published = None
            return {"media": media}
        finally:
            for path in (temporary, published):
                if path is not None:
                    try:
                        self._managed_dir()
                        path.unlink(missing_ok=True)
                    except (OSError, StudioError):
                        # Preserve the original error; startup recovery retries
                        # cleanup once permissions/disk access are restored.
                        log.warning("Could not remove an incomplete managed import: %s", path.name)

    def _delete_media(self, params: dict) -> dict:
        media_id = identifier(params.get("id"))
        tombstone, target = None, None
        try:
            with self._storage.transaction() as connection:
                row = self._media(media_id)
                target = self._media_path(row)
                # Rename first so rollback can restore the copy. The original
                # user file is never named in a delete operation.
                if target.exists():
                    if not target.is_file():
                        raise StudioError("UNSAFE_PATH", "The managed video path is not a regular file.")
                    tombstone = target.with_name(".delete-" + target.name)
                    os.replace(target, tombstone)
                connection.execute("DELETE FROM media WHERE id = ?", (media_id,))
        except BaseException:
            if tombstone is not None and tombstone.exists():
                os.replace(tombstone, target)
            raise
        if tombstone is not None:
            try:
                tombstone.unlink(missing_ok=True)
            except OSError:
                # Deletion is committed; recovery can remove the tombstone.
                log.warning("Could not remove a deleted managed video: %s", tombstone.name)
        return {"deleted": True}

    def _recover_imports(self) -> None:
        """Remove only our staging files and UUID copies left by a failed commit.

        Missing committed files are retained and surfaced as status=missing, so
        metadata and links are never silently discarded after disk removal.
        """
        with self._storage.transaction() as connection:
            known = {row[0] for row in connection.execute("SELECT relative_path FROM media")}
            for path in self._managed_dir().iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name.startswith(".delete-"):
                    original = path.name.removeprefix(".delete-")
                    try:
                        valid = (identifier(Path(original).stem) == Path(original).stem
                                 and Path(original).suffix.lower() in VIDEO_FORMATS)
                    except StudioError:
                        valid = False
                    if valid:
                        if original in known:
                            target = self.media_dir / original
                            if not target.exists():
                                os.replace(path, target)
                            else:
                                path.unlink()
                        else:
                            path.unlink()
                    continue
                staged = (path.name.startswith(".import-") and ".partial" in path.name
                          and path.suffix.lower() in VIDEO_FORMATS)
                orphan = False
                if path.name not in known and path.suffix.lower() in VIDEO_FORMATS:
                    try:
                        orphan = identifier(path.stem) == path.stem
                    except StudioError:
                        pass
                if staged or orphan:
                    path.unlink()
            self._recover_uploads()

    def _recover_uploads(self) -> None:
        """Clean browser staging after a killed process, never user source paths.

        Called only during startup under the service lock/write transaction;
        the runtime's directory lock excludes another active HTTP uploader.
        Uploads contain one immediate file, so recovery never needs recursive
        deletion and can leave unexpected directory structures untouched.
        """
        staging = self.data_dir / "imports"
        try:
            if not staging.exists():
                return
            if staging.is_symlink() or staging.resolve() != staging or not staging.is_dir():
                log.warning("Skipped an unsafe browser upload staging directory")
                return
            for folder in staging.iterdir():
                if not folder.name.startswith("upload-"):
                    continue
                try:
                    if (folder.is_symlink() or folder.resolve() != folder or not folder.is_dir()
                            or getattr(folder, "is_junction", lambda: False)()):
                        continue
                    children = list(folder.iterdir())
                    # Check the complete folder before removing any files.
                    if any(child.is_symlink() or child.resolve() != child or not child.is_file()
                           or not stat.S_ISREG(child.stat().st_mode) for child in children):
                        continue
                    for child in children:
                        child.unlink()
                    folder.rmdir()
                except (OSError, RuntimeError):
                    log.warning("Could not clean a stale browser upload folder: %s", folder.name)
        except (OSError, RuntimeError):
            log.warning("Could not inspect the browser upload staging directory")
