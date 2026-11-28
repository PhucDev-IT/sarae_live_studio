"""Loopback transport for studio development and native media previews.

Business operations live in StudioService; neither transport imports the SaaS app.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import queue
import re
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from sarae_agent.ipc import protocol
from sarae_agent.studio.service import StudioError, StudioService

METHODS = (
    "studio.status", "studio.products.list", "studio.products.update", "studio.products.delete",
    "studio.tiktok.status", "studio.tiktok.login", "studio.tiktok.connect", "studio.tiktok.sync",
    "studio.presenters.list", "studio.presenters.create", "studio.presenters.update", "studio.presenters.delete",
    "studio.media.list", "studio.media.import", "studio.media.update", "studio.media.delete", "studio.media.url", "studio.media.inspect", "studio.media.thumbnail",
    "studio.media.markers.list", "studio.media.markers.set", "studio.media.overlays.list", "studio.media.overlays.set",
    "studio.overlays.templates", "studio.overlays.assets.list", "studio.overlays.assets.import",
    "studio.overlays.assets.delete",
    "studio.ai.config", "studio.ai.configure", "studio.ai.set_key", "studio.ai.delete_key", "studio.ai.test",
    "studio.ai.reply", "studio.ai.product_analyze", "studio.tiktok.detail", "studio.products.detail",
    "studio.obs.status", "studio.obs.configure", "studio.obs.connect", "studio.obs.disconnect",
    "studio.live.sessions.list", "studio.live.sessions.create", "studio.live.sessions.update",
    "studio.live.sessions.delete", "studio.live.sessions.overlay_issues", "studio.live.status", "studio.live.start",
    "studio.live.pause", "studio.live.resume", "studio.live.next", "studio.live.previous", "studio.live.seek",
    "studio.live.stop", "studio.live.pins",
    "studio.replies.status", "studio.replies.configure", "studio.replies.chat_start", "studio.replies.chat_stop",
    "studio.replies.simulate", "studio.replies.speak", "studio.replies.send_comment", "studio.replies.cancel",
    "studio.replies.edit", "studio.replies.hold",
    "studio.replies.dismiss", "studio.replies.stop_voice", "studio.replies.voices", "studio.replies.voices_start",
    "studio.account.status", "studio.account.activate", "studio.account.check", "studio.account.deactivate",
)
ACCOUNT_METHODS = {name: name.rsplit(".", 1)[-1] for name in METHODS if name.startswith("studio.account.")}
# Answered without a license: the account screen itself, Core status, and stopping playback (never harmful).
UNLICENSED_METHODS = frozenset({*ACCOUNT_METHODS, "studio.status", "studio.live.stop"})
REPLY_METHODS = {name: name.rsplit(".", 1)[-1] for name in METHODS if name.startswith("studio.replies.")}
LIVE_METHODS = {"studio.obs.status": "obs_status", "studio.obs.configure": "obs_configure",
                "studio.obs.connect": "obs_connect", "studio.obs.disconnect": "obs_disconnect",
                "studio.live.sessions.list": "sessions_list", "studio.live.sessions.create": "sessions_create",
                "studio.live.sessions.update": "sessions_update", "studio.live.sessions.delete": "sessions_delete",
                "studio.live.status": "status", "studio.live.start": "start",
                "studio.live.pause": "pause", "studio.live.resume": "resume", "studio.live.next": "next",
                "studio.live.previous": "previous", "studio.live.seek": "seek",
                "studio.live.stop": "stop", "studio.live.pins": "configure_pins"}
READ_ONLY_LIVE = {"studio.obs.status", "studio.live.sessions.list", "studio.live.status"}
# Provider calls run outside the service lock, on the transport's worker thread.
AI_METHODS = {"studio.ai.config": "config", "studio.ai.configure": "configure", "studio.ai.set_key": "set_key",
              "studio.ai.delete_key": "delete_key", "studio.ai.test": "test", "studio.ai.reply": "reply",
              "studio.ai.product_analyze": "product_analyze"}
MAX_UPLOAD = 20 * 1024**3
DEFAULT_ORIGINS = frozenset({"http://127.0.0.1:1420", "http://localhost:1420"})


def error_info(exc: Exception) -> dict:
    if isinstance(exc, StudioError):
        return {"code": exc.code, "message": exc.message, "details": exc.details}
    return {"code": "INTERNAL_ERROR", "message": "Không hoàn tất được thao tác local."}


class StudioTransport:
    def __init__(self, service: StudioService, *, native: bool = False):
        self.service = service
        self.native = native
        self.token = secrets.token_urlsafe(32)
        self.base_url = ""
        self._listeners: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self.stopped = threading.Event()
        self.ipc_emit = None
        self.tiktok = None  # TikTokConnector, attached by the runtime
        self.ai = None  # StudioAI, attached by the runtime
        self.live = None  # StudioLive, attached by the runtime
        self.replies = None  # ReplyDesk, attached by the runtime
        self.account = None  # StudioAccount, attached by the runtime; None (tests, tools) means no license gate

    def _thumbnail_url(self, product: dict) -> dict:
        has_thumbnail = product.pop("has_thumbnail", False)
        url = f"{self.base_url}/studio/thumbnails/{quote(product['id'], safe='')}?token={self.token}"
        return {**product, "thumbnail_url": url if has_thumbnail and self.base_url else None}

    def _media_thumbnail_url(self, media: dict) -> dict:
        url = f"{self.base_url}/studio/media-thumbnails/{quote(media['id'], safe='')}?token={self.token}&v={quote(media['sha256'], safe='')}"
        return {**media, "thumbnail_url": url if media.get("has_thumbnail") and self.base_url else None}

    def _asset_url(self, asset_id: str) -> str:
        return f"/api/studio-assets/{quote(asset_id, safe='')}?token={self.token}"

    def _scene_urls(self, scene: dict) -> dict:
        """The editor loads scene images from this server: `/api/…` paths with the session token (the renderer
        resolves them against the core's base URL). The OBS renderer gets its own cached copies instead."""
        scene = {**scene, "products": {k: dict(v) for k, v in scene.get("products", {}).items()},
                 "assets": {k: dict(v) for k, v in scene.get("assets", {}).items()}}
        for item in [*scene["products"].values(), *scene["assets"].values()]:
            for key in ("image_url", "url"):
                if isinstance(item.get(key), str) and item[key].startswith("/api/"):
                    item[key] = f"{item[key]}?token={self.token}"
        scene["api_base"] = self.base_url
        return scene

    def live_running(self) -> bool:
        """A LIVE session is playing now (also while paused)."""
        engine = getattr(self.live, "engine", None)
        return bool(getattr(engine, "active", False))

    def _require_license(self, method: str) -> None:
        """docs/HYBRID_STUDIO_SERVER.md: without a valid license only the account screen works — but a running
        LIVE is never cut. Everything that session uses keeps working; only a new LIVE cannot start."""
        if self.account is None or method in UNLICENSED_METHODS or self.account.licensed():
            return
        reason = self.account.status().get("reason")
        if self.live_running():
            if method == "studio.live.start":
                raise StudioError("LICENSE_REQUIRED", "License chưa hợp lệ nên không bắt đầu được phiên LIVE mới. "
                                  "Phiên đang phát vẫn chạy tiếp đến khi kết thúc.", {"reason": reason})
            return
        raise StudioError("LICENSE_REQUIRED", "Cần kích hoạt máy và có license Sarae còn hạn để dùng Studio.",
                          {"reason": reason})

    def dispatch(self, method: str, params: dict) -> dict:
        if method not in METHODS:
            raise StudioError("METHOD_NOT_FOUND", "Phương thức studio không được hỗ trợ.")
        self._require_license(method)
        if method in ACCOUNT_METHODS:
            if self.account is None:
                raise StudioError("ACCOUNT_UNAVAILABLE", "Kích hoạt máy chưa sẵn sàng trong Studio Core.")
            result = getattr(self.account, ACCOUNT_METHODS[method])(params)
            if method in ("studio.account.activate", "studio.account.check") and result.get("licensed") \
                    and self.replies is not None:
                # The cloned voices come from the server this machine just reached.
                threading.Thread(target=self.replies.voice_server.refresh, name="studio-voice-catalog", daemon=True).start()
            return result
        if method == "studio.products.detail":
            if set(params) != {"id"}:
                raise StudioError("VALIDATION_ERROR", "Cần mã sản phẩm trong Studio.")
            self.service.product_context(params["id"])
            return {"detail": self.service.get_setting("product_detail:" + params["id"], {}) or None}
        if method.startswith("studio.tiktok."):
            if params and method != "studio.tiktok.detail":
                raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, params))})
            if self.tiktok is None:
                raise StudioError("TIKTOK_UNAVAILABLE", "Kết nối TikTok chưa sẵn sàng trong Studio Core.")
            result = (self.tiktok.detail(params) if method == "studio.tiktok.detail"
                      else getattr(self.tiktok, method.rsplit(".", 1)[-1])())
            if method == "studio.tiktok.sync":
                self.emit("studio.changed", {"method": method})
            return result
        if method in AI_METHODS:
            if self.ai is None:
                raise StudioError("AI_UNAVAILABLE", "AI chưa sẵn sàng trong Studio Core.")
            result = getattr(self.ai, AI_METHODS[method])(params)
            if method in {"studio.ai.configure", "studio.ai.set_key", "studio.ai.delete_key", "studio.scripts.generate"}:
                self.emit("studio.changed", {"method": method})
            return result
        if method in REPLY_METHODS:
            if self.replies is None:
                raise StudioError("REPLIES_UNAVAILABLE", "Bàn trả lời chưa sẵn sàng trong Studio Core.")
            return getattr(self.replies, REPLY_METHODS[method])(params)
        if method in LIVE_METHODS:
            if self.live is None:
                raise StudioError("LIVE_UNAVAILABLE", "Bộ phát LIVE chưa sẵn sàng trong Studio Core.")
            result = getattr(self.live, LIVE_METHODS[method])(params)
            if method not in READ_ONLY_LIVE:
                self.emit("studio.changed", {"method": method})
            return result
        if method == "studio.media.url":
            if set(params) != {"id"} or not isinstance(params["id"], str):
                raise StudioError("INVALID_PARAMS", "Cần mã video hợp lệ.")
            self.service.resolve_media(params["id"])
            return {"url": f"{self.base_url}/studio/media/{quote(params['id'], safe='')}?token={self.token}"}
        result = self.service.dispatch(method, params)
        if method in {"studio.media.inspect", "studio.media.thumbnail"}:
            self.emit("studio.changed", {"method": method})
        if method == "studio.status":
            result = {**result, "capabilities": {
                **result.get("capabilities", {}), "native_dialog": self.native,
                "local_file_reference": False, "obs": self.live is not None, "ai": self.ai is not None,
                "tiktok_sync": self.tiktok is not None,
            }}
        if method == "studio.products.list":
            result = {"products": [self._thumbnail_url(product) for product in result["products"]]}
        elif method == "studio.products.update":
            result = {"product": self._thumbnail_url(result["product"])}
        elif method == "studio.media.list":
            result = {"media": [self._media_thumbnail_url(media) for media in result["media"]]}
        elif method.startswith("studio.media.") and "media" in result:
            result = {"media": self._media_thumbnail_url(result["media"])}
        if method in ("studio.media.overlays.list", "studio.media.overlays.set"):
            result = {**result, "scene": self._scene_urls(result["scene"])}
        elif method == "studio.overlays.assets.list":
            result = {"assets": [{**asset, "url": self._asset_url(asset["id"])} for asset in result["assets"]]}
        if method.rsplit(".", 1)[-1] in {"create", "update", "delete", "import"} or method in (
                "studio.media.markers.set", "studio.media.overlays.set"):
            self.emit("studio.changed", {"method": method})
            if self.live is not None and method.split(".")[1] in {"media", "products"}:
                # A running session pins by the new markers from its current position.
                self.live.refresh()
        return result

    def emit(self, name: str, data: dict) -> None:
        frame = protocol.event(name, data)
        if self.ipc_emit:
            self.ipc_emit(name, data)
        with self._lock:
            for listener in self._listeners:
                try:
                    listener.put_nowait(frame)
                except queue.Full:
                    # Events invalidate lists; keeping the latest is sufficient.
                    try:
                        listener.get_nowait()
                        listener.put_nowait(frame)
                    except (queue.Empty, queue.Full):
                        pass

    def add_listener(self) -> queue.Queue:
        listener = queue.Queue(maxsize=32)
        with self._lock:
            if len(self._listeners) >= 16:
                raise StudioError("TOO_MANY_CLIENTS", "Đã mở quá nhiều kết nối studio.")
            self._listeners.add(listener)
        return listener

    def remove_listener(self, listener: queue.Queue) -> None:
        with self._lock:
            self._listeners.discard(listener)


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, transport: StudioTransport, port: int = 0):
        self.transport = transport
        super().__init__(("127.0.0.1", port), StudioHandler)
        transport.base_url = f"http://127.0.0.1:{self.server_port}"


class StudioHandler(BaseHTTPRequestHandler):
    server: StudioHTTPServer
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, _format, *args):
        # Never log bearer tokens, filenames or user data from request URLs.
        pass

    @property
    def transport(self) -> StudioTransport:
        return self.server.transport

    def _origin_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        native_origin = self.transport.native and origin in {"http://tauri.localhost", "tauri://localhost"}
        return (host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                and (origin is None or origin in DEFAULT_ORIGINS or origin == self.transport.base_url or native_origin))

    def _headers(self, status: int, length: int | None, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        origin = self.headers.get("Origin")
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if length is not None:
            self.send_header("Content-Length", str(length))

    def _json(self, status: int, value: dict):
        body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8")
        self._headers(status, len(body), "application/json; charset=utf-8")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, code: str, message: str):
        # Error requests can have an unread body; don't reuse their connection.
        self.close_connection = True
        self._json(status, {"ok": False, "result": None, "error": {"code": code, "message": message}})

    def _authorize(self, *, query_token: bool = False) -> bool:
        if not self._origin_allowed():
            self._error(403, "ORIGIN_DENIED", "Nguồn kết nối không được phép.")
            return False
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if query_token:
            token = parse_qs(urlsplit(self.path).query).get("token", [token])[0]
        if not hmac.compare_digest(token, self.transport.token):
            self._error(401, "TOKEN_REQUIRED", "Phiên kết nối local đã hết hạn; hãy kết nối lại.")
            return False
        return True

    def do_OPTIONS(self):
        if not self._origin_allowed() or self.transport.native:
            self._error(403, "ORIGIN_DENIED", "Nguồn kết nối không được phép.")
            return
        self._headers(204, 0, "text/plain")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Studio-Client")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/studio/session" and not self.transport.native:
            # Custom header forces cross-origin browsers through a CORS preflight.
            if not self._origin_allowed() or self.headers.get("X-Studio-Client") != "1":
                self._error(403, "ORIGIN_DENIED", "Nguồn kết nối không được phép.")
                return
            self._json(200, {"token": self.transport.token, "base_url": self.transport.base_url})
            return
        if path.startswith("/studio/media/"):
            if self._authorize(query_token=True):
                self._media(path.removeprefix("/studio/media/"))
            return
        if path.startswith("/studio/thumbnails/"):
            if self._authorize(query_token=True):
                self._thumbnail(path.removeprefix("/studio/thumbnails/"))
            return
        if path.startswith("/api/studio-assets/") or path.startswith("/api/studio-products/"):
            if self._authorize(query_token=True):
                self._overlay_image(path)
            return
        if path.startswith("/studio/media-thumbnails/"):
            if self._authorize(query_token=True):
                self._media_thumbnail(path.removeprefix("/studio/media-thumbnails/"))
            return
        if path == "/studio/events" and not self.transport.native:
            if self._authorize(query_token=True):
                self._events()
            return
        self._error(404, "NOT_FOUND", "Đường dẫn không tồn tại.")

    def do_POST(self):
        if self.transport.native:
            # Drain a small unread body first: closing a socket with pending
            # input makes Windows reset it before the client reads the 404.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 64 * 1024:
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            self._error(404, "NOT_FOUND", "Bản native dùng IPC cho nghiệp vụ.")
            return
        if not self._authorize():
            return
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if self.headers.get("Transfer-Encoding") or length < 0:
                self._error(411, "LENGTH_REQUIRED", "Thiếu dung lượng yêu cầu.")
                return
            if path == "/studio/rpc":
                if length > protocol.MAX_FRAME_BYTES:
                    self._error(413, "FRAME_TOO_LARGE", "Yêu cầu vượt quá 1 MB.")
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete body")
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) - {"method", "params"}:
                    raise ValueError("Invalid request")
                method, params = payload.get("method"), payload.get("params", {})
                if not isinstance(method, str) or not isinstance(params, dict):
                    raise ValueError("Invalid request")
                if method == "studio.media.import":
                    raise StudioError("NATIVE_ONLY", "Trình duyệt phải chọn và gửi file qua chức năng nhập video.")
                result = self.transport.dispatch(method, params)
            elif path == "/studio/overlay-assets":
                if not 0 < length <= 5 * 1024 * 1024:
                    self._error(413, "UPLOAD_TOO_LARGE", "Ảnh overlay tối đa 5 MB.")
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise StudioError("UPLOAD_INTERRUPTED", "Ảnh chưa được gửi đầy đủ; hãy chọn lại.")
                name = parse_qs(urlsplit(self.path).query).get("filename", ["image"])[0]
                asset = self.transport.service.add_overlay_asset(body, name)
                self.transport.emit("studio.changed", {"method": "studio.overlays.assets.import"})
                result = {"asset": {**asset, "url": self.transport._asset_url(asset["id"])}}
            elif path == "/studio/upload":
                if not 0 < length <= MAX_UPLOAD:
                    self._error(413, "UPLOAD_TOO_LARGE", "Video phải có dung lượng từ 1 byte đến 20 GB.")
                    return
                result = self._upload(length)
            else:
                self._error(404, "NOT_FOUND", "Đường dẫn không tồn tại.")
                return
            self._json(200, {"ok": True, "result": result, "error": None})
        except StudioError as exc:
            self.close_connection = True
            self._json(422, {"ok": False, "result": None, "error": error_info(exc)})
        except (ValueError, UnicodeError):
            self._error(400, "INVALID_PARAMS", "Yêu cầu local không hợp lệ.")
        except (OSError, TimeoutError):
            self._error(500, "IO_ERROR", "Không đọc/ghi được dữ liệu; kiểm tra file và dung lượng ổ đĩa.")
        except Exception:
            self._error(500, "INTERNAL_ERROR", "Không hoàn tất được thao tác local.")

    def _upload(self, length: int) -> dict:
        params = parse_qs(urlsplit(self.path).query)
        filename = params.get("filename", [""])[0]
        if (not filename or filename in {".", ".."} or len(filename) > 255
                or Path(filename).name != filename or any(char in filename for char in '\\/:\x00')
                or filename.rstrip(" .") != filename):
            raise StudioError("INVALID_PARAMS", "Tên file không hợp lệ.")
        # Stage under the same data volume. Import publishes only a validated full file.
        data_dir = Path(self.transport.service.dispatch("studio.status", {})["data_dir"])
        staging = data_dir / "imports"
        staging.mkdir(exist_ok=True)
        if staging.is_symlink() or staging.resolve() != staging:
            raise StudioError("UNSAFE_PATH", "Thư mục tạm không được trỏ ra ngoài dữ liệu studio.")
        with tempfile.TemporaryDirectory(prefix="upload-", dir=staging) as temporary:
            source = Path(temporary) / filename
            with source.open("xb") as target:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise StudioError("UPLOAD_INTERRUPTED", "Video chưa được gửi đầy đủ; hãy nhập lại.")
                    target.write(chunk)
                    remaining -= len(chunk)
                target.flush()
                os.fsync(target.fileno())
            args = {"source_path": str(source)}
            if "product_id" in params:
                args["product_id"] = params["product_id"][0]
            if "presenter_id" in params:
                # Repeated query key: ?presenter_id=a&presenter_id=b
                args["presenter_ids"] = params["presenter_id"]
            return self.transport.dispatch("studio.media.import", args)

    def _media_thumbnail(self, media_id: str):
        try:
            with self.transport.service.resolve_media_thumbnail(media_id).open("rb") as source:
                data = source.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                raise ValueError("Invalid cached thumbnail")
        except (StudioError, OSError, ValueError):
            self._error(404, "THUMBNAIL_MISSING", "Ảnh bìa video chưa được tạo hoặc không còn trong cache.")
            return
        self._headers(200, len(data), "image/jpeg")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _overlay_image(self, path: str):
        try:
            data = self.transport.service.overlay_image(path)
        except (StudioError, OSError, ValueError):
            self._error(404, "NOT_FOUND", "Ảnh overlay không còn trên máy.")
            return
        content_type = {b"\xff\xd8": "image/jpeg", b"\x89P": "image/png"}.get(data[:2], "image/webp")
        self._headers(200, len(data), content_type)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _thumbnail(self, product_id: str):
        try:
            data = self.transport.service.resolve_thumbnail(product_id).read_bytes()
        except (StudioError, OSError, ValueError):
            self._error(404, "NOT_FOUND", "Sản phẩm chưa có ảnh trên máy.")
            return
        content_type = {b"\xff\xd8": "image/jpeg", b"\x89P": "image/png"}.get(data[:2], "image/webp")
        self._headers(200, len(data), content_type)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _media(self, media_id: str):
        try:
            path = self.transport.service.resolve_media(media_id)
            source = path.open("rb")
        except (StudioError, OSError, ValueError):
            self._error(404, "MEDIA_MISSING", "Video không còn trong thư viện local.")
            return
        with source:
            size = os.fstat(source.fileno()).st_size
            start, end, status = 0, size - 1, 200
            requested = self.headers.get("Range")
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()):
                    self._range_error(size)
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                if start > end or start >= size:
                    self._range_error(size)
                    return
                status = 206
            self._headers(status, end - start + 1, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            source.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (ConnectionError, OSError):
                self.close_connection = True

    def _range_error(self, size: int):
        self._headers(416, 0, "text/plain")
        self.send_header("Content-Range", f"bytes */{size}")
        self.end_headers()

    def _events(self):
        try:
            listener = self.transport.add_listener()
        except StudioError as exc:
            self._error(429, exc.code, exc.message)
            return
        self._headers(200, None, "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not self.transport.stopped.is_set():
                try:
                    frame = listener.get(timeout=1)
                    payload = json.dumps(frame, ensure_ascii=True).encode("ascii")
                    self.wfile.write(b"data: " + payload + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            self.transport.remove_listener(listener)
