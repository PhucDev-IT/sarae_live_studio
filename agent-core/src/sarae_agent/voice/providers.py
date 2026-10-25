"""Audio generators. Providers write MP3 to disk so OBS can play a completed file."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable, Protocol

import httpx

from sarae_agent.errors import AgentError

FirstAudio = Callable[[], None]


class TTSProvider(Protocol):
    requires_key: bool
    def normalize(self, model: str | None, voice: str, rate: int, settings: dict | None) -> tuple[str, dict]: ...

    def synthesize(self, text: str, voice: str, model: str, settings: dict,
                   output: Path, api_key: str | None, first_audio: FirstAudio) -> None: ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, TTSProvider] = {}

    def register(self, name: str, provider: TTSProvider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> TTSProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise AgentError("TTS_PROVIDER", f"Nhà cung cấp giọng nói không được hỗ trợ: {name}") from exc


class EdgeTTSProvider:
    VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")
    requires_key = False

    def normalize(self, model: str | None, voice: str, rate: int, settings: dict | None) -> tuple[str, dict]:
        if voice not in self.VOICES or model not in (None, "edge-neural"):
            raise AgentError("INVALID_PARAMS", "Giọng hoặc model Edge TTS không được hỗ trợ")
        if settings and settings != {"rate": rate}:
            raise AgentError("INVALID_PARAMS", "Edge TTS không nhận voice settings")
        if not -30 <= rate <= 50:
            raise AgentError("INVALID_PARAMS", "Tốc độ đọc từ -30% đến +50%")
        return "edge-neural", {"rate": rate}

    def synthesize(self, text: str, voice: str, model: str, settings: dict,
                   output: Path, api_key: str | None, first_audio: FirstAudio) -> None:
        if voice not in self.VOICES or model != "edge-neural":
            raise AgentError("INVALID_PARAMS", "Giọng hoặc model Edge TTS không được hỗ trợ")
        try:
            import edge_tts
        except ImportError as exc:
            raise AgentError("TTS_UNAVAILABLE", "Thiếu thư viện edge-tts trong Agent Core") from exc

        async def run() -> None:
            for attempt in range(4):
                try:
                    async def stream() -> None:
                        with output.open("wb") as file:
                            async for chunk in edge_tts.Communicate(text, voice, rate=f"{settings.get('rate', 0):+d}%").stream():
                                if chunk["type"] == "audio":
                                    if file.tell() == 0:
                                        first_audio()
                                    file.write(chunk["data"])
                    await asyncio.wait_for(stream(), timeout=25)
                    if output.stat().st_size:
                        return
                    raise edge_tts.exceptions.NoAudioReceived()
                except edge_tts.exceptions.NoAudioReceived:
                    if attempt == 3:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))

        try:
            asyncio.run(run())
        except asyncio.TimeoutError as exc:
            raise AgentError("TTS_TIMEOUT", "Tạo giọng nói quá 25 giây", retryable=True) from exc
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError("TTS_FAILED", f"Dịch vụ Edge TTS lỗi: {type(exc).__name__}", retryable=True) from exc


class ElevenLabsProvider:
    URL = "https://api.elevenlabs.io/v1/text-to-speech"
    RETRIES = 3
    requires_key = True

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    def normalize(self, model: str | None, voice: str, rate: int, settings: dict | None) -> tuple[str, dict]:
        model = model or "eleven_flash_v2_5"
        if not voice or not model or len(voice) > 100 or len(model) > 100 or not all(c.isalnum() or c in "_-" for c in voice + model):
            raise AgentError("INVALID_PARAMS", "Voice ID hoặc model ID không hợp lệ")
        if not -30 <= rate <= 50:
            raise AgentError("INVALID_PARAMS", "Tốc độ đọc từ -30% đến +50%")
        options = dict(settings or {})
        allowed = {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
        if options.keys() - allowed:
            raise AgentError("INVALID_PARAMS", "Tham số giọng nói ElevenLabs không được hỗ trợ")
        for name in ("stability", "similarity_boost", "style"):
            value = options.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1):
                raise AgentError("INVALID_PARAMS", f"{name} phải từ 0 đến 1")
        speed = options.get("speed")
        if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.7 <= speed <= 1.2):
            raise AgentError("INVALID_PARAMS", "speed phải từ 0.7 đến 1.2")
        if "use_speaker_boost" in options and not isinstance(options["use_speaker_boost"], bool):
            raise AgentError("INVALID_PARAMS", "use_speaker_boost không hợp lệ")
        if speed is None:
            speed = round(1 + rate / 100, 2)
            if not 0.7 <= speed <= 1.2:
                raise AgentError("INVALID_PARAMS", "Tốc độ ElevenLabs phải từ 0.7 đến 1.2")
            options["speed"] = speed
        return model, options

    def synthesize(self, text: str, voice: str, model: str, settings: dict,
                   output: Path, api_key: str | None, first_audio: FirstAudio) -> None:
        if not api_key:
            raise AgentError("TTS_KEY_MISSING", "Chưa cấu hình ElevenLabs API key")
        if not voice or not model or not all(c.isalnum() or c in "_-" for c in voice + model):
            raise AgentError("INVALID_PARAMS", "Voice ID hoặc model ID không hợp lệ")
        body = {"text": text, "model_id": model, "voice_settings": settings}
        url = f"{self.URL}/{voice}/stream"
        headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
        for attempt in range(self.RETRIES):
            received = False
            try:
                with httpx.Client(transport=self.transport, timeout=httpx.Timeout(25, connect=6),
                                  follow_redirects=False) as client:
                    with client.stream("POST", url, headers=headers, params={"output_format": "mp3_44100_128"},
                                       json=body) as response:
                        if response.status_code >= 400:
                            code = {401: "TTS_AUTH", 402: "TTS_QUOTA", 403: "TTS_AUTH",
                                    429: "TTS_RATE_LIMIT"}.get(response.status_code, "TTS_API_ERROR")
                            retry = response.status_code in (429, 500, 502, 503, 504)
                            if retry and attempt < self.RETRIES - 1:
                                time.sleep(min(2.0, 0.35 * (2 ** attempt)))
                                continue
                            raise AgentError(code, f"ElevenLabs HTTP {response.status_code}", retryable=retry)
                        with output.open("wb") as file:
                            for chunk in response.iter_bytes(8192):
                                if chunk:
                                    if file.tell() == 0:
                                        first_audio()
                                    received = True
                                    file.write(chunk)
                        if output.stat().st_size == 0:
                            raise AgentError("TTS_EMPTY_AUDIO", "ElevenLabs không trả về âm thanh", retryable=True)
                        return
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if received:
                    raise AgentError("TTS_STREAM_INTERRUPTED", "Luồng âm thanh ElevenLabs bị ngắt",
                                     retryable=True) from exc
                if attempt == self.RETRIES - 1:
                    code = "TTS_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "TTS_NETWORK"
                    raise AgentError(code, f"Kết nối ElevenLabs lỗi: {type(exc).__name__}", retryable=True) from exc
                time.sleep(min(2.0, 0.35 * (2 ** attempt)))


def default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("edge", EdgeTTSProvider())
    registry.register("elevenlabs", ElevenLabsProvider())
    return registry
