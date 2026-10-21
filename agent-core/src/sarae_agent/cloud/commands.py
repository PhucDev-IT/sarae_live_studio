"""Cloud command kinds -> local actions. Payloads are re-validated locally (never trust the wire).

Kinds and payload limits mirror backend/app/cloud_agents.py (COMMAND_KINDS, validate_payload); the lanes
mirror its BACKGROUND_KINDS so the Agent runs each lane in order, like the Cloud hands them out.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from sarae_agent.errors import AgentError

MediaId = Field(pattern=r"^[a-f0-9]{64}$")
MediaIds = Field(min_length=1, max_length=300)

# Cloud lane "background"; everything else is the "live" lane (OBS playback).
BACKGROUND_KINDS = {"tts_speak", "media_rescan", "tiktok_products_sync", "monitor_config", "monitor_start", "monitor_stop"}


def lane(kind: str) -> str:
    return "background" if kind in BACKGROUND_KINDS else "live"


class Payload(BaseModel):
    model_config = {"extra": "forbid"}


class NoPayload(Payload):
    pass


class PlayPayload(Payload):
    media_id: str = MediaId


class MediaIdsPayload(Payload):
    media_ids: list[str] = MediaIds

    @field_validator("media_ids")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        if any(not (len(m) == 64 and all(c in "0123456789abcdef" for c in m)) for m in value):
            raise ValueError("Danh sách media_ids không hợp lệ")
        return value


class PlaylistPayload(MediaIdsPayload):
    loop: bool = True


class ProductPlayPayload(MediaIdsPayload):
    session_id: int = Field(ge=1)
    product_id: int = Field(ge=1)
    mode: Literal["immediate", "after_current"] = "immediate"


class SelectPayload(Payload):
    index: int = Field(ge=0, le=9999)


class SeekPayload(Payload):
    ms: int = Field(ge=0, le=24 * 3600 * 1000)


class ObsConnectionPayload(Payload):
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=4455, ge=1, le=65535)
    password: str | None = Field(default="", max_length=200)


class ObsConfigurePayload(ObsConnectionPayload):
    input_name: str = Field(default="Sarae Video", min_length=1, max_length=200)
    dual_mode: bool = False
    source_a: str | None = Field(default="", max_length=200)
    source_b: str | None = Field(default="", max_length=200)
    camera_input: str | None = Field(default="", max_length=200)


class TikTokSyncPayload(Payload):
    expected_user_id: str = Field(pattern=r"^[0-9]{1,80}$")


class TtsPayload(Payload):
    text: str = Field(min_length=1, max_length=450)
    voice: Literal["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"] = "vi-VN-HoaiMyNeural"


class MonitorConfigPayload(Payload):
    interval_seconds: float = Field(default=2.0, ge=0.5, le=15)
    threshold: float = Field(default=0.82, ge=0.65, le=0.99)
    telegram_enabled: bool = False
    telegram_chat_id: str = Field(default="", max_length=50)


class CommandRouter:
    def __init__(self, *, playback, monitor, voice, rescan: Callable[[], dict] | None = None, tiktok_sync: Callable[[], dict] | None = None):
        """``playback`` is playback.engine.PlaybackEngine; ``rescan`` runs scan + Cloud library sync."""
        self.playback = playback
        self.monitor = monitor
        self.voice = voice
        self.rescan = rescan
        self.tiktok_sync = tiktok_sync
        p = playback

        def obs_configure(b: ObsConfigurePayload) -> dict:
            p.configure(host=b.host.strip(), port=b.port, password=b.password or "", input_name=b.input_name.strip(),
                        dual_mode=b.dual_mode, source_a=(b.source_a or "").strip(), source_b=(b.source_b or "").strip(),
                        camera_input=(b.camera_input or "").strip())
            # Status carries has_password only; the password itself is never echoed back.
            return p.status()

        self._table: dict[str, tuple[type[BaseModel], Callable[[BaseModel], object]]] = {
            "monitor_start": (NoPayload, lambda _: monitor.start()),
            "monitor_stop": (NoPayload, lambda _: monitor.stop()),
            "monitor_config": (MonitorConfigPayload, lambda b: monitor.configure(b.model_dump())),
            "obs_status": (NoPayload, lambda _: p.status()),
            "obs_configure": (ObsConfigurePayload, obs_configure),
            "obs_inputs": (ObsConnectionPayload, lambda b: p.list_inputs(b.host.strip(), b.port, b.password or "")),
            "local_media_play": (PlayPayload, lambda b: p.start_playlist([b.media_id], loop=False)),
            "local_playlist_start": (PlaylistPayload, lambda b: p.start_playlist(b.media_ids, loop=b.loop)),
            "product_play": (ProductPlayPayload,
                             lambda b: p.product_play(b.session_id, b.product_id, b.media_ids, b.mode)),
            "playback_cancel_pending": (NoPayload, lambda _: p.cancel_pending()),
            "playback_select": (SelectPayload, lambda b: p.select(b.index)),
            "playback_seek": (SeekPayload, lambda b: p.seek(b.ms)),
            "playback_camera": (NoPayload, lambda _: p.use_camera()),
            "playback_video": (NoPayload, lambda _: p.use_video()),
            "media_rescan": (NoPayload, lambda _: self._rescan()),
            "tiktok_products_sync": (TikTokSyncPayload, lambda b: self._tiktok_sync(b.expected_user_id)),
            "tts_speak": (TtsPayload, lambda b: voice.speak(b.text, b.voice)),
        }
        for kinds, action in ((("local_media_next", "obs_next", "playback_next"), p.next),
                              (("local_media_stop", "obs_stop", "playback_stop"), p.stop),
                              (("obs_pause", "playback_pause"), p.pause),
                              (("obs_resume", "playback_resume"), p.resume),
                              (("obs_previous", "playback_previous"), p.previous)):
            for kind in kinds:
                self._table[kind] = (NoPayload, lambda _, act=action: act())

    def _rescan(self) -> dict:
        if self.rescan is None:
            raise AgentError("AGENT_NOT_CONNECTED", "Agent chưa kết nối Cloud")
        return self.rescan()

    def _tiktok_sync(self, expected_user_id: str) -> dict:
        if self.tiktok_sync is None:
            raise AgentError("TIKTOK_SYNC_UNAVAILABLE", "Agent chưa hỗ trợ đồng bộ TikTok qua Cloud")
        result=self.tiktok_sync(expected_user_id)
        # Cloud command ACK has a size limit; the full catalog is already stored
        # by /tiktok-products/sync and fetched separately by the Web.
        return {"agent_id":result.get("agent_id"),
                "scanned_at":result.get("scanned_at"),
                "account":result.get("account"),
                "summary":result.get("summary"),
                "imported":result.get("imported")}

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
        return action(body)
