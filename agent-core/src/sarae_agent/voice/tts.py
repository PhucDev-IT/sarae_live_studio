"""Operator-approved Vietnamese TTS (Microsoft Edge neural voices via edge-tts).

The MP3 is generated locally, then loaded into the OBS input named in config.obs.voice_input
(a Media Source the operator creates once, with audio enabled in the mixer). The video media
input is never touched. One utterance at a time; generation has a hard timeout.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Callable

from sarae_agent.config import ConfigStore, paths
from sarae_agent.errors import AgentError
from sarae_agent.obs.controller import ObsController

log = logging.getLogger("sarae.voice")

VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")
MAX_CHARS = 450


class VoiceService:
    GENERATE_TIMEOUT = 25.0

    def __init__(self, config: ConfigStore, obs: ObsController, synthesize: Callable[[str, str, str], None] | None = None):
        self._config = config
        self._obs = obs
        self._lock = threading.Lock()
        self._synthesize = synthesize or self._edge_tts
        self.last: dict | None = None

    def _edge_tts(self, text: str, voice: str, output: str) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise AgentError("TTS_UNAVAILABLE", "Thiếu thư viện edge-tts trong Agent Core") from exc

        async def run() -> None:
            await asyncio.wait_for(edge_tts.Communicate(text, voice).save(output), timeout=self.GENERATE_TIMEOUT)

        try:
            asyncio.run(run())
        except asyncio.TimeoutError as exc:
            raise AgentError("TTS_TIMEOUT", f"Tạo giọng nói quá {int(self.GENERATE_TIMEOUT)} giây",
                             retryable=True) from exc
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - aiohttp/network errors from the TTS service
            raise AgentError("TTS_FAILED", f"Dịch vụ TTS lỗi: {type(exc).__name__}: {str(exc)[:200]}",
                             retryable=True) from exc

    def speak(self, text: str, voice: str = VOICES[0], obs_source: str | None = None) -> dict:
        text = (text or "").strip()
        if not 1 <= len(text) <= MAX_CHARS:
            raise AgentError("INVALID_PARAMS", f"Nội dung TTS từ 1 đến {MAX_CHARS} ký tự")
        if voice not in VOICES:
            raise AgentError("INVALID_PARAMS", "Giọng đọc không được hỗ trợ")
        source = obs_source or self._config.get().obs.voice_input
        if not self._lock.acquire(blocking=False):
            raise AgentError("TTS_BUSY", "Đang phát một câu TTS khác")
        started = time.monotonic()
        try:
            output = paths.voice / f"voice_{uuid.uuid4().hex}.mp3"
            self._synthesize(text, voice, str(output))
            if not output.is_file() or output.stat().st_size == 0:
                raise AgentError("TTS_FAILED", "Dịch vụ TTS không trả về âm thanh")
            media = self._obs.play_file(source, str(output.resolve()), unmute=True)
            self.last = {"status": "sent_to_obs", "source": source, "voice": voice, "chars": len(text),
                         "media_state": media.get("state"),
                         "duration_ms": int((time.monotonic() - started) * 1000)}
            log.info("Đã phát TTS (%s ký tự) qua nguồn OBS '%s'", len(text), source)
            return dict(self.last)
        finally:
            self._lock.release()
            self._prune()

    @staticmethod
    def _prune(keep: int = 20) -> None:
        files = sorted(paths.voice.glob("voice_*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass  # still opened by OBS
