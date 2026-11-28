"""Device activation and license for the hybrid Studio (docs/HYBRID_STUDIO_SERVER.md).

- The customer pastes a one-time activation key; ``POST /api/identity/activate`` returns this machine's
  ``agent_id`` + ``agent_credential``. Both, and the last good license check (with ``checked_at``), are kept in
  one DPAPI-protected file in the studio data directory. The credential is never logged or returned.
- The license is checked at startup (background thread, never blocking) and then every 6 hours.
- Offline (network error, 5xx): the app stays open while the last good check is younger than ``grace_hours``
  (72 h). ``LICENSE_EXPIRED`` locks the app; ``AGENT_NOT_ACTIVATED``/``AGENT_UNAUTHORIZED``/``AGENT_REVOKED``
  forget the credential. A running LIVE is never cut: the transport keeps it working (see transport.py).
- ``SARAE_STUDIO_DEV_UNLICENSED=1`` bypasses the license for dev/test, with a warning in the log.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .models import StudioError

log = logging.getLogger(__name__)
DEFAULT_SERVER_URL = "https://api.puns.id.vn"
KEY_ENTROPY = b"SaraeLiveStudio/device/v1"
CHECK_SECONDS = 6 * 3600
RETRY_SECONDS = 10 * 60
DEFAULT_GRACE_HOURS = 72
# The local clock may be a little behind the time of the last check; further back is treated as tampering.
CLOCK_BACK_TOLERANCE = 10 * 60
AUTH_CODES = frozenset({"AGENT_NOT_ACTIVATED", "AGENT_UNAUTHORIZED", "AGENT_REVOKED"})
AGENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
ACTIVATION_KEY = re.compile(r"[\x21-\x7e]{6,200}")
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
MESSAGES = {
    "ACTIVATION_KEY_INVALID": "Mã kích hoạt không đúng. Kiểm tra lại mã Sarae đã gửi cho bạn.",
    "ACTIVATION_KEY_USED": "Mã kích hoạt này đã được dùng. Hãy xin Sarae một mã mới.",
    "AGENT_LIMIT_REACHED": "Tài khoản đã kích hoạt đủ số máy cho phép. Gỡ một máy cũ hoặc liên hệ Sarae để thêm máy.",
    "AGENT_NOT_ACTIVATED": "Máy này chưa được kích hoạt. Hãy nhập mã kích hoạt.",
    "AGENT_UNAUTHORIZED": "Thông tin kích hoạt của máy không còn hợp lệ. Hãy kích hoạt lại.",
    "AGENT_REVOKED": "Máy này đã bị gỡ khỏi tài khoản Sarae. Hãy nhập mã kích hoạt mới.",
    "LICENSE_EXPIRED": "License Sarae đã hết hạn. Liên hệ Sarae để gia hạn.",
    "OFFLINE_GRACE_EXPIRED": "Đã quá 72 giờ chưa kiểm tra được license với server Sarae. Kết nối mạng rồi bấm Kiểm tra lại.",
    "LICENSE_UNCHECKED": "Chưa kiểm tra được license với server Sarae. Kết nối mạng rồi bấm Kiểm tra lại.",
    "SERVER_UNREACHABLE": "Không kết nối được server Sarae. Kiểm tra mạng rồi thử lại.",
    "SERVER_URL_INVALID": "SARAE_STUDIO_SERVER_URL phải dùng https:// (http:// chỉ cho localhost).",
    "RATE_LIMITED": "Thử quá nhiều lần. Hãy đợi một lúc rồi thử lại.",
    "CREDENTIAL_UNREADABLE": "Không đọc được thông tin kích hoạt đã lưu (khác Windows user hoặc máy khác). Hãy kích hoạt lại.",
}


def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch is not None else None


def _epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def validate_server_url(raw: str) -> str:
    """https only; plain http is allowed only for a gateway on this machine. No credentials, query or fragment."""
    url = raw.strip().rstrip("/")
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        parts = None
    if (parts is None or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
            or not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK))):
        raise StudioError("SERVER_URL_INVALID", MESSAGES["SERVER_URL_INVALID"])
    return url


def dev_unlicensed(environ=None) -> bool:
    return (environ if environ is not None else os.environ).get("SARAE_STUDIO_DEV_UNLICENSED", "").strip() == "1"


class ServerError(Exception):
    """A gateway answer that is not 200: its ``X-Sarae-Error`` code (or a local one for network trouble)."""

    def __init__(self, code: str, status: int | None, detail: str = "", retry_after: float | None = None):
        super().__init__(code)
        self.code, self.status, self.detail, self.retry_after = code, status, detail, retry_after

    @property
    def offline(self) -> bool:
        """Network trouble or a server-side failure: the grace window applies."""
        return self.code == "SERVER_UNREACHABLE" or (self.status or 0) >= 500 or self.code == "RATE_LIMITED"


def server_error(response: httpx.Response) -> ServerError:
    code = (response.headers.get("X-Sarae-Error") or "").strip().upper()[:64]
    detail = ""
    try:
        body = json.loads(response.read()[:8192])
        if isinstance(body, dict) and isinstance(body.get("detail"), str):
            detail = body["detail"].strip()[:300]
    except (ValueError, httpx.HTTPError):
        pass
    retry_after = None
    try:
        retry_after = max(0.0, float(response.headers.get("Retry-After", "")))
    except ValueError:
        pass
    if not code:
        code = "SERVER_UNREACHABLE" if response.status_code >= 500 else f"HTTP_{response.status_code}"
    return ServerError(code, response.status_code, detail, retry_after)


class StudioAccount:
    """This machine's activation and license. Thread-safe; network calls run outside the state lock."""

    def __init__(self, data_dir: Path, *, transport: httpx.BaseTransport | None = None,
                 protect: Callable[[bytes], bytes] | None = None, unprotect: Callable[[bytes], bytes] | None = None,
                 clock: Callable[[], float] = time.time, emit: Callable[[str, dict], None] = lambda *_: None,
                 environ: dict | None = None, hostname: str | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.transport = transport
        if protect is None or unprotect is None:
            from sarae_agent.activation import dpapi
            protect = protect or (lambda data: dpapi.protect(data, entropy=KEY_ENTROPY, description="Sarae Live Studio"))
            unprotect = unprotect or (lambda data: dpapi.unprotect(data, entropy=KEY_ENTROPY))
        self._protect, self._unprotect = protect, unprotect
        self._clock = clock
        self.emit = emit
        environ = os.environ if environ is None else environ
        self.dev_bypass = dev_unlicensed(environ)
        self.raw_server_url = (environ.get("SARAE_STUDIO_SERVER_URL") or DEFAULT_SERVER_URL).strip()
        self.hostname = (hostname or socket.gethostname() or "studio")[:100]
        # Set by the runtime: whether a LIVE session is playing (the UI shows a banner instead of the lock).
        self.live_running: Callable[[], bool] = lambda: False
        self._lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._checking = False
        self._next_check = 0.0
        self._last_attempt: float | None = None
        self._last_error: dict | None = None
        self._notice: str | None = None  # why the machine is no longer activated
        self._published: tuple | None = None
        self._record = self._load()
        if self.dev_bypass:
            log.warning("SARAE_STUDIO_DEV_UNLICENSED=1: Studio chạy không kiểm tra license (chỉ dành cho dev/test).")

    # --- storage -------------------------------------------------------------------------
    @property
    def path(self) -> Path:
        folder = self.data_dir / "secrets"
        folder.mkdir(parents=True, exist_ok=True)
        if folder.is_symlink() or folder.resolve() != folder:
            raise StudioError("UNSAFE_PATH", "The secrets directory points outside the studio data directory.")
        return folder / "studio-device.dpapi.json"

    def _load(self) -> dict | None:
        try:
            path = self.path
            if not path.is_file():
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = json.loads(self._unprotect(base64.b64decode(payload["blob"])).decode("utf-8"))
            if not isinstance(record, dict) or not AGENT_ID.fullmatch(str(record.get("agent_id", ""))) \
                    or not isinstance(record.get("agent_credential"), str) or not record["agent_credential"]:
                raise ValueError("Incomplete device record")
            return record
        except Exception:  # noqa: BLE001 - another Windows user/machine or a damaged file: activate again
            log.warning("Không đọc được thông tin kích hoạt Studio đã lưu; cần kích hoạt lại.")
            self._notice = "CREDENTIAL_UNREADABLE"
            return None

    def _save(self, record: dict) -> None:
        blob = self._protect(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        path = self.path
        temporary = path.with_suffix(".partial")
        temporary.write_text(json.dumps({"format": "sarae-studio-dpapi", "version": 1,
                                         "blob": base64.b64encode(blob).decode("ascii")}), encoding="utf-8")
        temporary.replace(path)

    def _forget(self, notice: str | None, agent_id: str | None = None) -> None:
        with self._lock:
            if agent_id is not None and (self._record or {}).get("agent_id") != agent_id:
                return  # re-activated meanwhile: the refusal was for the previous credential
            self._record = None
            self._notice = notice
            self._last_error = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            log.warning("Không xóa được file kích hoạt Studio cũ.")

    # --- state ---------------------------------------------------------------------------
    def server_url(self) -> str:
        return validate_server_url(self.raw_server_url)

    def _record_for_server(self) -> dict | None:
        """A credential belongs to the server it was issued by; another SARAE_STUDIO_SERVER_URL needs activation."""
        record = self._record
        if record is None:
            return None
        try:
            current = self.server_url()
        except StudioError:
            return None
        return record if record.get("server_url") == current else None

    def _evaluate(self, now: float) -> tuple[str, str | None, float | None]:
        """(state, reason, grace_until) — state: unactivated, active, offline, locked."""
        record = self._record_for_server()
        if record is None:
            return "unactivated", self._notice, None
        lock = record.get("lock")
        if isinstance(lock, dict) and lock.get("code"):
            return "locked", str(lock["code"]), None
        checked = record.get("checked_at")
        if not isinstance(checked, (int, float)):
            return "locked", "LICENSE_UNCHECKED", None
        license_ = record.get("license") or {}
        grace = license_.get("grace_hours", DEFAULT_GRACE_HOURS)
        grace = DEFAULT_GRACE_HOURS if isinstance(grace, bool) or not isinstance(grace, (int, float)) else min(max(grace, 0), 168)
        grace_until = checked + grace * 3600
        if now > grace_until or now < checked - CLOCK_BACK_TOLERANCE:
            return "locked", "OFFLINE_GRACE_EXPIRED", None
        expires = _epoch(license_.get("expires_at"))
        offset = record.get("server_offset", 0.0)
        server_now = now + (offset if isinstance(offset, (int, float)) else 0.0)
        if expires is not None and server_now >= expires:
            return "locked", "LICENSE_EXPIRED", None
        if self._last_error and self._last_error.get("offline") and (self._last_attempt or 0) >= checked:
            return "offline", self._last_error["code"], grace_until
        return "active", None, grace_until

    def licensed(self) -> bool:
        if self.dev_bypass:
            return True
        with self._lock:
            return self._evaluate(self._clock())[0] in ("active", "offline")

    def status(self, params: dict | None = None) -> dict:
        if params:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, params))})
        now = self._clock()
        with self._lock:
            state, reason, grace_until = self._evaluate(now)
            record = self._record_for_server() or {}
            license_ = record.get("license") or {}
            error = {k: v for k, v in self._last_error.items() if k in ("code", "message")} if self._last_error else None
            result = {
                "state": state, "licensed": self.dev_bypass or state in ("active", "offline"),
                "dev_bypass": self.dev_bypass, "reason": reason, "message": MESSAGES.get(reason or "", None),
                "activated": bool(record), "server_url": self.raw_server_url, "agent_id": record.get("agent_id"),
                "device_name": license_.get("hostname") or record.get("hostname") or self.hostname,
                "email": license_.get("email"), "expires_at": license_.get("expires_at"),
                "max_agents": license_.get("max_agents"), "activated_at": _iso(record.get("activated_at")),
                "checked_at": _iso(record.get("checked_at")), "last_attempt_at": _iso(self._last_attempt),
                "grace_until": _iso(grace_until) if state == "offline" else None,
                "last_error": error, "checking": self._checking,
            }
        try:
            result["live_running"] = bool(self.live_running())
        except Exception:  # noqa: BLE001 - status never fails because of the LIVE layer
            result["live_running"] = False
        return result

    def _changed(self) -> None:
        status = self.status()
        with self._lock:
            self._published = (status["state"], status["licensed"], status["reason"])
        self.emit("studio.account.changed", status)

    def auth(self) -> tuple[str, dict] | None:
        """(server URL, device headers) for the gateway, or None when this machine is not activated."""
        with self._lock:
            record = self._record_for_server()
            if record is None:
                return None
            return record["server_url"], {"X-Sarae-Agent": record["agent_id"],
                                          "Authorization": "Bearer " + record["agent_credential"]}

    def report(self, code: str) -> None:
        """Another gateway call (voices, TTS) was refused for this device or license: apply the same rules."""
        if code in AUTH_CODES:
            log.warning("Server Sarae từ chối máy này (%s); cần kích hoạt lại.", code)
            self._forget(code)
            self._changed()
        elif code == "LICENSE_EXPIRED":
            self._lock_record(code)
            self._changed()

    def _lock_record(self, code: str, agent_id: str | None = None) -> None:
        with self._lock:
            if self._record is None or (agent_id is not None and self._record.get("agent_id") != agent_id):
                return
            self._record = {**self._record, "lock": {"code": code}}
            record = dict(self._record)
        try:
            self._save(record)
        except Exception:  # noqa: BLE001 - the lock still holds in memory
            log.warning("Không lưu được trạng thái license.")

    # --- network -------------------------------------------------------------------------
    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
                            follow_redirects=False)

    def _request(self, method: str, path: str, *, headers: dict | None = None, body: dict | None = None,
                 timeout: float = 15) -> dict:
        base = self.server_url()
        try:
            with self._client(timeout) as client:
                response = client.request(method, base + path, headers=headers, json=body)
                if response.status_code != 200:
                    raise server_error(response)
                value = json.loads(response.read()[:65536])
        except httpx.HTTPError as exc:
            raise ServerError("SERVER_UNREACHABLE", None) from exc
        except ValueError as exc:
            raise ServerError("SERVER_UNREACHABLE", 200, "Server trả dữ liệu không hợp lệ.") from exc
        if not isinstance(value, dict):
            raise ServerError("SERVER_UNREACHABLE", 200, "Server trả dữ liệu không hợp lệ.")
        return value

    def activate(self, params: dict) -> dict:
        if set(params) != {"activation_key"} or not isinstance(params.get("activation_key"), str):
            raise StudioError("VALIDATION_ERROR", "Hãy nhập mã kích hoạt.", {"field": "activation_key"})
        key = params["activation_key"].strip()
        if not ACTIVATION_KEY.fullmatch(key):
            raise StudioError("ACTIVATION_KEY_INVALID", MESSAGES["ACTIVATION_KEY_INVALID"], {"field": "activation_key"})
        base = self.server_url()
        try:
            result = self._request("POST", "/api/identity/activate", timeout=20,
                                   body={"activation_key": key, "hostname": self.hostname, "platform": "WINDOWS"})
        except ServerError as exc:
            raise self._studio_error(exc) from None
        agent_id, credential = result.get("agent_id"), result.get("agent_credential")
        if not isinstance(agent_id, str) or not AGENT_ID.fullmatch(agent_id) or not isinstance(credential, str) \
                or not 8 <= len(credential) <= 2000 or any(ch.isspace() for ch in credential):
            raise StudioError("SERVER_UNREACHABLE", "Server Sarae trả thông tin kích hoạt không hợp lệ.")
        record = {"server_url": base, "agent_id": agent_id, "agent_credential": credential,
                  "owner_id": str(result.get("owner_id") or "")[:100], "hostname": self.hostname,
                  "activated_at": self._clock(), "checked_at": None, "license": None, "lock": None}
        self._save(record)
        with self._lock:
            self._record, self._notice, self._last_error = record, None, None
        log.info("Đã kích hoạt Studio trên máy này (agent %s).", agent_id)
        self.check()
        return self.status()

    def check(self, params: dict | None = None) -> dict:
        """Ask the gateway for the license now (operator button, startup, every 6 h)."""
        if params:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, params))})
        auth = self.auth()
        if auth is None:
            raise StudioError("AGENT_NOT_ACTIVATED", MESSAGES["AGENT_NOT_ACTIVATED"])
        with self._check_lock:
            with self._lock:
                self._checking = True
            try:
                self._check(auth[1])
            finally:
                with self._lock:
                    self._checking = False
        self._changed()
        return self.status()

    def _check(self, headers: dict) -> None:
        now = self._clock()
        agent = headers["X-Sarae-Agent"]
        with self._lock:
            self._last_attempt = now
        try:
            value = self._request("GET", "/api/studio/v1/license", headers=headers)
        except StudioError as exc:  # SERVER_URL_INVALID
            with self._lock:
                self._last_error = {"code": exc.code, "message": exc.message, "offline": True}
                self._next_check = now + RETRY_SECONDS
            return
        except ServerError as exc:
            if exc.code in AUTH_CODES:
                log.warning("Kiểm tra license: server từ chối máy này (%s).", exc.code)
                self._forget(exc.code, agent)
                return
            if exc.code == "LICENSE_EXPIRED":
                log.warning("Kiểm tra license: license đã hết hạn.")
                self._lock_record(exc.code, agent)
                with self._lock:
                    self._last_error = None
                    self._next_check = now + RETRY_SECONDS
                return
            wait = exc.retry_after if exc.code == "RATE_LIMITED" and exc.retry_after else RETRY_SECONDS
            log.info("Kiểm tra license chưa được (%s); dùng lần kiểm tra hợp lệ gần nhất.", exc.code)
            with self._lock:
                self._last_error = {"code": exc.code if exc.offline else "SERVER_UNREACHABLE",
                                    "message": MESSAGES.get(exc.code) or exc.detail or MESSAGES["SERVER_UNREACHABLE"],
                                    "offline": True}
                self._next_check = now + min(max(wait, 30), RETRY_SECONDS)
            return
        if value.get("valid") is not True:
            self._lock_record("LICENSE_EXPIRED", agent)
            with self._lock:
                self._last_error = None
                self._next_check = now + RETRY_SECONDS
            return
        server_time = _epoch(value.get("server_time"))
        license_ = {"email": str(value.get("email") or "")[:200] or None,
                    "expires_at": value.get("expires_at") if _epoch(value.get("expires_at")) is not None else None,
                    "max_agents": value.get("max_agents") if isinstance(value.get("max_agents"), int) else None,
                    "agent_id": str(value.get("agent_id") or "")[:100] or None,
                    "hostname": str(value.get("hostname") or "")[:100] or None,
                    "grace_hours": value.get("grace_hours") if isinstance(value.get("grace_hours"), (int, float))
                    and not isinstance(value.get("grace_hours"), bool) else DEFAULT_GRACE_HOURS}
        with self._lock:
            if self._record is None or self._record.get("agent_id") != agent:
                return  # forgotten or re-activated meanwhile
            self._record = {**self._record, "license": license_, "checked_at": now, "lock": None,
                            "server_offset": (server_time - now) if server_time is not None else 0.0}
            record = dict(self._record)
            self._last_error = None
            self._next_check = now + CHECK_SECONDS
        self._save(record)

    def _studio_error(self, exc: ServerError) -> StudioError:
        if exc.code == "RATE_LIMITED" and exc.retry_after:
            message = f"Thử quá nhiều lần. Hãy đợi {int(exc.retry_after)} giây rồi thử lại."
        else:
            message = MESSAGES.get(exc.code) or exc.detail or MESSAGES["SERVER_UNREACHABLE"]
        return StudioError(exc.code if exc.code in MESSAGES else "ACTIVATION_FAILED", message)

    def deactivate(self, params: dict | None = None) -> dict:
        """Forget this machine's activation locally (the server keeps its record until an admin removes it)."""
        if params:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, params))})
        self._forget(None)
        log.info("Đã gỡ kích hoạt Studio trên máy này.")
        self._changed()
        return self.status()

    # --- background checks ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="studio-license", daemon=True)
            self._thread.start()

    def tick(self) -> None:
        """One step of the background loop: check when due, and tell the UI when the state changed by itself
        (grace window over, license date reached)."""
        due = self.auth() is not None and self._clock() >= self._next_check
        if due:
            try:
                self.check()
            except StudioError:
                pass
            except Exception:  # noqa: BLE001 - the loop must survive
                log.exception("Kiểm tra license lỗi không mong đợi")
                with self._lock:
                    self._next_check = self._clock() + RETRY_SECONDS
            return
        status = self.status()
        if (status["state"], status["licensed"], status["reason"]) != self._published:
            self._changed()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(max(1.0, min(60.0, self._next_check - self._clock())))

    def close(self) -> None:
        self._stop.set()
