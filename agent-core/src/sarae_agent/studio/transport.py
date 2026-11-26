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
    "studio.status", "studio.products.list", "studio.products.create",
    "studio.products.update", "studio.products.delete", "studio.media.list",
    "studio.media.import", "studio.media.delete", "studio.media.url",
)
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

    def dispatch(self, method: str, params: dict) -> dict:
        if method not in METHODS:
            raise StudioError("METHOD_NOT_FOUND", "Phương thức studio không được hỗ trợ.")
        if method == "studio.media.url":
            if set(params) != {"id"} or not isinstance(params["id"], str):
                raise StudioError("INVALID_PARAMS", "Cần mã video hợp lệ.")
            self.service.resolve_media(params["id"])
            return {"url": f"{self.base_url}/studio/media/{quote(params['id'], safe='')}?token={self.token}"}
        result = self.service.dispatch(method, params)
        if method == "studio.status":
            result = {**result, "capabilities": {
                **result.get("capabilities", {}), "native_dialog": self.native,
                "local_file_reference": False, "obs": False, "ai": False,
            }}
        if method.rsplit(".", 1)[-1] in {"create", "update", "delete", "import"}:
            self.emit("studio.changed", {"method": method})
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
        if path == "/studio/events" and not self.transport.native:
            if self._authorize(query_token=True):
                self._events()
            return
        self._error(404, "NOT_FOUND", "Đường dẫn không tồn tại.")

    def do_POST(self):
        if self.transport.native:
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
            return self.transport.dispatch("studio.media.import", args)

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
