"""Cloud command kinds -> local actions. Payloads are re-validated locally (never trust the wire).

Kinds and payload limits mirror backend/app/cloud_agents.py (COMMAND_KINDS, validate_payload); the lanes
mirror its BACKGROUND_KINDS so the Agent runs each lane in order, like the Cloud hands them out.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from sarae_agent.errors import AgentError
from sarae_agent.media.cloud_cache import MEDIA_ID
from sarae_agent.playback.engine import PolicyUpdate

# Sarae Media Library ids (32-hex); the files come from the Agent cloud media cache.
MediaId = Field(pattern=MEDIA_ID.pattern)
MediaIds = Field(min_length=1, max_length=300)

# Cloud lane "background"; everything else is the "live" lane (OBS playback).
BACKGROUND_KINDS = {"PIN_PRODUCT", "UNPIN_PRODUCT", "GET_PIN_STATE", "SET_AUTO_PIN", "tts_speak", "tts_prepare", "tiktok_products_sync", "media_prepare", "monitor_config", "monitor_start", "monitor_stop"}
# Cloud lane "chat": TikTok LIVE chat replies (docs/AI_AUTO_REPLY.md), never behind a TTS or a product switch.
CHAT_KINDS = {"chat_send", "chat_probe"}


def lane(kind: str) -> str:
    if kind in CHAT_KINDS:
        return "chat"
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
        if any(not (isinstance(m, str) and MEDIA_ID.fullmatch(m)) for m in value):
            raise ValueError("Danh sách media_ids không hợp lệ")
        return value


class PlaylistPayload(MediaIdsPayload):
    loop: bool = True


class ProductPlayPayload(MediaIdsPayload):
    session_id: int = Field(ge=1)
    product_id: int = Field(ge=1)
    mode: Literal["immediate", "after_current"] = "immediate"


class PlaybackPolicyPayload(PolicyUpdate, Payload):
    """docs/PLAYBACK_POLICY.md: session_id, policy, lineup (<=300 products, <=3000 media ids)."""


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


class MediaPreparePayload(Payload):
    session_id: int = Field(gt=0)


class TikTokSyncPayload(Payload):
    expected_user_id: str = Field(pattern=r"^[0-9]{1,80}$")


class PinTarget(Payload):
    tiktok_product_id: str | None = Field(default=None, pattern=r"^[0-9]{1,80}$")
    name: str = Field(min_length=1, max_length=200)
    image_url: str = Field(default="", max_length=1000)
    price_text: str = Field(default="", max_length=80)
    expected_user_id: str = Field(default="", max_length=80)


class PinPayload(Payload):
    session_id: int = Field(ge=1)
    target: PinTarget
    confirmed_token: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{1,80}$")


class PinStatePayload(Payload):
    target: PinTarget | None = None


class UnpinPayload(Payload):
    session_id: int = Field(ge=1)
    expected_user_id: str = Field(pattern=r"^[0-9]{1,80}$")


class AutoPinProduct(PinTarget):
    sarae_product_id: int = Field(ge=1)


class TimelineMarker(Payload):
    id: int = Field(ge=1)
    at_ms: int = Field(ge=0, le=24 * 3600 * 1000)
    action: Literal["PIN_PRODUCT", "UNPIN_PRODUCT", "NO_ACTION"]
    product_id: int | None = Field(default=None, ge=1)
    seq: int = Field(default=0, ge=0)


class TimelinePayload(Payload):
    """An approved, active timeline version (backend/app/live_timeline.py agent_timeline)."""
    version: int = Field(ge=1)
    markers: list[TimelineMarker] = Field(default_factory=list, max_length=2000)
    products: list[AutoPinProduct] = Field(default_factory=list, max_length=300)
    expected_user_id: str = Field(default="", pattern=r"^[0-9]{0,80}$")


class VideoPlayPayload(Payload):
    session_id: int = Field(ge=1)
    media_id: str = MediaId
    loop: bool = True
    start_ms: int = Field(default=0, ge=0, le=24 * 3600 * 1000)
    timeline: TimelinePayload | None = None


class TimelineSetPayload(Payload):
    session_id: int = Field(ge=1)
    timeline: TimelinePayload | None = None


class SetAutoPinPayload(Payload):
    enabled: bool
    session_id: int | None = Field(default=None, ge=1)
    products: list[AutoPinProduct] = Field(default_factory=list, max_length=300)


class TtsPayload(Payload):
    text: str = Field(min_length=1, max_length=450)
    voice: str = Field(default="vi-VN-HoaiMyNeural", min_length=1, max_length=100)
    provider: Literal["edge", "elevenlabs"] = "edge"
    model: str | None = Field(default=None, min_length=1, max_length=100)
    settings: dict = Field(default_factory=dict)
    # AI voice (backend/app/live_reply_director.py): speed in percent, not spoken after ttl_seconds, video ducking.
    rate: int = Field(default=0, ge=-30, le=50)
    ttl_seconds: float | None = Field(default=None, ge=3, le=300)
    duck: bool = True
    # Viewer names said before `text` ("Lan với Hoa ơi,"): changes per viewer, while `text` repeats and is cached.
    intro: str | None = Field(default=None, min_length=1, max_length=120)


class TtsPreparePayload(Payload):
    """Answers generated into the voice cache before they are needed (nothing is played)."""
    texts: list[str] = Field(min_length=1, max_length=40)
    voice: str = Field(default="vi-VN-HoaiMyNeural", min_length=1, max_length=100)
    provider: Literal["edge", "elevenlabs"] = "edge"
    model: str | None = Field(default=None, min_length=1, max_length=100)
    settings: dict = Field(default_factory=dict)
    rate: int = Field(default=0, ge=-30, le=50)

    @field_validator("texts")
    @classmethod
    def _texts(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(t) <= 450 for t in value):
            raise ValueError("Mỗi câu TTS từ 1 đến 450 ký tự")
        return value


class ChatTargetPayload(Payload):
    msg_id: str | None = Field(default=None, min_length=1, max_length=80)
    user_id: str | None = Field(default=None, pattern=r"^[0-9]{1,80}$")
    username: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._]{1,64}$")
    nickname: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=500)


class ChatSendPayload(Payload):
    """docs/AI_AUTO_REPLY.md "Agent command chat_send", plus the desk's pacing."""
    reply_id: int = Field(ge=1)
    session_id: int = Field(ge=1)
    expected_user_id: str = Field(pattern=r"^[0-9]{1,80}$")
    ttl_seconds: float = Field(ge=1, le=600)
    text: str = Field(min_length=1, max_length=300)
    target: ChatTargetPayload
    min_interval_seconds: float = Field(default=8, ge=0, le=600)
    max_per_minute: int = Field(default=5, ge=1, le=60)

    @field_validator("text")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Nội dung bình luận trống")
        return value


class MonitorConfigPayload(Payload):
    interval_seconds: float = Field(default=2.0, ge=0.5, le=15)
    threshold: float = Field(default=0.82, ge=0.65, le=0.99)
    telegram_enabled: bool = False
    telegram_chat_id: str = Field(default="", max_length=50)


class CommandRouter:
    def __init__(self, *, playback, monitor, voice, tiktok_sync: Callable[[], dict] | None = None,
                 media_prepare: Callable[[int], dict] | None = None, chat=None, auto_pin=None, timeline=None):
        """``playback`` is playback.engine.PlaybackEngine; ``tiktok_sync`` scans TikTok and syncs the Cloud;
        ``media_prepare`` starts downloading a LIVE session's videos into the cache (returns at once);
        ``chat`` (auto_pin.AutoPinService) posts LIVE chat replies: chat_send(payload) / chat_probe()."""
        self.playback = playback
        self.monitor = monitor
        self.voice = voice
        self.tiktok_sync = tiktok_sync
        self.media_prepare = media_prepare
        self.chat = chat
        self.auto_pin = auto_pin
        self.timeline = timeline
        p = playback

        def obs_configure(b: ObsConfigurePayload) -> dict:
            p.configure(host=b.host.strip(), port=b.port, password=b.password or "", input_name=b.input_name.strip(),
                        dual_mode=b.dual_mode, source_a=(b.source_a or "").strip(), source_b=(b.source_b or "").strip(),
                        camera_input=(b.camera_input or "").strip())
            # Status carries has_password only; the password itself is never echoed back.
            return p.status()

        def tts_options(b: TtsPayload | TtsPreparePayload) -> dict:
            return {name: getattr(b, name) for name in ("provider", "model", "settings")
                    if name in b.model_fields_set}

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
            "video_play": (VideoPlayPayload, self._video_play),
            "timeline_set": (TimelineSetPayload, self._timeline_set),
            "playback_cancel_pending": (NoPayload, lambda _: p.cancel_pending()),
            "playback_policy": (PlaybackPolicyPayload,
                                lambda b: p.set_policy(b.session_id, b.policy.model_dump(),
                                                       [item.model_dump() for item in b.lineup])),
            "playback_select": (SelectPayload, lambda b: p.select(b.index)),
            "playback_seek": (SeekPayload, lambda b: p.seek(b.ms)),
            "playback_camera": (NoPayload, lambda _: p.use_camera()),
            "playback_video": (NoPayload, lambda _: p.use_video()),
            "tiktok_products_sync": (TikTokSyncPayload, lambda b: self._tiktok_sync(b.expected_user_id)),
            "media_prepare": (MediaPreparePayload, lambda b: self._media_prepare(b.session_id)),
            "PIN_PRODUCT": (PinPayload, lambda b: auto_pin.pin_product({**b.target.model_dump(),
                **({"confirmed_token": b.confirmed_token} if b.confirmed_token is not None else {})},
                current=lambda: bool(p.active and p.session_id == b.session_id), guard=p.lock)),
            "UNPIN_PRODUCT": (UnpinPayload, lambda b: auto_pin.unpin_product(b.expected_user_id,
                current=lambda: bool(p.active and p.session_id == b.session_id), guard=p.lock)),
            "GET_PIN_STATE": (PinStatePayload, lambda b: auto_pin.get_pin_state(b.target.model_dump() if b.target else None)),
            "SET_AUTO_PIN": (SetAutoPinPayload, lambda b: auto_pin.set_video_auto(b.enabled,
                {item.sarae_product_id: item.model_dump(exclude={"sarae_product_id"}) for item in b.products},
                b.session_id)),
            "tts_speak": (TtsPayload, lambda b: voice.speak(b.text, b.voice, rate=b.rate, ttl_seconds=b.ttl_seconds,
                                                            duck=b.duck, intro=b.intro, **tts_options(b))),
            "tts_prepare": (TtsPreparePayload, lambda b: voice.prepare(b.texts, b.voice, b.rate,
                                                                          **tts_options(b))),
            "tts_clear": (NoPayload, lambda _: voice.clear()),
            "chat_send": (ChatSendPayload, lambda b: self._chat().chat_send(b.model_dump())),
            "chat_probe": (NoPayload, lambda _: self._chat().chat_probe()),
        }
        for kinds, action in ((("local_media_next", "obs_next", "playback_next"), p.next),
                              (("local_media_stop", "obs_stop", "playback_stop"), self._stop),
                              (("obs_pause", "playback_pause"), p.pause),
                              (("obs_resume", "playback_resume"), p.resume),
                              (("obs_previous", "playback_previous"), p.previous)):
            for kind in kinds:
                self._table[kind] = (NoPayload, lambda _, act=action: act())

    def _scheduler(self):
        if self.timeline is None:
            raise AgentError("TIMELINE_UNAVAILABLE", "Agent chưa hỗ trợ timeline ghim sản phẩm")
        return self.timeline

    def _video_play(self, b: VideoPlayPayload) -> dict:
        scheduler = self._scheduler()
        result = self.playback.video_play(b.session_id, b.media_id, loop=b.loop, start_ms=b.start_ms)
        if not result.get("unchanged"):
            scheduler.load(b.session_id, b.timeline.model_dump() if b.timeline else None)
        return {**result, "timeline": scheduler.status()}

    def _timeline_set(self, b: TimelineSetPayload) -> dict:
        """A new active version during LIVE: only the pin schedule changes, never playback or OBS."""
        scheduler = self._scheduler()
        p = self.playback
        with p.lock:
            running = p.active and p.session_mode == "video" and p.session_id == b.session_id
        if not running:
            raise AgentError("SESSION_NOT_PLAYING", f"Phiên LIVE #{b.session_id} không phát trên Agent này")
        return scheduler.load(b.session_id, b.timeline.model_dump() if b.timeline else None)

    def _stop(self):
        result = self.playback.stop()
        if self.timeline is not None:
            self.timeline.clear("stopped")
        return result

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

    def _chat(self):
        if self.chat is None:
            raise AgentError("CHAT_UNAVAILABLE", "Agent chưa hỗ trợ gửi bình luận TikTok LIVE")
        return self.chat

    def _media_prepare(self, session_id: int) -> dict:
        if self.media_prepare is None:
            raise AgentError("MEDIA_PREPARE_UNAVAILABLE", "Agent chưa hỗ trợ tải video về cache")
        status = self.media_prepare(session_id)
        # Progress goes up with heartbeats; the ACK only says the download was accepted.
        return {"session_id": status.get("session_id"), "state": status.get("state"), "total": status.get("total")}

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
