"""Overlay images kept on this PC, so the OBS renderer never loads anything from the Internet during LIVE.

A snapshot names its images as signed Cloud URLs (overlay assets, our product photos) or https URLs of the TikTok
catalog. Each is downloaded once, checked (size, image type from its content, sha256 of overlay assets) and stored
under its content hash; the scene handed to the renderer points at the local copy instead
(`/api/overlay-files/<sha256>.<ext>`, served by the Agent's overlay server). An image that cannot be fetched is
reported as missing: the renderer then refuses READY and the overlay stays hidden.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger("sarae.overlay")

MAX_FILE_BYTES = 5 * 1024 * 1024
# All the images one snapshot may show (Phase 4 enforces the same budget on the Backend side).
MAX_SCENE_BYTES = 60 * 1024 * 1024
FILE_NAME = re.compile(r"^[0-9a-f]{64}\.(png|jpg|webp|gif)$")
INDEX_FILE = "index.json"


def image_type(data: bytes) -> str | None:
    """File extension from the image's own bytes (never from a header or a URL)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def url_key(url: str) -> str:
    """Cache key of an image URL: our signed URLs change their query (exp, sig) on every snapshot, the path does not."""
    parts = urlsplit(url)
    return f"{parts.path}" if url.startswith("/api/") else url


class OverlayCache:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def _index(self) -> dict:
        try:
            found = json.loads((self.root / INDEX_FILE).read_text(encoding="utf-8"))
            return found if isinstance(found, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self, index: dict) -> None:
        temp = self.root / (INDEX_FILE + ".tmp")
        temp.write_text(json.dumps(index), encoding="utf-8")
        temp.replace(self.root / INDEX_FILE)

    def path(self, name: str) -> Path | None:
        """A cached file by its served name, None for anything else (no path traversal possible)."""
        if not FILE_NAME.fullmatch(name):
            return None
        target = self.root / name
        return target if target.is_file() else None

    def _store(self, data: bytes, expected_sha: str | None) -> str:
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("ảnh lớn hơn 5 MB")
        kind = image_type(data)
        if kind is None:
            raise ValueError("nội dung không phải ảnh PNG/JPEG/WebP/GIF")
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha and digest != expected_sha.lower():
            raise ValueError("sha256 không khớp")
        name = f"{digest}.{kind}"
        target = self.root / name
        if not target.is_file():
            temp = self.root / f"{name}.tmp"
            temp.write_bytes(data)
            temp.replace(target)
        return name

    def localize(self, snapshot: dict, fetch: Callable[[str], bytes], token: str) -> tuple[dict, list[dict]]:
        """(scene for the renderer with every image pointing at its local copy, images that could not be cached).
        `fetch(url)` downloads one image (raises on failure)."""
        self.root.mkdir(parents=True, exist_ok=True)
        scene = copy.deepcopy(snapshot)
        missing: list[dict] = []
        total = 0
        with self._lock:
            index = self._index()

            def local(url: str | None, expected_sha: str | None, what: dict) -> str:
                nonlocal total
                if not url:
                    return ""
                key = url_key(url)
                name = index.get(key)
                cached = self.path(name) if isinstance(name, str) else None
                if cached is not None and expected_sha and not name.startswith(expected_sha.lower()):
                    cached = None  # the asset's content changed
                try:
                    if cached is None:
                        if not (url.startswith("/api/") or url.startswith("https://")):
                            raise ValueError("URL ảnh không được phép")
                        name = self._store(fetch(url), expected_sha)
                        index[key] = name
                        cached = self.root / name
                    total += cached.stat().st_size
                    if total > MAX_SCENE_BYTES:
                        raise ValueError("tổng dung lượng ảnh overlay vượt 60 MB")
                except Exception as exc:  # noqa: BLE001 - one image never stops the others
                    missing.append({**what, "error": str(exc)[:200]})
                    log.warning("Không tải được ảnh overlay %s: %s", what, exc)
                    return ""
                return f"/api/overlay-files/{name}?t={token}"

            for asset_id, asset in (scene.get("assets") or {}).items():
                asset["url"] = local(asset.get("url"), asset.get("sha256"), {"asset_id": asset_id})
            for product_id, product in (scene.get("products") or {}).items():
                if product.get("image_url"):
                    product["image_url"] = local(product["image_url"], None, {"product_id": product_id})
            self._save_index(index)
        return scene, missing
