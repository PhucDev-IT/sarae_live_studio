"""Transport-independent local studio operations; no cloud or native runtime imports."""
from __future__ import annotations

import hashlib
import json
import logging
import math
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

from .models import (EMPTY_FACTS, StudioError, identifier, identifier_list, presenter_fields, product_facts,
                     product_fields, tiktok_snapshot, timestamp)
from .storage import StudioStorage, data_root
from . import overlays as overlay_rules
from .overlays import image_info
from sarae_agent.overlay import schedule as overlay_schedule
from . import media_thumbnails


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


def _finite_number(value) -> float | None:
    try:
        number = float(value) if not isinstance(value, bool) else math.nan
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _video_metadata(video: dict, container: dict) -> dict:
    duration = _finite_number(video.get("duration"))
    if duration is None or duration <= 0:
        duration = _finite_number(container.get("duration"))
    if duration is not None and duration <= 0:
        duration = None
    width, height = int(video["width"]), int(video["height"])
    rotation = None
    for side_data in video.get("side_data_list", []):
        rotation = _finite_number(side_data.get("rotation"))
        if rotation is None and isinstance(side_data.get("displaymatrix"), str):
            # FFprobe prints three indexed rows of the fixed-point display matrix.
            try:
                rows = [line.split(":", 1)[1].split()
                        for line in side_data["displaymatrix"].splitlines() if ":" in line]
                a, b = float(rows[0][0]), float(rows[0][1])
                if math.isfinite(a) and math.isfinite(b) and (a or b):
                    rotation = math.degrees(math.atan2(-b, a))
            except (IndexError, ValueError):
                pass
        if rotation is not None:
            break
    if rotation is None:
        rotation = _finite_number(video.get("tags", {}).get("rotate"))
    if rotation is not None and math.isclose(rotation % 180, 90, abs_tol=0.01):
        width, height = height, width
    return {"duration_seconds": duration, "width": width, "height": height}


def probe_video(path: Path) -> dict:
    """Validate the copied bytes and read display metadata with a 15s deadline."""
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
                [executable, "-v", "error", "-protocol_whitelist", "file",
                 "-format_whitelist", ",".join(sorted(allowed)), "-select_streams", "v:0",
                 "-show_entries", "stream=codec_type,width,height,duration:stream_disposition=attached_pic:"
                 "stream_tags=rotate:stream_side_data=rotation,displaymatrix:format=format_name,duration",
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
        return _video_metadata(video, payload.get("format", {}))
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
        # Bumped on every marker change: the pin scheduler restarts its attempt budget for a new timeline.
        self.markers_revision = 1
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
        # Inspection and frame generation release the lock. OBS/live
        # controls and shutdown must remain responsive during library backfills.
        if method == "studio.media.inspect":
            return self._inspect_media(params)
        if method == "studio.media.thumbnail":
            return self._generate_media_thumbnail(params)
        with self._lock:
            self._ensure_open()
            if not isinstance(method, str) or not isinstance(params, dict):
                raise StudioError("VALIDATION_ERROR", "method must be text and params must be an object.")
            handlers = {
                "studio.status": (self._status, set()),
                "studio.products.list": (self._list_products, set()),
                "studio.products.update": (self._update_product, {"id", "description", "facts"}),
                "studio.products.delete": (self._delete_product, {"id"}),
                "studio.presenters.list": (self._list_presenters, set()),
                "studio.presenters.create": (self._create_presenter, {"name", "description", "active"}),
                "studio.presenters.update": (self._update_presenter, {"id", "name", "description", "active"}),
                "studio.presenters.delete": (self._delete_presenter, {"id"}),
                "studio.media.list": (self._list_media, {"product_id", "presenter_id"}),
                "studio.media.import": (self._import_media, {"source_path", "product_id", "presenter_ids"}),
                "studio.media.update": (self._update_media, {"id", "product_id", "presenter_ids"}),
                "studio.media.delete": (self._delete_media, {"id"}),
                "studio.media.markers.list": (self._list_markers, {"media_id"}),
                "studio.media.markers.set": (self._set_markers, {"media_id", "markers"}),
                "studio.media.overlays.list": (self._list_overlays, {"media_id"}),
                "studio.media.overlays.set": (self._set_overlays, {"media_id", "overlays"}),
                "studio.overlays.templates": (lambda _: self.overlay_templates(), set()),
                "studio.overlays.assets.list": (self._list_overlay_assets, set()),
                "studio.overlays.assets.import": (self._import_overlay_asset, {"source_path"}),
                "studio.overlays.assets.delete": (self._delete_overlay_asset, {"id"}),
                "studio.live.sessions.overlay_issues": (lambda p: self.session_overlay_issues(p.get("id")), {"id"}),
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
        try:
            product["facts"] = product_facts(json.loads(row["facts"] or "{}"))
        except (ValueError, StudioError):
            product["facts"] = json.loads(json.dumps(EMPTY_FACTS))
        return product

    def product_context(self, product_id: str) -> dict:
        """The product as the AI may see it: TikTok fields, description and facts only."""
        with self._lock:
            self._ensure_open()
            product = self._serialize_product(self._product(identifier(product_id, "product_id")))
        return {key: product[key] for key in ("id", "name", "description", "price", "origin_price", "stock_num",
                                              "category", "facts")}

    # --- settings (never secrets) ---------------------------------------------------------
    def get_setting(self, key: str, default: dict) -> dict:
        with self._lock:
            self._ensure_open()
            row = self._storage.connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        try:
            value = json.loads(row["value"]) if row else None
        except ValueError:
            value = None
        return value if isinstance(value, dict) else dict(default)

    def set_setting(self, key: str, value: dict) -> None:
        with self._lock:
            self._ensure_open()
            with self._storage.transaction() as connection:
                connection.execute("""INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                                   (key, json.dumps(value, ensure_ascii=False), timestamp()))

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
        # Name, price, stock and image belong to TikTok; Studio owns the description and facts.
        product_id = identifier(params.get("id"))
        fields = {}
        if "description" in params:
            fields["description"] = product_fields({"name": "-", "description": params["description"]},
                                                   partial=True)["description"]
        if "facts" in params:
            fields["facts"] = json.dumps(product_facts(params["facts"]), ensure_ascii=False)
        if not fields:
            raise StudioError("VALIDATION_ERROR", "Provide the product description or facts to update.")
        fields["updated_at"] = timestamp()
        with self._storage.transaction() as connection:
            self._product(product_id)
            connection.execute("UPDATE products SET " + ", ".join(f"{key} = ?" for key in fields) + " WHERE id = ?",
                               (*fields.values(), product_id))
            product = self._serialize_product(self._product(product_id))
        return {"product": product}

    def _delete_product(self, params: dict) -> dict:
        product_id = identifier(params.get("id"))
        with self._storage.transaction() as connection:
            thumbnail = self._product(product_id)["thumbnail"]
            # Media remains usable in the library when its product is deleted.
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
            connection.execute("DELETE FROM settings WHERE key = ?", ("product_detail:" + product_id,))
        if thumbnail:
            self._remove_thumbnail(thumbnail)
        return {"deleted": True}

    # --- LIVE sessions ----------------------------------------------------------------------
    # Business rules live here; studio/live.py validates the playback policy with the engine's
    # own model and drives OBS. Only one session can be "live" at a time.
    SCHEDULE_FIELDS = ("overlay_mode", "overlay_every_ms", "overlay_min_gap_ms")

    def _session_row(self, session_id: str) -> dict:
        row = self._storage.connection.execute("SELECT * FROM live_sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "LIVE session not found.", {"id": session_id})
        session = {key: row[key] for key in ("id", "title", "status", "end_reason", "started_at", "ended_at",
                                            "created_at", "updated_at", "video_order")}
        session["video_loop"] = bool(row["video_loop"])
        # Overlay schedule (dev LIVE_OVERLAYS.md §13–14): one switch for all the session's videos.
        session["overlay_mode"] = row["overlay_mode"]
        session["overlay_every_ms"] = row["overlay_every_ms"]
        session["overlay_min_gap_ms"] = row["overlay_min_gap_ms"]
        try:
            ids = json.loads(row["video_ids"] or "[]")
        except ValueError:
            ids = []
        # A video deleted from the library leaves the session (its markers went with it).
        session["video_ids"] = [media_id for media_id in ids if isinstance(media_id, str) and
                                self._storage.connection.execute("SELECT 1 FROM media WHERE id = ?",
                                                                 (media_id,)).fetchone()]
        return session

    def live_session(self, session_id: str) -> dict:
        with self._lock:
            self._ensure_open()
            return self._session_row(identifier(session_id, "session_id"))

    def list_live_sessions(self) -> list[dict]:
        with self._lock:
            self._ensure_open()
            ids = [row[0] for row in self._storage.connection.execute(
                "SELECT id FROM live_sessions ORDER BY status = 'live' DESC, updated_at DESC, id")]
            return [self._session_row(session_id) for session_id in ids]

    @staticmethod
    def _session_title(title: object) -> str:
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200 or "\x00" in title:
            raise StudioError("VALIDATION_ERROR", "title must contain 1 to 200 characters.", {"field": "title"})
        return title.strip()

    def _session_videos(self, video_ids: object) -> list[str]:
        if not isinstance(video_ids, list) or not 1 <= len(video_ids) <= 100:
            raise StudioError("VALIDATION_ERROR", "Choose 1 to 100 videos for the session.", {"field": "video_ids"})
        ids = [identifier(value, "video_ids") for value in video_ids]
        if len(set(ids)) != len(ids):
            raise StudioError("VALIDATION_ERROR", "A video appears twice in the session.", {"field": "video_ids"})
        for media_id in ids:
            self._media(media_id)
        return ids

    def save_live_session(self, session_id: str | None, params: dict) -> dict:
        """Create (session_id None) or update a session: title, video_ids (ordered), video_order, video_loop and the
        overlay schedule (overlay_mode, overlay_every_ms, overlay_min_gap_ms; editable while live: the snapshot's
        revision changes, so the running OverlayService picks it up on its next sync)."""
        unknown = set(params) - {"title", "video_ids", "video_order", "video_loop", *self.SCHEDULE_FIELDS}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        fields = {}
        if session_id is None or "title" in params:
            fields["title"] = self._session_title(params.get("title"))
        if "video_order" in params:
            if params["video_order"] not in ("sequential", "random", "shuffle"):
                raise StudioError("VALIDATION_ERROR", "video_order must be sequential, random or shuffle.",
                                  {"field": "video_order"})
            fields["video_order"] = params["video_order"]
        if "video_loop" in params:
            if not isinstance(params["video_loop"], bool):
                raise StudioError("VALIDATION_ERROR", "video_loop must be true or false.", {"field": "video_loop"})
            fields["video_loop"] = int(params["video_loop"])
        if "overlay_mode" in params:
            if params["overlay_mode"] not in overlay_rules.SCHEDULE_MODES:
                raise StudioError("VALIDATION_ERROR", "overlay_mode must be fixed, window or random.",
                                  {"field": "overlay_mode"})
            fields["overlay_mode"] = params["overlay_mode"]
        for field, low, high in (("overlay_every_ms", overlay_rules.MIN_EVERY_MS, overlay_rules.MAX_EVERY_MS),
                                 ("overlay_min_gap_ms", 0, overlay_rules.MAX_GAP_MS)):
            if field in params:
                value = params[field]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    raise StudioError("VALIDATION_ERROR", f"{field} must be a whole number from {low} to {high}.",
                                      {"field": field})
                fields[field] = value
        with self._lock:
            self._ensure_open()
            if session_id is None or "video_ids" in params:
                fields["video_ids"] = json.dumps(self._session_videos(params.get("video_ids")))
            now = timestamp()
            with self._storage.transaction() as connection:
                if session_id is None:
                    session_id = str(uuid4())
                    connection.execute("INSERT INTO live_sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                                       (session_id, fields["title"], now, now))
                else:
                    session_id = identifier(session_id)
                    self._session_row(session_id)
                if not fields and session_id:
                    raise StudioError("VALIDATION_ERROR", "Provide a title, videos, order or loop to update.")
                fields["updated_at"] = now
                connection.execute("UPDATE live_sessions SET " + ", ".join(f"{key} = ?" for key in fields)
                                   + " WHERE id = ?", (*fields.values(), session_id))
                return self._session_row(session_id)

    def delete_live_session(self, session_id: str) -> None:
        with self._lock:
            self._ensure_open()
            session_id = identifier(session_id)
            with self._storage.transaction() as connection:
                if self._session_row(session_id)["status"] == "live":
                    raise StudioError("SESSION_LIVE", "Dừng phiên LIVE trước khi xóa.")
                connection.execute("DELETE FROM live_sessions WHERE id = ?", (session_id,))

    def set_live_status(self, session_id: str, status: str, end_reason: str | None = None) -> dict:
        with self._lock:
            self._ensure_open()
            now = timestamp()
            with self._storage.transaction() as connection:
                self._session_row(session_id)
                if status == "live":
                    other = connection.execute("SELECT id FROM live_sessions WHERE status = 'live' AND id != ?",
                                               (session_id,)).fetchone()
                    if other:
                        raise StudioError("SESSION_CONFLICT", "Một phiên LIVE khác đang phát. Hãy dừng phiên đó trước.")
                    connection.execute("""UPDATE live_sessions SET status = 'live', started_at = ?, ended_at = NULL,
                        end_reason = NULL, updated_at = ? WHERE id = ?""", (now, now, session_id))
                else:
                    connection.execute("""UPDATE live_sessions SET status = ?, ended_at = ?, end_reason = ?, updated_at = ?
                        WHERE id = ? AND status = 'live'""", (status, now, end_reason, now, session_id))
                return self._session_row(session_id)

    def interrupt_live_sessions(self, reason: str) -> None:
        """At Core start: a session left "live" by a crash never resumes on its own."""
        with self._lock:
            self._ensure_open()
            now = timestamp()
            with self._storage.transaction() as connection:
                connection.execute("""UPDATE live_sessions SET status = 'ended', ended_at = ?, end_reason = ?,
                    updated_at = ? WHERE status = 'live'""", (now, reason, now))

    # --- pin markers (per video) -----------------------------------------------------------
    def _marker_rows(self, media_ids: list[str]) -> list[dict]:
        if not media_ids:
            return []
        rows = self._storage.connection.execute(
            "SELECT m.*, p.name AS product_name, p.tiktok_product_id FROM media_markers m "
            "LEFT JOIN products p ON p.id = m.product_id WHERE m.media_id IN ("
            + ", ".join("?" for _ in media_ids) + ") ORDER BY m.media_id, m.at_ms, m.seq, m.id", media_ids)
        return [dict(row) for row in rows]

    def _list_markers(self, params: dict) -> dict:
        media_id = identifier(params.get("media_id"), "media_id")
        self._media(media_id)
        return {"markers": self._marker_rows([media_id])}

    def _set_markers(self, params: dict) -> dict:
        """Replace one video's markers (the editor autosaves the whole list)."""
        media_id = identifier(params.get("media_id"), "media_id")
        markers = params.get("markers")
        if not isinstance(markers, list) or len(markers) > 500:
            raise StudioError("VALIDATION_ERROR", "markers must list at most 500 markers.", {"field": "markers"})
        row = self._media(media_id)
        limit_ms = int(row["duration_seconds"] * 1000) + 1000 if row["duration_seconds"] else None
        clean = []
        for index, marker in enumerate(markers):
            if not isinstance(marker, dict) or set(marker) - {"at_ms", "action", "product_id"}:
                raise StudioError("VALIDATION_ERROR", "A marker is invalid.", {"field": "markers", "index": index})
            at_ms, action = marker.get("at_ms"), marker.get("action")
            if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0 or (limit_ms and at_ms > limit_ms):
                raise StudioError("VALIDATION_ERROR", "A marker is outside the video.", {"field": "markers", "index": index})
            if action not in ("PIN_PRODUCT", "UNPIN_PRODUCT"):
                raise StudioError("VALIDATION_ERROR", "action must be PIN_PRODUCT or UNPIN_PRODUCT.",
                                  {"field": "markers", "index": index})
            product_id = None
            if action == "PIN_PRODUCT":
                product_id = identifier(marker.get("product_id"), "product_id")
                if not self._product(product_id)["tiktok_product_id"]:
                    raise StudioError("VALIDATION_ERROR", "Only products synced from TikTok can be pinned.",
                                      {"field": "markers", "index": index})
            elif marker.get("product_id") is not None:
                raise StudioError("VALIDATION_ERROR", "An unpin marker has no product.", {"field": "markers", "index": index})
            clean.append((at_ms, action, product_id))
        clean.sort(key=lambda item: item[0])
        with self._storage.transaction() as connection:
            connection.execute("DELETE FROM media_markers WHERE media_id = ?", (media_id,))
            connection.executemany(
                "INSERT INTO media_markers(media_id, at_ms, action, product_id, seq) VALUES (?, ?, ?, ?, ?)",
                [(media_id, at_ms, action, product_id, seq) for seq, (at_ms, action, product_id) in enumerate(clean)])
            self.markers_revision += 1
        return {"markers": self._marker_rows([media_id])}

    def timeline_for(self, media_ids: list[str], expected_user_id: str) -> dict:
        """The scheduler's timeline for a session's videos: markers plus what the pinner needs per product."""
        with self._lock:
            self._ensure_open()
            markers = self._marker_rows(media_ids)
            products = {}
            for marker in markers:
                product_id = marker["product_id"]
                if product_id and product_id not in products:
                    product = self._product(product_id)
                    price = product["price"]
                    products[product_id] = {
                        "sarae_product_id": product_id, "tiktok_product_id": product["tiktok_product_id"],
                        "name": product["name"][:200], "image_url": (product["image_url"] or "")[:1000],
                        "price_text": (f"{int(price):,}".replace(",", ".") + "₫") if price is not None else ""}
            return {"version": self.markers_revision, "expected_user_id": expected_user_id,
                    "markers": [{"id": m["id"], "media_id": m["media_id"], "at_ms": m["at_ms"], "action": m["action"],
                                 "product_id": m["product_id"], "product_name": m["product_name"], "seq": m["seq"]}
                                for m in markers],
                    "products": list(products.values())}

    def record_live_event(self, session_id: str, kind: str, product_id: str | None = None,
                          detail: dict | None = None) -> None:
        with self._lock:
            if self._closed:
                return
            with self._storage.transaction() as connection:
                connection.execute("""INSERT INTO live_events(session_id, kind, product_id, detail, created_at)
                    VALUES (?, ?, ?, ?, ?)""", (session_id, kind[:40], product_id,
                                                json.dumps(detail or {}, ensure_ascii=False)[:2000], timestamp()))
                # Keep the log bounded per session.
                connection.execute("""DELETE FROM live_events WHERE session_id = ? AND id NOT IN
                    (SELECT id FROM live_events WHERE session_id = ? ORDER BY id DESC LIMIT 500)""",
                                   (session_id, session_id))

    def live_events(self, session_id: str, limit: int = 30) -> list[dict]:
        with self._lock:
            self._ensure_open()
            rows = self._storage.connection.execute(
                "SELECT * FROM live_events WHERE session_id = ? ORDER BY id DESC LIMIT ?", (session_id, limit))
            events = []
            for row in rows:
                event = dict(row)
                try:
                    event["detail"] = json.loads(event["detail"])
                except ValueError:
                    event["detail"] = {}
                events.append(event)
            return events

    # --- overlays (per video) ---------------------------------------------------------------
    def _overlay_rows(self, media_ids: list[str]) -> list[dict]:
        if not media_ids:
            return []
        rows = self._storage.connection.execute(
            "SELECT * FROM media_overlays WHERE media_id IN (" + ", ".join("?" for _ in media_ids)
            + ") ORDER BY media_id, position, id", media_ids)
        result = []
        for row in rows:
            try:
                overlay = json.loads(row["data"])
            except ValueError:
                continue
            result.append({"id": row["id"], "media_id": row["media_id"], **overlay})
        return result

    def _overlay_issues(self, media_row, overlays: list[dict]) -> list[dict]:
        issues = []
        duration_ms = int(media_row["duration_seconds"] * 1000) if media_row["duration_seconds"] else None
        products = {row[0] for row in self._storage.connection.execute("SELECT id FROM products")}
        assets = {row[0] for row in self._storage.connection.execute("SELECT id FROM overlay_assets")}
        for index, overlay in enumerate(overlays):
            if duration_ms and overlay["start_ms"] + overlay["duration_ms"] > duration_ms + 500:
                issues.append({"level": "error", "code": "AFTER_END", "overlay": index,
                               "message": "Overlay kết thúc sau khi video hết."})
            if overlay_rules.product_refs(overlay) - products:
                issues.append({"level": "error", "code": "PRODUCT_MISSING", "overlay": index,
                               "message": "Sản phẩm của overlay không còn trong danh mục."})
            if overlay_rules.asset_refs(overlay) - assets:
                issues.append({"level": "error", "code": "ASSET_MISSING", "overlay": index,
                               "message": "Ảnh của overlay đã bị xóa."})
        peak, at = overlay_rules.busiest(overlays)
        if peak > overlay_rules.MAX_CONCURRENT:
            issues.append({"level": "warning", "code": "TOO_MANY_AT_ONCE", "at_ms": at,
                           "message": f"{peak} overlay cùng lúc (tối đa {overlay_rules.MAX_CONCURRENT}); OBS sẽ không hiện bản này."})
        return issues

    def _list_overlays(self, params: dict) -> dict:
        media_id = identifier(params.get("media_id"), "media_id")
        row = self._media(media_id)
        overlays = self._overlay_rows([media_id])
        return {"overlays": overlays, "issues": self._overlay_issues(row, overlays),
                "scene": self._overlay_scene([media_id], include_disabled=True)}

    def _set_overlays(self, params: dict) -> dict:
        """Replace one video's overlays (the editor autosaves the whole list, in drawing order)."""
        media_id = identifier(params.get("media_id"), "media_id")
        row = self._media(media_id)
        overlays = overlay_rules.validate_overlays(params.get("overlays"))
        duration_ms = int(row["duration_seconds"] * 1000) if row["duration_seconds"] else None
        for index, overlay in enumerate(overlays):
            if duration_ms and overlay["start_ms"] + overlay["duration_ms"] > duration_ms + 500:
                raise StudioError("VALIDATION_ERROR", f"Overlay {index + 1} kết thúc sau khi video hết.",
                                  {"field": "overlays", "index": index})
            for product_id in overlay_rules.product_refs(overlay):
                self._product(product_id)
            for asset_id in overlay_rules.asset_refs(overlay):
                self._overlay_asset(asset_id)
        with self._storage.transaction() as connection:
            connection.execute("DELETE FROM media_overlays WHERE media_id = ?", (media_id,))
            connection.executemany("INSERT INTO media_overlays(media_id, position, data) VALUES (?, ?, ?)",
                                   [(media_id, index, json.dumps(overlay, ensure_ascii=False))
                                    for index, overlay in enumerate(overlays)])
        saved = self._overlay_rows([media_id])
        return {"overlays": saved, "issues": self._overlay_issues(row, saved),
                "scene": self._overlay_scene([media_id], include_disabled=True)}

    def _overlay_scene(self, media_ids: list[str], *, include_disabled: bool) -> dict:
        """Snapshot-shaped scene (dev LIVE_OVERLAYS.md §6) for the renderer: overlays plus the products and images they
        read. Images are local paths `/api/studio-…/<id>` (the editor adds its token, the OBS cache reads the file)."""
        overlays = [o for o in self._overlay_rows(media_ids) if include_disabled or o.get("enabled", True)]
        products, assets = {}, {}
        for overlay in overlays:
            for product_id in overlay_rules.product_refs(overlay):
                if product_id in products:
                    continue
                row = self._storage.connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                if row is None:
                    continue
                products[product_id] = {"name": row["name"], "sku": row["tiktok_product_id"] or "", "price": row["price"],
                                        "origin_price": row["origin_price"],
                                        **({"image_url": f"/api/studio-products/{product_id}"} if row["thumbnail"] else {})}
            for asset_id in overlay_rules.asset_refs(overlay):
                row = self._storage.connection.execute("SELECT * FROM overlay_assets WHERE id = ?", (asset_id,)).fetchone()
                if row is not None:
                    assets[asset_id] = {"url": f"/api/studio-assets/{asset_id}", "mime_type": row["mime_type"],
                                        "sha256": row["sha256"], "width": row["width"], "height": row["height"],
                                        "size_bytes": row["size_bytes"]}
        scene = {"canvas": {"width": overlay_rules.CANVAS_WIDTH, "height": overlay_rules.CANVAS_HEIGHT},
                 "overlays": [{key: value for key, value in o.items() if key != "id"} for o in overlays],
                 "products": products, "assets": assets}
        scene["revision"] = hashlib.sha256(json.dumps(scene, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        return scene

    @staticmethod
    def _session_schedule(session: dict) -> dict:
        return {"mode": session.get("overlay_mode") or "fixed",
                "every_ms": session.get("overlay_every_ms", overlay_rules.DEFAULT_SCHEDULE["every_ms"]),
                "min_gap_ms": session.get("overlay_min_gap_ms", overlay_rules.DEFAULT_SCHEDULE["min_gap_ms"])}

    @staticmethod
    def overlay_timeline_id(session_id: str) -> int:
        """The plan seed's `timeline_id` of a session (dev: the version id, an int). Studio sessions have UUIDs, so
        this is FNV-1a 32-bit of the session id (overlay/schedule.seed_of): stable, an integer for the TS model."""
        return overlay_schedule.seed_of(session_id)

    def _video_context(self, media_ids: list[str]) -> tuple[dict, dict]:
        """(pins, durations) of videos, like dev's video_context: pin spans from each video's markers (the Agent's
        schedule.pin_spans; unknown duration: up to UNKNOWN_DURATION_MS) and the known durations in ms."""
        durations = {}
        for media_id in media_ids:
            row = self._storage.connection.execute("SELECT duration_seconds FROM media WHERE id = ?",
                                                   (media_id,)).fetchone()
            if row is not None and row["duration_seconds"]:
                durations[media_id] = int(row["duration_seconds"] * 1000)
        markers = self._marker_rows(media_ids)
        pins = {media_id: overlay_schedule.pin_spans([m for m in markers if m["media_id"] == media_id],
                                                     durations.get(media_id) or overlay_rules.UNKNOWN_DURATION_MS)
                for media_id in dict.fromkeys(media_ids)}
        return pins, durations

    def overlay_snapshot(self, session: dict | list[str]) -> dict | None:
        """What the Agent's OverlayService shows for a session: its videos' enabled overlays, None when there are none.
        Fixed mode: the scene plus version / timeline_id None (unchanged). Window / random: also `schedule`,
        `timeline_id` (overlay_timeline_id), `pins` and `durations`, all part of the revision. A bare list of media
        ids (older callers) counts as a fixed-mode session of those videos."""
        if isinstance(session, list):
            session = {"id": None, "video_ids": session}
        media_ids = session["video_ids"]
        schedule = self._session_schedule(session)
        with self._lock:
            self._ensure_open()
            scene = self._overlay_scene(media_ids, include_disabled=False)
            if not scene["overlays"]:
                return None
            if not overlay_schedule.is_planned(schedule["mode"]):
                return {**scene, "version": 1, "timeline_id": None}
            pins, durations = self._video_context(media_ids)
        content = {key: value for key, value in scene.items() if key != "revision"}
        content.update({"version": 1, "timeline_id": self.overlay_timeline_id(session["id"]), "schedule": schedule,
                        "pins": pins, "durations": durations})
        content["revision"] = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False)
                                             .encode("utf-8")).hexdigest()[:24]
        return content

    def session_overlay_issues(self, session_id: str) -> dict:
        """Checks of the session's overlay schedule (dev planned_issues) over all its videos' overlays: errors
        SHOW_LONGER_THAN_WINDOW (window), SHOW_LONGER_THAN_SLOT, ANIMATION_LONGER_THAN_SHOW; warnings
        PRODUCT_NEVER_PINNED / PIN_TOO_SHORT (random), DENSITY_TOO_HIGH, NO_OVERLAY_IN_SLOTS. Fixed mode: none (the
        per-video checks of studio.media.overlays.list apply)."""
        with self._lock:
            self._ensure_open()
            session = self._session_row(identifier(session_id, "id"))
            schedule = self._session_schedule(session)
            if not overlay_schedule.is_planned(schedule["mode"]):
                return {"schedule": schedule, "issues": []}
            overlays = self._overlay_rows(session["video_ids"])
            pins, durations = self._video_context(session["video_ids"])
        return {"schedule": schedule, "issues": overlay_rules.planned_issues(overlays, schedule, pins, durations)}

    def overlay_templates(self) -> dict:
        return overlay_rules.templates()

    # Overlay images: PNG/JPEG/WebP, ≤ 5 MB, ≤ 4096 px a side (dev LIVE_OVERLAYS.md §5), stored by id.
    def _asset_dir(self) -> Path:
        folder = self.data_dir / "overlay_assets"
        folder.mkdir(exist_ok=True)
        if folder.is_symlink() or folder.resolve() != folder:
            raise StudioError("UNSAFE_PATH", "The overlay image directory points outside the studio data directory.")
        return folder

    def _overlay_asset(self, asset_id: str):
        if not isinstance(asset_id, str) or len(asset_id) != 32 or any(c not in "0123456789abcdef" for c in asset_id):
            raise StudioError("VALIDATION_ERROR", "Invalid overlay image id.", {"field": "asset_id"})
        row = self._storage.connection.execute("SELECT * FROM overlay_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise StudioError("NOT_FOUND", "Overlay image not found.", {"id": asset_id})
        return row

    def add_overlay_asset(self, data: bytes, original_name: str) -> dict:
        kind, width, height = image_info(data)
        if len(data) > 5 * 1024 * 1024:
            raise StudioError("VALIDATION_ERROR", "Ảnh overlay tối đa 5 MB.", {"field": "file"})
        if kind is None:
            raise StudioError("VALIDATION_ERROR", "Chỉ nhận ảnh PNG, JPEG hoặc WebP.", {"field": "file"})
        if not width or not height or max(width, height) > 4096:
            raise StudioError("VALIDATION_ERROR", "Ảnh overlay tối đa 4096 px mỗi cạnh.", {"field": "file"})
        asset_id = uuid4().hex
        with self._lock:
            self._ensure_open()
            folder = self._asset_dir()
            target = folder / asset_id
            temporary = folder / (".partial-" + asset_id)
            temporary.write_bytes(data)
            try:
                with self._storage.transaction() as connection:
                    os.replace(temporary, target)
                    connection.execute("""INSERT INTO overlay_assets(id, original_name, mime_type, sha256, width, height,
                        size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                       (asset_id, Path(original_name or "image").name[:200], kind,
                                        hashlib.sha256(data).hexdigest(), width, height, len(data), timestamp()))
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            return dict(self._overlay_asset(asset_id))

    def _import_overlay_asset(self, params: dict) -> dict:
        raw = params.get("source_path")
        if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).is_file():
            raise StudioError("VALIDATION_ERROR", "source_path must be an absolute image file path.", {"field": "source_path"})
        with open(raw, "rb") as handle:
            data = handle.read(5 * 1024 * 1024 + 1)
        return {"asset": self.add_overlay_asset(data, Path(raw).name)}

    def _list_overlay_assets(self, params: dict) -> dict:
        rows = self._storage.connection.execute("SELECT * FROM overlay_assets ORDER BY created_at DESC, id")
        return {"assets": [dict(row) for row in rows]}

    def _delete_overlay_asset(self, params: dict) -> dict:
        row = self._overlay_asset(params.get("id"))
        for overlay in self._overlay_rows([r[0] for r in self._storage.connection.execute("SELECT id FROM media")]):
            if row["id"] in overlay_rules.asset_refs(overlay):
                raise StudioError("ASSET_IN_USE", "Ảnh đang được dùng trong một overlay; gỡ khỏi overlay trước khi xóa.")
        with self._storage.transaction() as connection:
            connection.execute("DELETE FROM overlay_assets WHERE id = ?", (row["id"],))
        (self._asset_dir() / row["id"]).unlink(missing_ok=True)
        return {"deleted": True}

    def overlay_image(self, url: str) -> bytes:
        """Bytes of a scene image path: `/api/studio-assets/<id>` or `/api/studio-products/<id>` (product cover)."""
        with self._lock:
            self._ensure_open()
            path = url.split("?", 1)[0]
            if path.startswith("/api/studio-assets/"):
                row = self._overlay_asset(path.removeprefix("/api/studio-assets/"))
                target = self._asset_dir() / row["id"]
            elif path.startswith("/api/studio-products/"):
                target = self.resolve_thumbnail(path.removeprefix("/api/studio-products/"))
            else:
                raise StudioError("NOT_FOUND", "Unknown overlay image.")
            if target.is_symlink() or not target.is_file():
                raise StudioError("NOT_FOUND", "Overlay image file is missing.")
            return target.read_bytes()

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
                                          "storage_mode", "mime_type", "created_at", "duration_seconds",
                                          "width", "height")}
        media["metadata_probed"] = bool(row["metadata_probed"])
        media["has_thumbnail"] = False
        # Transport supplies a session-token URL, never a filesystem path.
        media["thumbnail_url"] = None
        if links is None:
            links = self._presenter_links([row["id"]])
        media["presenter_ids"] = links.get(row["id"], [])
        media["marker_count"] = self._storage.connection.execute(
            "SELECT COUNT(*) FROM media_markers WHERE media_id = ?", (row["id"],)).fetchone()[0]
        media["overlay_count"] = self._storage.connection.execute(
            "SELECT COUNT(*) FROM media_overlays WHERE media_id = ?", (row["id"],)).fetchone()[0]
        try:
            self.resolve_media(row["id"])
            media["status"] = "ready"
        except StudioError as exc:
            if exc.code not in {"MEDIA_MISSING", "UNSAFE_PATH", "STORAGE_ERROR"}:
                raise
            media["status"] = "missing"
        if media["status"] == "ready":
            try:
                target = media_thumbnails.checked_path(self.data_dir, media_thumbnails.cache_name(row["id"], row["sha256"]))
                media["has_thumbnail"] = media_thumbnails.valid_jpeg(target)
            except (OSError, RuntimeError, StudioError):
                pass
        return media

    def resolve_media_thumbnail(self, media_id: str) -> Path:
        """Resolve an existing JPEG for a real managed video; never generate on GET."""
        with self._lock:
            self._ensure_open()
            try:
                media_id = identifier(media_id)
                row = self._media(media_id)
                self.resolve_media(media_id)
                target = media_thumbnails.checked_path(self.data_dir, media_thumbnails.cache_name(media_id, row["sha256"]))
                if not media_thumbnails.valid_jpeg(target):
                    raise StudioError("NOT_FOUND", "This video has no cached thumbnail.", {"id": media_id})
                return target
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                raise StudioError("NOT_FOUND", "This video has no cached thumbnail.") from exc

    def _generate_media_thumbnail(self, params: dict) -> dict:
        temporary = None
        try:
            with self._lock:
                self._ensure_open()
                if not isinstance(params, dict):
                    raise StudioError("VALIDATION_ERROR", "params must be an object.")
                if params.keys() - {"id"}:
                    raise StudioError("VALIDATION_ERROR", "Unexpected parameters.",
                                      {"fields": sorted(str(key) for key in params.keys() - {"id"})})
                media_id = identifier(params.get("id"))
                row = self._media(media_id)
                path = self.resolve_media(media_id)
                fingerprint = self._media_fingerprint(path)
                identity = (row["relative_path"], row["size_bytes"], row["sha256"])
                name = media_thumbnails.cache_name(media_id, row["sha256"])
                target = media_thumbnails.checked_path(self.data_dir, name)
                if media_thumbnails.valid_jpeg(target, decode=True):
                    return {"media": self._serialize_media(row)}
                folder = media_thumbnails.cache_dir(self.data_dir, create=True)
                outgoing = tempfile.NamedTemporaryFile(mode="w+b", prefix=".frame-", suffix=".jpg",
                                                       dir=folder, delete=False)
                temporary = Path(outgoing.name)
                duration = row["duration_seconds"]
                allowed = VIDEO_FORMATS[path.suffix.lower()][0]
            # No global lock during subprocess execution, including fallback.
            with outgoing:
                media_thumbnails.generate_frame(path, outgoing, allowed, duration)
            with self._lock:
                self._ensure_open()
                row = self._media(media_id)
                current_path = self.resolve_media(media_id)
                if (identity != (row["relative_path"], row["size_bytes"], row["sha256"])
                        or current_path != path or self._media_fingerprint(current_path) != fingerprint):
                    raise StudioError("MEDIA_MISSING", "The managed video changed during thumbnail generation.", {"id": media_id})
                target = media_thumbnails.checked_path(self.data_dir, name)
                stage = media_thumbnails.checked_path(self.data_dir, temporary.name)
                if not media_thumbnails.valid_jpeg(stage, decode=True):
                    raise StudioError("THUMBNAIL_FAILED", "FFmpeg did not create a valid JPEG thumbnail.")
                os.replace(stage, target)
                temporary = None
                return {"media": self._serialize_media(row)}
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            raise StudioError("THUMBNAIL_FAILED", "Cannot create the local video thumbnail. You can retry.") from exc
        finally:
            if temporary is not None:
                try:
                    media_thumbnails.checked_path(self.data_dir, temporary.name).unlink(missing_ok=True)
                except (OSError, RuntimeError, StudioError):
                    log.warning("Could not remove a staged video thumbnail: %s", temporary.name)

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

    @staticmethod
    def _media_fingerprint(path: Path) -> tuple:
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _inspect_media(self, params: dict) -> dict:
        try:
            with self._lock:
                self._ensure_open()
                if not isinstance(params, dict):
                    raise StudioError("VALIDATION_ERROR", "params must be an object.")
                if params.keys() - {"id"}:
                    raise StudioError("VALIDATION_ERROR", "Unexpected parameters.",
                                      {"fields": sorted(str(key) for key in params.keys() - {"id"})})
                media_id = identifier(params.get("id"))
                row = self._media(media_id)
                if row["metadata_probed"] or (row["width"] is not None and row["height"] is not None):
                    if not row["metadata_probed"]:
                        with self._storage.transaction() as connection:
                            connection.execute("UPDATE media SET metadata_probed = 1 WHERE id = ?", (media_id,))
                    return {"media": self._serialize_media(self._media(media_id))}
                path = self.resolve_media(media_id)
                fingerprint = self._media_fingerprint(path)
                identity = (row["relative_path"], row["size_bytes"], row["sha256"])
            error = None
            try:
                metadata = probe_video(path) or {}
            except StudioError as exc:
                error, metadata = exc, {}
            with self._lock:
                self._ensure_open()
                row = self._media(media_id)
                current_path = self.resolve_media(media_id)
                if (identity != (row["relative_path"], row["size_bytes"], row["sha256"])
                        or current_path != path or self._media_fingerprint(current_path) != fingerprint):
                    raise StudioError("MEDIA_MISSING", "The managed video changed during inspection.", {"id": media_id})
                if error is not None:
                    # Keep the record retryable after installing FFprobe or
                    # recovering from a transient read/probe failure.
                    raise error
                with self._storage.transaction() as connection:
                    connection.execute("""UPDATE media SET
                        duration_seconds = COALESCE(duration_seconds, ?),
                        width = COALESCE(width, ?), height = COALESCE(height, ?), metadata_probed = 1
                        WHERE id = ?""", (metadata.get("duration_seconds"), metadata.get("width"),
                                          metadata.get("height"), media_id))
                return {"media": self._serialize_media(self._media(media_id))}
        except (OSError, sqlite3.Error) as exc:
            raise StudioError("STORAGE_ERROR", "The local storage operation failed. Check disk space and permissions.") from exc

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
                metadata = probe_video(temporary) or {}
                self._managed_dir()
                os.replace(temporary, target)
                published = target
                temporary = None
                connection.execute("""INSERT INTO media(id, product_id, original_name, relative_path,
                    size_bytes, sha256, storage_mode, mime_type, created_at, duration_seconds, width, height,
                    metadata_probed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                                   (media_id, product_id, source.name, target.name, size, digest.hexdigest(),
                                    "managed", VIDEO_FORMATS[suffix][1], timestamp(), metadata.get("duration_seconds"),
                                    metadata.get("width"), metadata.get("height")))
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
        try:
            thumbnail = media_thumbnails.checked_path(self.data_dir, media_thumbnails.cache_name(media_id, row["sha256"]))
            thumbnail.unlink(missing_ok=True)
        except (OSError, RuntimeError, StudioError):
            log.warning("Could not remove a deleted video's thumbnail: %s", media_id)
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
