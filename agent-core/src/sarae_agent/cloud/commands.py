"""Cloud command kinds -> local actions. Payloads are re-validated locally (never trust the wire).

Kinds mirror backend/app/cloud_agents.py CommandBody.kind.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from sarae_agent.errors import AgentError

MediaId = Field(pattern=r"^[a-f0-9]{64}$")


class NoPayload(BaseModel):
    model_config = {"extra": "forbid"}


class PlayPayload(BaseModel):
    media_id: str = MediaId


class PlaylistPayload(BaseModel):
    media_ids: list[str] = Field(min_length=1, max_length=300)
    loop: bool = True


class LoopPayload(BaseModel):
    loop: bool


class TtsPayload(BaseModel):
    text: str = Field(min_length=1, max_length=450)
    voice: Literal["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"] = "vi-VN-HoaiMyNeural"


class MonitorConfigPayload(BaseModel):
    interval_seconds: float = Field(default=2.0, ge=0.5, le=15)
    threshold: float = Field(default=0.82, ge=0.65, le=0.99)
    telegram_enabled: bool = False
    telegram_chat_id: str = Field(default="", max_length=50)


class CommandRouter:
    def __init__(self, *, playback, monitor, voice, obs, library):
        self.playback = playback
        self.monitor = monitor
        self.voice = voice
        self.obs = obs
        self.library = library
        p = playback
        self._table: dict[str, tuple[type[BaseModel], Callable[[BaseModel], object]]] = {
            "monitor_start": (NoPayload, lambda _: monitor.start()["state"]),
            "monitor_stop": (NoPayload, lambda _: monitor.stop()["state"]),
            "monitor_config": (MonitorConfigPayload, lambda b: monitor.configure(b.model_dump())),
            "obs_status": (NoPayload, lambda _: {"obs": obs.inspect(), "local": p.status()}),
            "obs_pause": (NoPayload, lambda _: p.pause()),
            "obs_resume": (NoPayload, lambda _: p.resume()),
            "obs_next": (NoPayload, lambda _: p.next()),
            "obs_previous": (NoPayload, lambda _: p.previous()),
            "obs_stop": (NoPayload, lambda _: p.stop(reason="Lệnh Cloud")),
            "local_media_play": (PlayPayload, lambda b: p.play(b.media_id)),
            "local_media_next": (NoPayload, lambda _: p.next()),
            "local_media_previous": (NoPayload, lambda _: p.previous()),
            "local_media_stop": (NoPayload, lambda _: p.stop(reason="Lệnh Cloud")),
            "local_playlist_start": (PlaylistPayload, lambda b: p.play_playlist(b.media_ids, b.loop)),
            "local_playback_loop": (LoopPayload, lambda b: p.set_loop(b.loop)),
            "tts_speak": (TtsPayload, lambda b: voice.speak(b.text, b.voice)),
            "media_sync": (NoPayload, lambda _: library.scan_blocking()),
        }

    @property
    def kinds(self) -> list[str]:
        return sorted(self._table)

    def execute(self, kind: str, payload: dict | None) -> object:
        entry = self._table.get(kind)
        if entry is None:
            raise AgentError("UNSUPPORTED_COMMAND", f"Agent không hỗ trợ lệnh {kind}")
        model, action = entry
        try:
            body = model.model_validate(payload or {})
        except ValidationError as exc:
            first = exc.errors()[0]
            raise AgentError("INVALID_COMMAND_PAYLOAD",
                             f"{'.'.join(str(x) for x in first.get('loc', ())) or 'payload'}: {first.get('msg')}") from exc
        result = action(body)
        if isinstance(result, dict) and result.get("state") == "failed":
            # media_sync returns a job summary; a failed scan must not be ACKed as succeeded.
            error = result.get("error") or {}
            raise AgentError(error.get("code", "COMMAND_FAILED"), error.get("message", "Thực thi thất bại"))
        return result
