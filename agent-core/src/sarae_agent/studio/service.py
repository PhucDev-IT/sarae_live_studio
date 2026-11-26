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

from .models import (StudioError, identifier, identifier_list, presenter_fields, product_fields, tiktok_snapshot,
                     timestamp)
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
THUMBNAIL_SUFFIXES = {".jpg", ".png", ".webp"}
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
                "studio.products.update": (self._update_product, {"id", "description"}),
                "studio.products.delete": (self._delete_product, {"id"}),
                "studio.presenters.list": (self._list_presenters, set()),
                "studio.presenters.create": (self._create_presenter, {"name", "description", "active"}),
                "studio.presenters.update": (self._update_presenter, {"id", "name", "description", "active"}),
                "studio.presenters.delete": (self._delete_presenter, {"id"}),
                "studio.media.list": (self._list_media, {"product_id", "presenter_id"}),
                "studio.media.import": (self._import_media, {"source_path", "product_id", "presenter_ids"}),
                "studio.media.update": (self._update_media, {"id", "product_id", "presenter_ids"}),
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
                "capabilities": {"products": True, "tiktok_sync": True, "presenters": True, "managed_media": True,
                                 "native_dialog": False, "local_file_reference": False, "obs": False, "ai": False}}

    @staticmethod
    def _serialize_product(row) -> dict:
        product = {key: row[key] for key in row.keys() if key != "thumbnail"}
        product["source"] = "tiktok" if row["tiktok_product_id"] else "manual"
        product["has_thumbnail"] = bool(row["thumbnail"])
        return product

    def _list_products(self, params: dict) -> dict:
        rows = self._storage.connection.execute(
            "SELECT * FROM products ORDER BY tiktok_product_id IS NULL, name COLLATE NOCASE, id").fetchall()
        return {"products": [self._serialize_product(row) for row in rows]}

    def _product(self, product_id: str):
        row = self._storage.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "Product not found.", {"id": product_id})
        return row

    def _update_product(self, params: dict) -> dict:
        # Name, price, stock and image belong to TikTok; Studio owns the description.
        product_id = identifier(params.get("id"))
        if "description" not in params:
            raise StudioError("VALIDATION_ERROR", "Provide the product description to update.")
        description = product_fields({"name": "-", "description": params["description"]}, partial=True)["description"]
        with self._storage.transaction() as connection:
            self._product(product_id)
            connection.execute("UPDATE products SET description = ?, updated_at = ? WHERE id = ?",
                               (description, timestamp(), product_id))
            product = self._serialize_product(self._product(product_id))
        return {"product": product}

    def _delete_product(self, params: dict) -> dict:
        product_id = identifier(params.get("id"))
        with self._storage.transaction() as connection:
            thumbnail = self._product(product_id)["thumbnail"]
            # Media remains usable in the library when its product is deleted.
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
        if thumbnail:
            self._remove_thumbnail(thumbnail)
        return {"deleted": True}

    # --- TikTok catalog -------------------------------------------------------------------
    def import_tiktok_snapshot(self, account: object, rows: object) -> dict:
        """Upsert a complete LIVE Console snapshot by (seller_id, product_id).

        TikTok fields are refreshed; description, video and presenter links are
        kept. Products missing from this snapshot are reported, never deleted.
        """
        profile, products = tiktok_snapshot(account, rows)
        with self._lock:
            self._ensure_open()
            try:
                now, created, updated, stale = timestamp(), 0, 0, []
                with self._storage.transaction() as connection:
                    for item in products:
                        row = connection.execute(
                            """SELECT id, image_url, thumbnail FROM products
                               WHERE tiktok_seller_id = ? AND tiktok_product_id = ?""",
                            (item["tiktok_seller_id"], item["tiktok_product_id"])).fetchone()
                        values = (item["name"], item["price"], item["origin_price"], item["stock_num"],
                                  item["category"], item["image_url"], now)
                        if row is None:
                            connection.execute(
                                """INSERT INTO products(id, name, description, price, origin_price, stock_num, category,
                                   image_url, last_synced_at, tiktok_seller_id, tiktok_product_id, created_at, updated_at)
                                   VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (str(uuid4()), *values, item["tiktok_seller_id"], item["tiktok_product_id"], now, now))
                            created += 1
                        else:
                            if row["thumbnail"] and row["image_url"] != item["image_url"]:
                                # A new cover is downloaded again; the old file goes after commit.
                                stale.append(row["thumbnail"])
                                connection.execute("UPDATE products SET thumbnail = NULL WHERE id = ?", (row["id"],))
                            connection.execute(
                                """UPDATE products SET name = ?, price = ?, origin_price = ?, stock_num = ?, category = ?,
                                   image_url = ?, last_synced_at = ?, updated_at = ? WHERE id = ?""",
                                (*values, now, row["id"]))
                            updated += 1
                    sellers = sorted({item["tiktok_seller_id"] for item in products})
                    seen = {(item["tiktok_seller_id"], item["tiktok_product_id"]) for item in products}
                    missing = 0
                    if sellers:
                        for row in connection.execute(
                                "SELECT tiktok_seller_id, tiktok_product_id FROM products WHERE tiktok_seller_id IN ("
                                + ", ".join("?" for _ in sellers) + ")", sellers):
                            missing += (row[0], row[1]) not in seen
                    connection.execute(
                        """INSERT INTO tiktok_accounts(user_id, nick_name, user_name, avatar_url, product_count, last_synced_at)
                           VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET nick_name = excluded.nick_name,
                           user_name = excluded.user_name, avatar_url = excluded.avatar_url,
                           product_count = excluded.product_count, last_synced_at = excluded.last_synced_at""",
                        (profile["user_id"], profile["nick_name"], profile["user_name"], profile["avatar_url"],
                         len(products), now))
            except sqlite3.Error as exc:
                raise StudioError("STORAGE_ERROR", "Could not save the TikTok product list.") from exc
            for name in stale:
                self._remove_thumbnail(name)
        return {"created": created, "updated": updated, "missing": missing, "total": len(products),
                "account": {**profile, "product_count": len(products), "last_synced_at": now}}

    def tiktok_account(self) -> dict | None:
        with self._lock:
            self._ensure_open()
            row = self._storage.connection.execute(
                "SELECT * FROM tiktok_accounts ORDER BY last_synced_at DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def thumbnail_jobs(self) -> list[dict]:
        """TikTok products whose cover image has not been cached yet."""
        with self._lock:
            self._ensure_open()
            rows = self._storage.connection.execute(
                "SELECT id, image_url FROM products WHERE image_url != '' AND thumbnail IS NULL").fetchall()
            return [dict(row) for row in rows]

    def _thumbnail_dir(self) -> Path:
        folder = self.data_dir / "thumbnails"
        folder.mkdir(exist_ok=True)
        if folder.is_symlink() or folder.resolve() != folder:
            raise StudioError("UNSAFE_PATH", "The thumbnail directory points outside the studio data directory.")
        return folder

    def store_thumbnail(self, product_id: str, image_url: str, data: bytes, suffix: str) -> bool:
        """Publish a downloaded cover unless the product or its image changed meanwhile."""
        if suffix not in THUMBNAIL_SUFFIXES or not data:
            return False
        name = identifier(product_id) + suffix
        with self._lock:
            self._ensure_open()
            folder = self._thumbnail_dir()
            temporary = folder / (".partial-" + name)
            temporary.write_bytes(data)
            try:
                with self._storage.transaction() as connection:
                    row = connection.execute("SELECT image_url, thumbnail FROM products WHERE id = ?",
                                             (product_id,)).fetchone()
                    if row is None or row["image_url"] != image_url:
                        return False
                    os.replace(temporary, folder / name)
                    connection.execute("UPDATE products SET thumbnail = ? WHERE id = ?", (name, product_id))
                if row["thumbnail"] and row["thumbnail"] != name:
                    self._remove_thumbnail(row["thumbnail"])
                return True
            finally:
                temporary.unlink(missing_ok=True)

    def resolve_thumbnail(self, product_id: str) -> Path:
        with self._lock:
            self._ensure_open()
            try:
                row = self._product(identifier(product_id))
                name = row["thumbnail"]
                if not name or name != row["id"] + Path(name).suffix or Path(name).suffix not in THUMBNAIL_SUFFIXES:
                    raise StudioError("NOT_FOUND", "This product has no cached image.")
                target = self._thumbnail_dir() / name
                if target.is_symlink() or not target.is_file():
                    raise StudioError("NOT_FOUND", "This product has no cached image.")
                return target
            except (OSError, sqlite3.Error) as exc:
                raise StudioError("NOT_FOUND", "This product has no cached image.") from exc

    def _remove_thumbnail(self, name: str) -> None:
        try:
            (self._thumbnail_dir() / Path(name).name).unlink(missing_ok=True)
        except (OSError, StudioError):
            log.warning("Could not remove a product thumbnail: %s", name)

    @staticmethod
    def _serialize_presenter(row) -> dict:
        presenter = dict(row)
        presenter["active"] = bool(presenter["active"])
        return presenter

    def _list_presenters(self, params: dict) -> dict:
        rows = self._storage.connection.execute(
            "SELECT * FROM presenters ORDER BY active DESC, name COLLATE NOCASE, id").fetchall()
        return {"presenters": [self._serialize_presenter(row) for row in rows]}

    def _presenter(self, presenter_id: str):
        row = self._storage.connection.execute("SELECT * FROM presenters WHERE id = ?", (presenter_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "Presenter not found.", {"id": presenter_id})
        return row

    def _create_presenter(self, params: dict) -> dict:
        fields = presenter_fields(params)
        presenter_id, now = str(uuid4()), timestamp()
        with self._storage.transaction() as connection:
            connection.execute("""INSERT INTO presenters(id, name, description, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                               (presenter_id, fields["name"], fields["description"], fields["active"], now, now))
            presenter = self._serialize_presenter(self._presenter(presenter_id))
        return {"presenter": presenter}

    def _update_presenter(self, params: dict) -> dict:
        presenter_id = identifier(params.get("id"))
        fields = presenter_fields(params, partial=True)
        if not fields:
            raise StudioError("VALIDATION_ERROR", "Provide at least one presenter field to update.")
        fields["updated_at"] = timestamp()
        with self._storage.transaction() as connection:
            self._presenter(presenter_id)
            connection.execute("UPDATE presenters SET " + ", ".join(f"{key} = ?" for key in fields) + " WHERE id = ?",
                               (*fields.values(), presenter_id))
            presenter = self._serialize_presenter(self._presenter(presenter_id))
        return {"presenter": presenter}

    def _delete_presenter(self, params: dict) -> dict:
        presenter_id = identifier(params.get("id"))
        with self._storage.transaction() as connection:
            self._presenter(presenter_id)
            # Links cascade; the videos themselves stay in the library.
            connection.execute("DELETE FROM presenters WHERE id = ?", (presenter_id,))
        return {"deleted": True}

    def _presenter_ids(self, params: dict) -> list[str] | None:
        if "presenter_ids" not in params:
            return None
        ids = identifier_list(params["presenter_ids"], "presenter_ids")
        for presenter_id in ids:
            self._presenter(presenter_id)
        return ids

    def _link_presenters(self, connection, media_id: str, presenter_ids: list[str]) -> None:
        connection.execute("DELETE FROM media_presenters WHERE media_id = ?", (media_id,))
        connection.executemany("INSERT INTO media_presenters(media_id, presenter_id) VALUES (?, ?)",
                               [(media_id, presenter_id) for presenter_id in presenter_ids])

    def _presenter_links(self, media_ids: list[str] | None = None) -> dict[str, list[str]]:
        query = "SELECT media_id, presenter_id FROM media_presenters"
        args: tuple = ()
        if media_ids is not None:
            if not media_ids:
                return {}
            query += " WHERE media_id IN (" + ", ".join("?" for _ in media_ids) + ")"
            args = tuple(media_ids)
        links: dict[str, list[str]] = {}
        for media_id, presenter_id in self._storage.connection.execute(query + " ORDER BY presenter_id", args):
            links.setdefault(media_id, []).append(presenter_id)
        return links

    def _update_media(self, params: dict) -> dict:
        media_id = identifier(params.get("id"))
        if not {"product_id", "presenter_ids"} & params.keys():
            raise StudioError("VALIDATION_ERROR", "Provide product_id or presenter_ids to update.")
        with self._storage.transaction() as connection:
            self._media(media_id)
            if "product_id" in params:
                product_id = params["product_id"]
                if product_id is not None:
                    product_id = identifier(product_id, "product_id")
                    self._product(product_id)
                connection.execute("UPDATE media SET product_id = ? WHERE id = ?", (product_id, media_id))
            presenter_ids = self._presenter_ids(params)
            if presenter_ids is not None:
                self._link_presenters(connection, media_id, presenter_ids)
            media = self._serialize_media(self._media(media_id))
        return {"media": media}

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

    def _serialize_media(self, row, links: dict[str, list[str]] | None = None) -> dict:
        media = {key: row[key] for key in ("id", "product_id", "original_name", "size_bytes", "sha256",
                                          "storage_mode", "mime_type", "created_at")}
        if links is None:
            links = self._presenter_links([row["id"]])
        media["presenter_ids"] = links.get(row["id"], [])
        try:
            self.resolve_media(row["id"])
            media["status"] = "ready"
        except StudioError as exc:
            if exc.code not in {"MEDIA_MISSING", "UNSAFE_PATH", "STORAGE_ERROR"}:
                raise
            media["status"] = "missing"
        return media

    def _list_media(self, params: dict) -> dict:
        conditions, args = [], []
        if params.get("product_id") is not None:
            product_id = identifier(params["product_id"], "product_id")
            self._product(product_id)
            conditions.append("product_id = ?")
            args.append(product_id)
        if params.get("presenter_id") is not None:
            presenter_id = identifier(params["presenter_id"], "presenter_id")
            self._presenter(presenter_id)
            conditions.append("id IN (SELECT media_id FROM media_presenters WHERE presenter_id = ?)")
            args.append(presenter_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self._storage.connection.execute(
            f"SELECT * FROM media{where} ORDER BY created_at DESC, id", args).fetchall()
        links = self._presenter_links()
        return {"media": [self._serialize_media(row, links) for row in rows]}

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
                presenter_ids = self._presenter_ids(params) or []
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
                self._link_presenters(connection, media_id, presenter_ids)
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
