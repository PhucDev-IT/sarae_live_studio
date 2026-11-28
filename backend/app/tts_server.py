"""HTTP client for sarae-voice-server (the GPU TTS on the server PC).

The only module that talks to the voice server; used by the gateway (voice list, LIVE sentences) and the admin page
(clone, transcript, preview, delete). Every URL is settings.sarae_tts_server_url plus a fixed path and ids checked
against a strict pattern: nothing from a request becomes a host or a path, and redirects are never followed. The
orchestrator token is sent as a Bearer header and never logged or returned.

Errors:
  TtsUnavailable  network failure, timeout, server not configured, or the model not ready (503).
  TtsError        the voice server answered with an error code (e.g. VOICE_NOT_FOUND, TTS_BUSY).
Only idempotent calls are retried on network errors.
"""
from __future__ import annotations

import logging
import re
import threading
import time

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


def put_transcript(voice_id: str, transcript: str | None) -> dict:
    """Manual transcript; None or "" goes back to the stored Whisper text (no new Whisper run)."""
    return _call("PUT", f"/v1/voices/{_check_id(voice_id, 'voice_id')}/transcript", retries=1, timeout=120,
                 json={"transcript": transcript or None}).json()


def delete_voice(voice_id: str) -> None:
    try:
        _call("DELETE", f"/v1/voices/{_check_id(voice_id, 'voice_id')}", retries=1, ok=(204,))
    except TtsError as exc:
        if exc.code != "VOICE_NOT_FOUND":
            raise


# --- synthesis -----------------------------------------------------------------------------------------
def list_voices(timeout: float = 5) -> list[dict]:
    """GET /v1/voices: the raw profiles (callers must whitelist fields before passing them on)."""
    voices = _call("GET", "/v1/voices", timeout=timeout).json().get("voices")
    return [v for v in voices if isinstance(v, dict)] if isinstance(voices, list) else []


def synthesize_live(voice_id: str, text: str, *, timeout: float, max_bytes: int) -> tuple[bytes, str]:
    """Studio gateway: one sentence with preset live_fast (same body as the Studio's local mode, seed 42).
    Streams the WAV and stops past `max_bytes`. Returns (audio, X-Voice-Rev). Not retried."""
    body = {"voice_id": _check_id(voice_id, "voice_id"), "text": text, "preset": "live_fast", "seed": 42}
    try:
        with _client(timeout) as client:
            with client.stream("POST", "/v1/tts/synthesize", json=body) as response:
                if response.is_redirect:
                    raise TtsError(502, "TTS_REDIRECT_REFUSED", "Máy chủ giọng nói trả về chuyển hướng")
                if response.status_code != 200:
                    response.read()
                    err = _error(response)
                    if response.status_code == 503:
                        raise TtsUnavailable(err.code, err.message or "Máy chủ giọng nói chưa sẵn sàng")
                    raise err
                audio = bytearray()
                for chunk in response.iter_bytes():
                    audio.extend(chunk)
                    if len(audio) > max_bytes:
                        raise TtsError(502, "TTS_INVALID_AUDIO", "Âm thanh máy chủ giọng nói quá lớn")
                return bytes(audio), response.headers.get("X-Voice-Rev", "")
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        log.warning("voice server live synthesize failed: %s", type(exc).__name__)
        code = "TTS_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "TTS_BACKEND_UNREACHABLE"
        raise TtsUnavailable(code, "Không kết nối được máy chủ giọng nói G-OmniVoice") from exc
