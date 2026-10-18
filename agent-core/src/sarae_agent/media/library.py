"""Allowed video folders, MP4 scanning and the media_id <-> local path index.

media_id = sha256(casefold(resolved absolute path)), identical to the previous Agent so
playlists already stored in the Cloud keep pointing at the same files.
Removing a folder or an index entry never touches the real video file.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sarae_agent.config import ConfigStore, paths, read_json, write_json
from sarae_agent.errors import AgentError

log = logging.getLogger("sarae.media")

MAX_VIDEOS = 10_000  # Cloud /agent-library/sync limit
VIDEO_SUFFIXES = (".mp4",)
INDEX_VERSION = 1

Emit = Callable[[str, dict], None]


def media_id_for(path: Path) -> str:
    return hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()


def _normalize_folder(raw: str) -> Path:
    if not raw or not raw.strip():
        raise AgentError("INVALID_PATH", "Đường dẫn thư mục trống")
    try:
        folder = Path(raw.strip()).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AgentError("FOLDER_NOT_FOUND", "Thư mục không tồn tại hoặc không truy cập được") from exc
    if not folder.is_dir():
        raise AgentError("FOLDER_NOT_FOUND", "Đường dẫn không phải thư mục")
    if folder.parent == folder:
        raise AgentError("FOLDER_TOO_BROAD", "Không cho phép chọn cả ổ đĩa; hãy chọn thư mục video cụ thể")
    return folder


def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


class MediaLibrary:
    def __init__(self, config: ConfigStore, emit: Emit):
        self._config = config
        self._emit = emit
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._cancel = threading.Event()
        self._job: dict | None = None
        self._index: dict = self._load_index()
        self.dirty = True  # Cloud inventory must be (re)sent after start

    # --- persistence ---------------------------------------------------------------------
    def _load_index(self) -> dict:
        data = read_json(paths.media_index, None)
        if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
            legacy = read_json(paths.legacy_data / "agent_local_index.json", None)
            items = {}
            if isinstance(legacy, dict):
                for mid, path in legacy.items():
                    if isinstance(path, str) and len(mid) == 64:
                        items[mid] = {"path": path, "filename": Path(path).name, "size_bytes": 0,
                                      "modified_ns": 0, "folder": ""}
            return {"version": INDEX_VERSION, "scanned_at": None, "items": items}
        data.setdefault("items", {})
        return data

    def _save_index(self) -> None:
        write_json(paths.media_index, self._index)

    # --- folders -------------------------------------------------------------------------
    def folders(self) -> list[dict]:
        result = []
        counts: dict[str, int] = {}
        with self._lock:
            for item in self._index["items"].values():
                counts[item.get("folder", "")] = counts.get(item.get("folder", ""), 0) + 1
        for folder in self._config.get().media.folders:
            exists = Path(folder).is_dir()
            result.append({"path": folder, "exists": exists, "video_count": counts.get(folder, 0)})
        return result

    def allowed_roots(self) -> list[Path]:
        return [Path(f) for f in self._config.get().media.folders]

    def add_folder(self, raw: str) -> list[dict]:
        folder = _normalize_folder(raw)
        current = self._config.get().media.folders
        if str(folder) in current:
            raise AgentError("FOLDER_ALREADY_ADDED", "Thư mục đã có trong danh sách")
        if len(current) >= 50:
            raise AgentError("FOLDER_LIMIT", "Tối đa 50 thư mục video")
        self._config.update({"media": {"folders": [*current, str(folder)]}})
        log.info("Đã thêm thư mục video %s", folder)
        return self.folders()

    def remove_folder(self, raw: str) -> list[dict]:
        current = self._config.get().media.folders
        target = raw.strip()
        if target not in current:
            raise AgentError("FOLDER_NOT_IN_LIST", "Thư mục không có trong danh sách")
        remaining = [f for f in current if f != target]
        self._config.update({"media": {"folders": remaining}})
        roots = [Path(f) for f in remaining]
        with self._lock:
            # Drop index entries no longer under any allowed folder. Files on disk are untouched.
            self._index["items"] = {mid: item for mid, item in self._index["items"].items()
                                    if _within(Path(item["path"]), roots)}
            self._save_index()
            self.dirty = True
        log.info("Đã bỏ thư mục %s khỏi danh sách (không xoá file)", target)
        return self.folders()

    # --- scanning ------------------------------------------------------------------------
    def scan_status(self) -> dict | None:
        return dict(self._job) if self._job else None

    def start_scan(self) -> dict:
        """Start a background scan; progress arrives as media.scan_progress / scan_finished events."""
        if not self._scan_lock.acquire(blocking=False):
            raise AgentError("SCAN_IN_PROGRESS", "Đang quét thư viện", {"job": self.scan_status()})
        job_id = "scan_" + uuid.uuid4().hex[:12]
        self._cancel.clear()
        self._job = {"job_id": job_id, "state": "running", "scanned_files": 0, "found": 0,
                     "started_at": datetime.now(timezone.utc).isoformat()}
        threading.Thread(target=self._scan_thread, args=(job_id,), name="media-scan", daemon=True).start()
        return {"job_id": job_id, "state": "accepted"}

    def cancel_scan(self) -> dict:
        self._cancel.set()
        return {"cancelling": self._job is not None and self._job.get("state") == "running"}

    def scan_blocking(self) -> dict:
        """Synchronous scan used by the Cloud `media_sync` command and periodic rescans."""
        if not self._scan_lock.acquire(timeout=120):
            raise AgentError("SCAN_IN_PROGRESS", "Đang quét thư viện")
        job_id = "scan_" + uuid.uuid4().hex[:12]
        self._cancel.clear()
        self._job = {"job_id": job_id, "state": "running", "scanned_files": 0, "found": 0,
                     "started_at": datetime.now(timezone.utc).isoformat()}
        return self._scan_thread(job_id)

    def _scan_thread(self, job_id: str) -> dict:
        started = time.monotonic()
        errors: list[str] = []
        found: dict[str, dict] = {}
        scanned = 0
        last_emit = 0.0
        truncated = False
        try:
            roots = self.allowed_roots()
            for root in roots:
                if not root.is_dir():
                    errors.append(f"Thư mục không còn tồn tại: {root}")
                    continue
                stack = [root]
                while stack and not self._cancel.is_set():
                    current = stack.pop()
                    try:
                        entries = list(os.scandir(current))
                    except OSError as exc:
                        errors.append(f"{current}: {exc.strerror or exc}")
                        continue
                    for entry in entries:
                        if self._cancel.is_set():
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=True):
                                continue
                        except OSError:
                            continue
                        scanned += 1
                        if not entry.name.lower().endswith(VIDEO_SUFFIXES):
                            continue
                        try:
                            resolved = Path(entry.path).resolve(strict=True)
                            stat = resolved.stat()
                        except (OSError, RuntimeError):
                            continue
                        if not _within(resolved, roots):
                            continue  # symlink/junction escaping the allowed folders
                        mid = media_id_for(resolved)
                        if mid in found:
                            continue
                        found[mid] = {"path": str(resolved), "filename": resolved.name,
                                      "size_bytes": stat.st_size, "modified_ns": stat.st_mtime_ns,
                                      "folder": str(root)}
                        if len(found) >= MAX_VIDEOS:
                            truncated = True
                            self._cancel.set()
                            break
                        now = time.monotonic()
                        if now - last_emit >= 0.4:
                            last_emit = now
                            self._job.update(scanned_files=scanned, found=len(found))
                            self._emit("media.scan_progress", {"job_id": job_id, "scanned_files": scanned,
                                                               "found": len(found), "folder": str(root)})
            cancelled = self._cancel.is_set() and not truncated
            if cancelled:
                state = "cancelled"
                summary = {"added": 0, "removed": 0, "total": len(self._index["items"])}
            else:
                with self._lock:
                    previous = set(self._index["items"])
                    self._index = {"version": INDEX_VERSION,
                                   "scanned_at": datetime.now(timezone.utc).isoformat(), "items": found}
                    self._save_index()
                    self.dirty = True
                state = "succeeded"
                summary = {"added": len(set(found) - previous), "removed": len(previous - set(found)),
                           "total": len(found)}
            result = {"job_id": job_id, "state": state, "scanned_files": scanned,
                      "duration_ms": int((time.monotonic() - started) * 1000), "truncated": truncated,
                      "errors": errors[:20], **summary}
            log.info("Quét thư viện %s: %s video (thêm %s, mất %s)", state, summary["total"],
                     summary["added"], summary["removed"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Quét thư viện lỗi")
            result = {"job_id": job_id, "state": "failed", "error": {"code": "SCAN_FAILED", "message": str(exc)},
                      "scanned_files": scanned, "duration_ms": int((time.monotonic() - started) * 1000)}
        finally:
            self._job = None
            self._scan_lock.release()
        self._emit("media.scan_finished", result)
        return result

    # --- queries -------------------------------------------------------------------------
    def summary(self) -> dict:
        with self._lock:
            return {"video_count": len(self._index["items"]), "folder_count": len(self.allowed_roots()),
                    "scanned_at": self._index.get("scanned_at"), "scan": self.scan_status()}

    def list_videos(self, offset: int = 0, limit: int = 200, query: str = "") -> dict:
        with self._lock:
            items = [{"media_id": mid, **item} for mid, item in self._index["items"].items()]
        if query:
            q = query.casefold()
            items = [i for i in items if q in i["filename"].casefold()]
        items.sort(key=lambda i: i["filename"].casefold())
        page = items[offset:offset + limit]
        for item in page:
            item["available"] = Path(item["path"]).is_file()
        return {"total": len(items), "offset": offset, "items": page}

    def cloud_inventory(self) -> list[dict]:
        """Metadata only: no absolute path leaves the PC."""
        with self._lock:
            return [{"media_id": mid, "filename": item["filename"][:255], "size_bytes": int(item["size_bytes"]),
                     "modified_ns": int(item["modified_ns"])}
                    for mid, item in self._index["items"].items()]

    def resolve(self, media_id: str) -> Path:
        if not (isinstance(media_id, str) and len(media_id) == 64 and all(c in "0123456789abcdef" for c in media_id)):
            raise AgentError("INVALID_MEDIA_ID", "media_id không hợp lệ")
        with self._lock:
            item = self._index["items"].get(media_id)
        if item is None:
            raise AgentError("MEDIA_NOT_INDEXED", "Video chưa có trong thư viện Agent (cần quét lại)",
                             {"media_id": media_id})
        path = Path(item["path"])
        if not path.is_file():
            self.dirty = True
            raise AgentError("MEDIA_FILE_MISSING", f"File không còn tồn tại: {item['filename']}",
                             {"media_id": media_id})
        resolved = path.resolve()
        if not _within(resolved, self.allowed_roots()):
            raise AgentError("MEDIA_OUTSIDE_ALLOWED", "Video nằm ngoài các thư mục đã cho phép",
                             {"media_id": media_id})
        if resolved.suffix.lower() not in VIDEO_SUFFIXES:
            raise AgentError("MEDIA_NOT_ALLOWED", "Chỉ phát file .mp4")
        return resolved

    def filename(self, media_id: str) -> str | None:
        with self._lock:
            item = self._index["items"].get(media_id)
        return item["filename"] if item else None
