"""Cloned voices served by the Sarae gateway (docs/HYBRID_STUDIO_SERVER.md), same interface as LocalVoiceServer.

- Catalog: Edge voices plus ``remote:<voice_id>`` from ``GET /api/studio/v1/voices`` (device auth).
- Synthesis: ``POST /api/studio/v1/tts/synthesize`` returns WAV; FFmpeg turns it into the MP3 OBS plays.
  This machine sends one line at a time (the gateway allows one in flight per customer); ``TTS_BUSY`` waits
  briefly and retries.
- A LIVE never stops because of TTS: when the gateway is unavailable (``TTS_UNAVAILABLE``,
  ``TTS_QUEUE_TIMEOUT``, network errors, refused device) that line is spoken with the Edge voice
  ``vi-VN-HoaiMyNeural``, logged, and not cached as the cloned voice.
- ``SARAE_STUDIO_VOICE_MODE=local`` keeps the dev-only local voice server (voice_server.py) instead.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from sarae_agent.errors import AgentError

from .account import AUTH_CODES, ServerError, server_error
from .voice_server import EDGE_VOICES, VOICE_ID, LocalVoiceServer

log = logging.getLogger(__name__)
FALLBACK_VOICE = "vi-VN-HoaiMyNeural"
MAX_AUDIO = 10 * 1024 * 1024
BUSY_WAIT_SECONDS = 20.0  # the line before ours is still being generated; longer than that, Edge says it
OFFLINE_SKIP_SECONDS = 30.0  # after the gateway failed, go straight to Edge for a while instead of waiting again


class _Fallback(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def voice_backend(account, environ=None):
    """Remote cloned voices by default; the old local voice server only with SARAE_STUDIO_VOICE_MODE=local."""
    mode = (environ if environ is not None else os.environ).get("SARAE_STUDIO_VOICE_MODE", "").strip().lower()
    if mode == "local":
        log.warning("SARAE_STUDIO_VOICE_MODE=local: dùng voice server local (chỉ dành cho dev).")
        return LocalVoiceServer()
    return RemoteVoiceServer(account)


class RemoteVoiceServer:
    provider = "remote_voice"
    prefix = "remote:"
    mode = "remote"
    requires_key = False

    def __init__(self, account, *, transport: httpx.BaseTransport | None = None, edge=None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 ffmpeg: str | None = None):
        self.account = account
        self.transport = transport
        if edge is None:
            from sarae_agent.voice.providers import EdgeTTSProvider
            edge = EdgeTTSProvider()
        self.edge = edge
        self._sleep, self._clock = sleep, clock
        self._ffmpeg = ffmpeg
        self._lock = threading.Lock()
        self._refreshing = threading.Lock()
        self._synth = threading.Lock()  # one line in flight from this machine
        self._online = False
        self._error = "Chưa kiểm tra server giọng Sarae."
        self._voices: list[dict] = []
        self._skip_until = 0.0
        self.fallbacks = 0
        self.last_fallback: str | None = None

    # --- catalog ---------------------------------------------------------------------------
    def catalog(self) -> dict:
        with self._lock:
            options = [*EDGE_VOICES, *[{"id": self.prefix + v["voice_id"], "name": v["name"], "provider": self.provider,
                                        "ready": self._online and v["ready"], "revision": v["rev"]}
                                       for v in self._voices]]
            return {"voice_options": options,
                    "voice_server": {"online": self._online, "starting": False, "error": self._error,
                                     "mode": self.mode, "fallbacks": self.fallbacks,
                                     "last_fallback": self.last_fallback}}

    def start(self) -> None:
        raise AgentError("VOICE_SERVER_REMOTE", "Giọng clone chạy trên server Sarae; máy này không cần bật voice server.")

    def refresh(self) -> dict:
        if not self._refreshing.acquire(blocking=False):
            return self.catalog()
        try:
            auth = self.account.auth()
            if auth is None:
                with self._lock:
                    self._online = False
                    self._error = "Máy chưa kích hoạt nên chưa tải được giọng clone. Giọng Edge vẫn dùng được."
                return self.catalog()
            base, headers = auth
            try:
                with httpx.Client(transport=self.transport, timeout=httpx.Timeout(10, connect=5),
                                  follow_redirects=False) as client:
                    response = client.get(base + "/api/studio/v1/voices", headers=headers)
                    if response.status_code != 200:
                        raise server_error(response)
                    payload = json.loads(response.read()[:1024 * 1024])
                voices = []
                for item in (payload.get("voices") or [])[:200] if isinstance(payload, dict) else []:
                    if isinstance(item, dict) and VOICE_ID.fullmatch(str(item.get("voice_id", ""))):
                        voices.append({"voice_id": item["voice_id"],
                                       "name": str(item.get("name") or item["voice_id"]).strip()[:80] or item["voice_id"],
                                       "rev": str(item.get("rev") or "")[:80], "ready": item.get("ready") is True})
                online = isinstance(payload, dict) and payload.get("online") is True
                with self._lock:
                    self._voices = voices
                    self._online = online
                    self._error = "" if online else "Server giọng Sarae đang tắt; câu trả lời sẽ đọc bằng giọng Edge."
                    if online:
                        self._skip_until = 0.0
            except ServerError as exc:
                self.account.report(exc.code)
                with self._lock:
                    self._online = False
                    self._error = (exc.detail or "Server Sarae từ chối tải giọng clone.") if exc.code in AUTH_CODES | {
                        "LICENSE_EXPIRED"} else "Server giọng Sarae chưa sẵn sàng; câu trả lời sẽ đọc bằng giọng Edge."
            except (httpx.HTTPError, ValueError):
                with self._lock:
                    self._online = False
                    self._error = "Không kết nối được server giọng Sarae; câu trả lời sẽ đọc bằng giọng Edge."
        finally:
            self._refreshing.release()
        return self.catalog()

    def revision(self, voice_id: str) -> str:
        with self._lock:
            return next((v["rev"] for v in self._voices if v["voice_id"] == voice_id), "")

    # --- provider ----------------------------------------------------------------------------
    def normalize(self, model, voice, rate, settings):
        if not VOICE_ID.fullmatch(voice) or model not in (None, "live_fast") or not -30 <= rate <= 50:
            raise AgentError("INVALID_PARAMS", "Giọng clone hoặc tốc độ đọc không hợp lệ.")
        return "live_fast", {"rate": rate, "revision": (settings or {}).get("revision", self.revision(voice))}

    def synthesize(self, text, voice, model, settings, output: Path, api_key, first_audio):
        """MP3 of the cloned voice; returns ``{"cache": False}`` when Edge said the line instead."""
        try:
            audio = self._remote_wav(text, voice, model)
            first_audio()
            self._to_mp3(audio, settings, output)
            return None
        except _Fallback as reason:
            code = reason.code
        with self._lock:
            self.fallbacks += 1
            self.last_fallback = code
        log.warning("Giọng clone chưa đọc được câu này (%s); đọc bằng giọng Edge %s.", code, FALLBACK_VOICE)
        output.unlink(missing_ok=True)
        self.edge.synthesize(text, FALLBACK_VOICE, "edge-neural", {"rate": settings.get("rate", 0)}, output, None,
                             first_audio)
        return {"cache": False, "fallback": FALLBACK_VOICE}

    def _mark_offline(self, message: str) -> None:
        with self._lock:
            self._online = False
            self._error = message
            self._skip_until = self._clock() + OFFLINE_SKIP_SECONDS

    def _remote_wav(self, text: str, voice: str, model: str) -> bytes:
        auth = self.account.auth()
        if auth is None:
            raise _Fallback("AGENT_NOT_ACTIVATED")
        if self._clock() < self._skip_until:
            raise _Fallback("TTS_UNAVAILABLE")
        base, headers = auth
        with self._synth:
            deadline = self._clock() + BUSY_WAIT_SECONDS
            while True:
                try:
                    audio = self._post(base, headers, {"voice_id": voice, "text": text, "preset": model})
                except httpx.HTTPError:
                    self._mark_offline("Không kết nối được server giọng Sarae; đang đọc bằng giọng Edge.")
                    raise _Fallback("SERVER_UNREACHABLE") from None
                except ServerError as exc:
                    if exc.code in ("TTS_BUSY", "RATE_LIMITED"):
                        wait = min(max(exc.retry_after or 1.0, 0.5), 5.0)
                        if self._clock() + wait <= deadline:
                            self._sleep(wait)
                            continue
                        raise _Fallback(exc.code) from None
                    if exc.code in AUTH_CODES or exc.code == "LICENSE_EXPIRED":
                        self.account.report(exc.code)
                        raise _Fallback(exc.code) from None
                    if exc.code in ("VOICE_NOT_FOUND", "INVALID_PARAMS"):
                        raise AgentError(exc.code, "Giọng clone đã chọn không còn trên server Sarae hoặc câu đọc không hợp lệ. "
                                         "Bấm Làm mới giọng rồi chọn lại giọng.") from None
                    if exc.code == "TTS_QUEUE_TIMEOUT":
                        raise _Fallback(exc.code) from None
                    self._mark_offline("Server giọng Sarae đang lỗi hoặc tắt; đang đọc bằng giọng Edge.")
                    raise _Fallback("TTS_UNAVAILABLE" if exc.code.startswith("HTTP_") else exc.code) from None
                with self._lock:
                    if not self._online:
                        self._online, self._error = True, ""
                return audio

    def _post(self, base: str, headers: dict, body: dict) -> bytes:
        # Cloudflare ends requests at 100 s and the gateway gives up queueing at 60 s.
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(95, connect=10), follow_redirects=False) as client:
            with client.stream("POST", base + "/api/studio/v1/tts/synthesize", headers=headers, json=body) as response:
                if response.status_code != 200:
                    raise server_error(response)
                audio = bytearray()
                for chunk in response.iter_bytes():
                    audio.extend(chunk)
                    if len(audio) > MAX_AUDIO:
                        raise ServerError("TTS_UNAVAILABLE", 200)
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise ServerError("TTS_UNAVAILABLE", 200)
        return bytes(audio)

    def _to_mp3(self, audio: bytes, settings: dict, output: Path) -> None:
        executable = self._ffmpeg or shutil.which("ffmpeg")
        if not executable:
            log.warning("Thiếu FFmpeg để phát giọng clone qua OBS.")
            raise _Fallback("FFMPEG_UNAVAILABLE")
        try:
            result = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0",
                                     "-vn", "-af", f"atempo={1 + settings.get('rate', 0) / 100:g}", "-codec:a", "libmp3lame",
                                     str(output)], input=audio, capture_output=True, timeout=20,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise _Fallback("FFMPEG_FAILED") from None
        if result.returncode or not output.is_file() or not output.stat().st_size:
            raise _Fallback("FFMPEG_FAILED")
