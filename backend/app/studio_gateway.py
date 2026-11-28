"""Studio gateway API (docs/HYBRID_STUDIO_SERVER.md): license, cloned-voice catalog and TTS for customer Studios.

Only mounted by app.gateway_main. Every endpoint needs an activated device (`X-Sarae-Agent` + `Authorization:
Bearer <agent_credential>`, checked by identity.verify_agent). Errors carry `X-Sarae-Error: <CODE>` and a Vietnamese
`detail`. The voice server token never leaves this process; voice profiles are whitelisted to id/name/rev.

TTS queue: one sentence in flight per owner (TTS_BUSY), a global FIFO gate of SARAE_GATEWAY_TTS_CONCURRENCY slots
(waiting longer than QUEUE_TIMEOUT -> TTS_QUEUE_TIMEOUT). Every synthesize attempt is counted in the usage table
(DATA_DIR/studio_gateway.sqlite3), never limited by it.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response

from app import identity, tts_server
from app.config import settings
from app.database import DATA_DIR
from app.identity import agent_error, verify_agent

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/studio/v1", tags=["Studio gateway"])

USAGE_DB = DATA_DIR / "studio_gateway.sqlite3"
VN = timezone(timedelta(hours=7))
VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
REVISION = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_TEXT = 450
MAX_AUDIO_BYTES = 10 * 1024 * 1024
QUEUE_TIMEOUT = 60.0          # seconds a sentence may wait for the GPU gate
REQUEST_BUDGET = 95.0         # Cloudflare cuts requests at 100 s
GRACE_HOURS = 72
AGENT_RATE = (120, 60.0)      # requests per agent per window (seconds)
ACTIVATE_RATE = (10, 600.0)   # activation attempts per client IP per window

AUTH_DETAIL = {
    "AGENT_NOT_ACTIVATED": "Máy chưa được kích hoạt.",
    "AGENT_UNAUTHORIZED": "Thông tin kích hoạt của máy không đúng. Hãy kích hoạt lại.",
    "AGENT_REVOKED": "Máy này đã bị gỡ khỏi tài khoản. Hãy liên hệ để được cấp mã kích hoạt mới.",
    "LICENSE_EXPIRED": "License đã hết hạn hoặc bị khóa.",
}


# --- rate limiting ----------------------------------------------------------------------------------------------
class SlidingWindow:
    """In-memory sliding-window limiter (one process; the gateway runs a single uvicorn worker)."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Count one request; None when allowed, else the seconds to wait (Retry-After)."""
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 10_000:
                for stale in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
                    del self._hits[stale]
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(hits[0] + self.window - now + 0.999))
            hits.append(now)
            return None


def rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(429, f"Gửi quá nhiều yêu cầu. Thử lại sau {retry_after} giây.",
                         headers={"X-Sarae-Error": "RATE_LIMITED", "Retry-After": str(retry_after)})


# --- TTS queue --------------------------------------------------------------------------------------------------
class FifoGate:
    """Counting semaphore that admits waiters strictly in arrival order, with a timeout."""

    def __init__(self, slots: int):
        self.slots = max(1, int(slots))
        self.active = 0
        self._queue: deque = deque()
        self._cond = threading.Condition()

    def acquire(self, timeout: float) -> bool:
        ticket = object()
        deadline = time.monotonic() + timeout
        with self._cond:
            self._queue.append(ticket)
            try:
                while not (self._queue[0] is ticket and self.active < self.slots):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(remaining)
                self._queue.popleft()
                self.active += 1
                return True
            finally:
                if ticket in self._queue:          # timed out: let the next waiter move up
                    self._queue.remove(ticket)
                self._cond.notify_all()

    def release(self) -> None:
        with self._cond:
            self.active -= 1
            self._cond.notify_all()

    def waiting(self) -> int:
        with self._cond:
            return len(self._queue)


_state_lock = threading.Lock()
_inflight: set[str] = set()
_voice_cache: list[dict] = []
_gate = FifoGate(settings.sarae_gateway_tts_concurrency)
agent_limiter = SlidingWindow(*AGENT_RATE)
activate_limiter = SlidingWindow(*ACTIVATE_RATE)


def reset_state() -> None:
    """Fresh queue, limiters and voice cache (tests; also picks up a changed concurrency setting)."""
    global _gate, agent_limiter, activate_limiter
    with _state_lock:
        _inflight.clear()
        _voice_cache.clear()
    _gate = FifoGate(settings.sarae_gateway_tts_concurrency)
    agent_limiter = SlidingWindow(*AGENT_RATE)
    activate_limiter = SlidingWindow(*ACTIVATE_RATE)


@contextmanager
def owner_slot(owner_id: str):
    with _state_lock:
        if owner_id in _inflight:
            raise HTTPException(429, "Đang tạo một câu khác cho tài khoản này. Hãy chờ câu đó xong.",
                                headers={"X-Sarae-Error": "TTS_BUSY", "Retry-After": "1"})
        _inflight.add(owner_id)
    try:
        yield
    finally:
        with _state_lock:
            _inflight.discard(owner_id)


# --- usage ------------------------------------------------------------------------------------------------------
def vn_day(at: datetime | None = None) -> str:
    return (at or datetime.now(timezone.utc)).astimezone(VN).date().isoformat()


@contextmanager
def usage_db():
    db = sqlite3.connect(USAGE_DB, timeout=10)
    try:
        with db:
            db.row_factory = sqlite3.Row
            db.execute("""CREATE TABLE IF NOT EXISTS usage_daily (
                owner_id TEXT NOT NULL, agent_id TEXT NOT NULL, day TEXT NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0, chars INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0, audio_bytes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (owner_id, agent_id, day))""")
            yield db
    finally:
        db.close()


def record_usage(owner_id: str, agent_id: str, *, chars: int, ok: bool, audio_bytes: int = 0) -> None:
    try:
        with usage_db() as db:
            db.execute("""INSERT INTO usage_daily(owner_id, agent_id, day, requests, chars, failures, audio_bytes)
                VALUES(?,?,?,1,?,?,?) ON CONFLICT(owner_id, agent_id, day) DO UPDATE SET
                requests=requests+1, chars=chars+excluded.chars, failures=failures+excluded.failures,
                audio_bytes=audio_bytes+excluded.audio_bytes""",
                       (owner_id, agent_id, vn_day(), chars, 0 if ok else 1, audio_bytes))
    except sqlite3.Error:
        log.exception("could not record studio usage")      # usage never blocks a sentence


def usage_rows(owner_id: str | None = None, days: int = 7) -> list[dict]:
    since = vn_day(datetime.now(timezone.utc) - timedelta(days=max(1, days) - 1))
    sql = "SELECT * FROM usage_daily WHERE day>=?" + (" AND owner_id=?" if owner_id else "") + \
          " ORDER BY day DESC, owner_id, agent_id"
    with usage_db() as db:
        return [dict(r) for r in db.execute(sql, (since, owner_id) if owner_id else (since,))]


# --- device auth ------------------------------------------------------------------------------------------------
def device(x_sarae_agent: str | None = Header(None), authorization: str | None = Header(None)) -> dict:
    agent_id = (x_sarae_agent or "").strip()
    credential = authorization[7:].strip() if authorization and authorization.startswith("Bearer ") else ""
    if not agent_id or not credential:
        raise agent_error(401, AUTH_DETAIL["AGENT_NOT_ACTIVATED"], "AGENT_NOT_ACTIVATED")
    if len(agent_id) > 100 or len(credential) > 200:
        raise agent_error(403, AUTH_DETAIL["AGENT_UNAUTHORIZED"], "AGENT_UNAUTHORIZED")
    try:
        return verify_agent(agent_id, credential)
    except HTTPException as exc:
        code = (exc.headers or {}).get("X-Sarae-Error", "AGENT_UNAUTHORIZED")
        raise agent_error(exc.status_code, AUTH_DETAIL.get(code, str(exc.detail)), code) from None


def check_rate(agent: dict) -> None:
    retry = agent_limiter.hit(agent["id"])
    if retry is not None:
        raise rate_limited(retry)


# --- endpoints --------------------------------------------------------------------------------------------------
@router.get("/license")
def license_(agent: dict = Depends(device)):
    check_rate(agent)
    with identity.db_open() as db:
        row = db.execute("""SELECT u.email, l.expires_at, l.max_agents, a.hostname FROM registered_agents a
            JOIN users u ON u.id=a.owner_id JOIN licenses l ON l.owner_id=a.owner_id WHERE a.id=?""",
                         (agent["id"],)).fetchone()
    return {"valid": True, "email": row["email"], "expires_at": row["expires_at"], "max_agents": row["max_agents"],
            "agent_id": agent["id"], "hostname": row["hostname"],
            "server_time": datetime.now(timezone.utc).isoformat(), "grace_hours": GRACE_HOURS}


def _public_voice(profile: dict) -> dict | None:
    voice_id = str(profile.get("voice_id", ""))
    if not VOICE_ID.match(voice_id):
        return None
    rev = str(profile.get("rev") or "")
    return {"voice_id": voice_id, "name": str(profile.get("name") or voice_id)[:100],
            "rev": rev if REVISION.match(rev) else ""}


@router.get("/voices")
def voices(agent: dict = Depends(device)):
    check_rate(agent)
    online = False
    try:
        health = tts_server.ready(max_age=0)
        listed = [v for v in (_public_voice(p) for p in tts_server.list_voices()) if v]
        with _state_lock:
            _voice_cache[:] = listed
        online = bool(health.get("online")) and health.get("state") == "ready"
    except (tts_server.TtsUnavailable, tts_server.TtsError, ValueError) as exc:
        log.warning("voice list unavailable: %s", getattr(exc, "code", type(exc).__name__))
    with _state_lock:
        cached = [dict(v) for v in _voice_cache]
    return {"online": online, "voices": [{**v, "ready": online} for v in cached]}


def _invalid(detail: str) -> HTTPException:
    return agent_error(422, detail, "INVALID_PARAMS")


def _unavailable(detail: str = "Máy chủ giọng nói đang tắt hoặc lỗi. Câu này sẽ đọc bằng giọng Edge.") -> HTTPException:
    return agent_error(503, detail, "TTS_UNAVAILABLE")


def _validate(body: Any) -> tuple[str, str]:
    if not isinstance(body, dict):
        raise _invalid("Dữ liệu gửi lên không hợp lệ.")
    voice_id, text, preset = body.get("voice_id"), body.get("text"), body.get("preset", "live_fast")
    if not isinstance(voice_id, str) or not VOICE_ID.match(voice_id):
        raise _invalid("Mã giọng (voice_id) không hợp lệ.")
    if not isinstance(text, str) or not text.strip():
        raise _invalid("Thiếu nội dung cần đọc.")
    if len(text.strip()) > MAX_TEXT:
        raise _invalid(f"Nội dung quá dài (tối đa {MAX_TEXT} ký tự).")
    if preset != "live_fast":
        raise _invalid("Preset không được hỗ trợ (chỉ dùng live_fast).")
    return voice_id, text.strip()


def _voice_error(exc: tts_server.TtsError) -> HTTPException:
    if exc.code == "VOICE_NOT_FOUND":
        return agent_error(404, "Không tìm thấy giọng này trên server. Hãy làm mới danh sách giọng.", "VOICE_NOT_FOUND")
    if exc.status in (400, 422) and exc.code != "TTS_AUTH":
        return _invalid("Máy chủ giọng nói từ chối nội dung hoặc tham số này.")
    if exc.code in ("TTS_BUSY", "TTS_QUEUE_TIMEOUT") or exc.status == 429:
        return agent_error(503, "GPU đang bận. Câu này sẽ đọc bằng giọng Edge.", "TTS_QUEUE_TIMEOUT")
    return _unavailable()


@router.post("/tts/synthesize")
def synthesize(body: Any = Body(None), agent: dict = Depends(device)):
    started = time.monotonic()
    chars = 0
    try:
        check_rate(agent)
        voice_id, text = _validate(body)
        chars = len(text)
        with owner_slot(agent["owner_id"]):
            gate = _gate
            if not gate.acquire(QUEUE_TIMEOUT):
                raise agent_error(503, "Hàng đợi GPU quá lâu. Câu này sẽ đọc bằng giọng Edge.", "TTS_QUEUE_TIMEOUT")
            try:
                remaining = REQUEST_BUDGET - (time.monotonic() - started)
                audio, rev = tts_server.synthesize_live(voice_id, text, timeout=max(5.0, remaining),
                                                        max_bytes=MAX_AUDIO_BYTES)
            except tts_server.TtsUnavailable as exc:
                log.warning("tts unavailable for %s: %s", agent["id"], exc.code)
                raise _unavailable() from None
            except tts_server.TtsError as exc:
                log.warning("tts error for %s: %s %s", agent["id"], exc.status, exc.code)
                raise (_unavailable("Máy chủ giọng nói trả âm thanh quá lớn.") if exc.code == "TTS_INVALID_AUDIO"
                       else _voice_error(exc)) from None
            finally:
                gate.release()
        if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise _unavailable("Máy chủ giọng nói chưa trả âm thanh WAV hợp lệ.")
    except Exception:                       # every failed attempt counts too (HTTPException included)
        record_usage(agent["owner_id"], agent["id"], chars=chars, ok=False)
        raise
    record_usage(agent["owner_id"], agent["id"], chars=chars, ok=True, audio_bytes=len(audio))
    if not REVISION.match(rev or ""):
        with _state_lock:
            rev = next((v["rev"] for v in _voice_cache if v["voice_id"] == voice_id), "")
    log.info("tts ok agent=%s chars=%s bytes=%s ms=%.0f", agent["id"], chars, len(audio),
             (time.monotonic() - started) * 1000)
    return Response(audio, media_type="audio/wav", headers={"X-Voice-Revision": rev, "Cache-Control": "no-store"})
