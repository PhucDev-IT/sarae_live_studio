"""HTTP client for the G-OmniVoice TTS Backend (voice-server/, docs/TTS_SERVER.md sections 15-16).

The only module that talks to the voice server. Every URL is built from settings.sarae_tts_server_url plus a fixed
path and ids checked against a strict pattern: nothing from a request ever becomes a host or a path, redirects are
never followed. The orchestrator token is sent as a Bearer header and never logged or returned.

Errors:
  TtsUnavailable  network failure, timeout, server not configured, or the model not ready (503): the voice server
                  cannot be reached or cannot work right now.
  TtsError        the voice server answered with an error code (e.g. VOICE_NOT_FOUND, TTS_BUSY, JOB_EXPIRED).
Retries: only idempotent calls are retried on network errors, and job creation always carries the caller's
Idempotency-Key, so a retry returns the same job instead of a second inference.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import httpx

from app.config import settings

log = logging.getLogger(__name__)
ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_transport: httpx.BaseTransport | None = None      # tests install an httpx.MockTransport here


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport
    _health.clear()


class TtsUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class TtsError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def configured() -> bool:
    return bool(settings.sarae_tts_server_url.strip() and settings.sarae_tts_server_token.strip())


def _check_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not ID.match(value):
        raise TtsError(422, "INVALID_PARAMS", f"{name} không hợp lệ")
    return value


def _client(timeout: float | None = None) -> httpx.Client:
    if not configured():
        raise TtsUnavailable("TTS_NOT_CONFIGURED", "Chưa cấu hình máy chủ giọng nói G-OmniVoice")
    return httpx.Client(base_url=settings.sarae_tts_server_url.strip().rstrip("/"), transport=_transport,
                        headers={"Authorization": f"Bearer {settings.sarae_tts_server_token.strip()}"},
                        timeout=httpx.Timeout(timeout or settings.sarae_tts_timeout_seconds, connect=3),
                        follow_redirects=False)


def _error(response: httpx.Response) -> TtsError:
    try:
        body = response.json().get("error") or {}
    except ValueError:
        body = {}
    retry = response.headers.get("Retry-After")
    code = body.get("code") or f"HTTP_{response.status_code}"
    message = str(body.get("message") or "")[:200]
    return TtsError(response.status_code, code, message, float(retry) if retry and retry.isdigit() else None)


def _call(method: str, path: str, *, retries: int = 0, timeout: float | None = None, ok=(200,), **kwargs) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _client(timeout) as client:
                response = client.request(method, path, **kwargs)
            if response.is_redirect:
                raise TtsError(502, "TTS_REDIRECT_REFUSED", "Máy chủ giọng nói trả về chuyển hướng")
            if response.status_code in ok:
                return response
            if response.status_code == 503:
                err = _error(response)
                raise TtsUnavailable(err.code, err.message or "Máy chủ giọng nói chưa sẵn sàng")
            raise _error(response)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            log.warning("voice server %s %s failed: %s (attempt %s)", method, path.split("?")[0], type(exc).__name__,
                        attempt + 1)
            if attempt < retries:
                time.sleep(min(1.0, 0.25 * (2 ** attempt)))
    code = "TTS_TIMEOUT" if isinstance(last, httpx.TimeoutException) else "TTS_BACKEND_UNREACHABLE"
    raise TtsUnavailable(code, "Không kết nối được máy chủ giọng nói G-OmniVoice")


# --- health (cached a few seconds: readiness checks and every speak use it) -------------------------------
_health: dict = {}
_health_lock = threading.Lock()


def ready(max_age: float = 5.0) -> dict:
    """{"online", "state", "error", "busy", "pending"}; never raises."""
    with _health_lock:
        if _health and time.monotonic() - _health["at"] < max_age:
            return _health["value"]
    try:
        with _client(3) as client:
            response = client.get("/health/ready")
        body = response.json()
        value = {"online": True, "state": body.get("state"), "error": body.get("error"), "busy": body.get("busy"),
                 "pending": body.get("pending")}
    except (httpx.HTTPError, ValueError) as exc:
        value = {"online": False, "state": None, "error": {"code": "TTS_BACKEND_UNREACHABLE", "message": type(exc).__name__},
                 "busy": None, "pending": None}
    except TtsUnavailable as exc:
        value = {"online": False, "state": None, "error": {"code": exc.code, "message": exc.message}, "busy": None,
                 "pending": None}
    with _health_lock:
        _health.update(at=time.monotonic(), value=value)
    return value


def status() -> dict:
    return _call("GET", "/v1/status", retries=1).json()


# --- voices ------------------------------------------------------------------------------------------
def put_voice(voice_id: str, wav: bytes, transcript: str | None, name: str | None, owner_ref: str) -> dict:
    data = {"owner_ref": owner_ref}
    if transcript:
        data["transcript"] = transcript
    if name:
        data["name"] = name[:100]
    # Same WAV + same transcript is idempotent on the voice server, so the upload may be retried.
    return _call("PUT", f"/v1/voices/{_check_id(voice_id, 'voice_id')}", retries=1, timeout=120, ok=(200, 201),
                 files={"wav": ("ref.wav", wav, "audio/wav")}, data=data).json()


def get_voice(voice_id: str) -> dict | None:
    try:
        return _call("GET", f"/v1/voices/{_check_id(voice_id, 'voice_id')}", retries=1).json()
    except TtsError as exc:
        if exc.code == "VOICE_NOT_FOUND":
            return None
        raise


def delete_voice(voice_id: str) -> None:
    try:
        _call("DELETE", f"/v1/voices/{_check_id(voice_id, 'voice_id')}", retries=1, ok=(204,))
    except TtsError as exc:
        if exc.code != "VOICE_NOT_FOUND":
            raise


def warmup(voice_id: str) -> dict:
    return _call("POST", f"/v1/voices/{_check_id(voice_id, 'voice_id')}/warmup", retries=1, timeout=120).json()


# --- synthesis -----------------------------------------------------------------------------------------
def synthesize(voice_id: str, text: str, preset: str = "demo_default") -> tuple[bytes, dict]:
    """Preview: WAV bytes and the server's trace headers. Not retried (a retry would be a second inference)."""
    response = _call("POST", "/v1/tts/synthesize", timeout=90,
                     json={"voice_id": _check_id(voice_id, "voice_id"), "text": text, "preset": preset})
    headers = {k: v for k, v in response.headers.items() if k.lower().startswith("x-") and k.lower() != "x-trace"}
    return response.content, headers


def create_job(*, idempotency_key: str, voice_id: str, text: str, priority: str, deadline_at: str,
               reader_agent_id: str | None, owner_ref: str, tags: list[str], preset: str = "demo_default") -> dict:
    """POST /v1/tts/jobs with the caller's key: retried on network errors, the server returns the same job."""
    body = {"voice_id": _check_id(voice_id, "voice_id"), "text": text, "priority": priority, "deadline_at": deadline_at,
            "reader_agent_id": reader_agent_id, "owner_ref": owner_ref, "tags": tags, "preset": preset}
    return _call("POST", "/v1/tts/jobs", retries=2, ok=(200, 202), json=body,
                 headers={"Idempotency-Key": idempotency_key}).json()


def get_job(job_id: str, wait: float = 0) -> dict:
    return _call("GET", f"/v1/tts/jobs/{_check_id(job_id, 'job_id')}", retries=1, timeout=max(10.0, wait + 5),
                 params={"wait": wait}).json()


def cancel_job(job_id: str) -> dict | None:
    try:
        return _call("DELETE", f"/v1/tts/jobs/{_check_id(job_id, 'job_id')}", retries=1).json()
    except TtsError as exc:
        if exc.status in (404, 410):
            return None
        raise


def cancel_tag(tag: str) -> int:
    if not re.match(r"^[A-Za-z0-9:_.-]{1,80}$", tag):
        raise TtsError(422, "INVALID_PARAMS", "tag không hợp lệ")
    return _call("DELETE", "/v1/tts/jobs", retries=1, params={"tag": tag}).json().get("cancelled", 0)


@contextmanager
def open_audio(job_id: str, wait: float) -> Iterator[httpx.Response]:
    """Stream a job's WAV with the orchestrator token (Backend relay). The caller reads status, headers and
    iter_bytes(); the connection is closed when the block ends."""
    job_id = _check_id(job_id, "job_id")
    try:
        with _client(wait + 30) as client:
            with client.stream("GET", f"/v1/tts/jobs/{job_id}/audio", params={"wait": wait}) as response:
                if response.is_redirect:
                    raise TtsError(502, "TTS_REDIRECT_REFUSED", "Máy chủ giọng nói trả về chuyển hướng")
                yield response
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        code = "TTS_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "TTS_BACKEND_UNREACHABLE"
        raise TtsUnavailable(code, "Không kết nối được máy chủ giọng nói G-OmniVoice") from exc


# --- LIVE admission ----------------------------------------------------------------------------------
def put_lease(owner_ref: str, session_id: str, ttl_s: float) -> dict:
    return _call("PUT", "/v1/admission/live", retries=1,
                 json={"owner_ref": owner_ref, "session_id": session_id, "ttl_s": ttl_s}).json()


def delete_lease(owner_ref: str, session_id: str) -> None:
    _call("DELETE", "/v1/admission/live", retries=1, params={"owner_ref": owner_ref, "session_id": session_id})


def admission() -> dict:
    return _call("GET", "/v1/admission/live", retries=1).json()
