"""Operator-approved Vietnamese TTS, played by the OWNER's Windows Agent on a separate OBS Media Source.

Create a Media Source named 'Sarae Voice' in OBS and enable its audio in the mixer.
This is not automatic TikTok replying and does not touch the video media input.
"""
import asyncio
from threading import RLock
from typing import Literal


from fastapi import APIRouter, Depends, HTTPException

from app.voice_policy import owner_voice_lock, require_voice_mutable

from app.identity import current_owner, owner_settings, update_owner_settings
from app.ownership import Owner
from app.per_owner import PerOwner
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.comment_processing import monitors

router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"], dependencies=[Depends(current_owner)])


VOICE_SOURCE = "Sarae Voice"
VOICE = "vi-VN-HoaiMyNeural"
SYNTH_ATTEMPTS = 4
# Previews are generated on the shared server; one at a time per OWNER.
preview_locks = PerOwner(lambda _owner_id: RLock())


OMNIVOICE_PRESET = "demo_default"


class FallbackPolicy(BaseModel):
    """What to do when G-OmniVoice cannot take a sentence (docs/TTS_SERVER.md 7.2). Default none everywhere: no voice
    is ever switched unless the owner chose it. Expired, cancelled or ended-session sentences never fall back."""
    live_reply: Literal["none", "comment_only", "provider", "hold_for_operator"] = "none"
    prepare: Literal["none", "provider"] = "none"
    manual: Literal["none", "provider"] = "none"
    # {"provider": "edge", "model", "voice", "rate", "settings"} used when a mode is "provider".
    provider: dict | None = None

    @model_validator(mode="after")
    def valid_provider(self):
        if "provider" in (self.live_reply, self.prepare, self.manual):
            if not self.provider:
                raise ValueError("Chọn giọng dự phòng (Edge TTS) cho chế độ provider")
        if self.provider is not None:
            spec = VoiceConfig.model_validate({**self.provider, "fallback": None})
            if spec.provider == "omnivoice":
                raise ValueError("Giọng dự phòng phải là Edge TTS")
            self.provider = {k: getattr(spec, k) for k in ("provider", "model", "voice", "rate", "settings")}
        return self


class VoiceConfig(BaseModel):
    provider: Literal["edge", "omnivoice"] = "edge"
    model: str = Field(default="edge-neural", min_length=1, max_length=100)
    voice: str = Field(default=VOICE, min_length=1, max_length=100)
    rate: int = Field(default=5, ge=-30, le=50)
    settings: dict = Field(default_factory=dict)
    fallback: FallbackPolicy | None = Field(default_factory=FallbackPolicy)

    @model_validator(mode="after")
    def valid_spec(self):
        if self.fallback is None:
            self.fallback = FallbackPolicy()
        if self.provider == "omnivoice":
            # G-OmniVoice: `voice` is a voice profile id of this owner, the preset is the verified demo default,
            # and nothing tunes it (no speed or voice settings).
            if self.model != OMNIVOICE_PRESET or self.settings or self.rate != 0:
                raise ValueError("G-OmniVoice dùng preset demo_default, rate 0 và không có voice settings")
            if len(self.voice) != 32 or not all(c in "0123456789abcdef" for c in self.voice):
                raise ValueError("Voice G-OmniVoice phải là id của một giọng đã tải lên")
            return self
        if self.provider == "edge":
            if self.model != "edge-neural" or self.voice not in (VOICE, "vi-VN-NamMinhNeural"):
                raise ValueError("Giọng hoặc model Edge TTS không hợp lệ")
            if self.settings:
                raise ValueError("Edge TTS không nhận voice settings")
        return self


def normalize_saved_voice(saved: dict) -> dict:
    """Read legacy settings without writing production data or retaining a removed fallback."""
    if saved.get("provider") == "elevenlabs":
        return VoiceConfig().model_dump()
    saved = dict(saved)
    policy = dict(saved.get("fallback") or {})
    if (policy.get("provider") or {}).get("provider") == "elevenlabs":
        policy = {**policy, "provider": None}
        for context in ("live_reply", "prepare", "manual"):
            if policy.get(context) == "provider":
                policy[context] = "none"
        saved["fallback"] = policy
    return saved


def retired_voice_notice(owner_id: str) -> str | None:
    saved = owner_settings(owner_id).get("voice") or {}
    if saved.get("provider") == "elevenlabs":
        return "Provider cũ đã được gỡ bỏ. Cấu hình hiện dùng Edge TTS mặc định; hãy chọn và lưu giọng muốn dùng."
    if ((saved.get("fallback") or {}).get("provider") or {}).get("provider") == "elevenlabs":
        return "Provider dự phòng cũ đã được gỡ bỏ và fallback tương ứng đã tắt. Hãy kiểm tra và lưu cấu hình."
    return None


def voice_config(owner_id: str) -> VoiceConfig:
    saved = owner_settings(owner_id)
    if saved.get("voice"):
        return VoiceConfig.model_validate(normalize_saved_voice(saved["voice"]))
    old = saved.get("ai_reply") or {}
    return VoiceConfig(voice=old.get("voice", VOICE), rate=old.get("rate", 5))


@router.get("/config")
def read_voice_config(owner: Owner):
    from app import tts_server
    config = voice_config(owner["id"])
    return {**config.model_dump(), "migration_notice": retired_voice_notice(owner["id"]),
            "omnivoice_configured": tts_server.configured()}


@router.put("/config")
def save_voice_config(body: VoiceConfig, owner: Owner):
    with owner_voice_lock(owner["id"]):
        if body.provider == "omnivoice":
            from app import voice_profiles
            with voice_profiles.SessionLocal() as db:
                voice_profiles.owned(db, owner["id"], body.voice)          # 404 unless it is one of this owner's voices
        current = voice_config(owner["id"])
        if body.model_dump(exclude={"fallback"}) != current.model_dump(exclude={"fallback"}) and "omnivoice" in (body.provider, current.provider):
            from app import voice_profiles
            with voice_profiles.SessionLocal() as db:
                require_voice_mutable(db, owner["id"])
        update_owner_settings(owner["id"], voice=body.model_dump())
        return read_voice_config(owner)


def tts_http_error(exc) -> HTTPException:
    """SpeakRefused / TtsUnavailable / TtsError as an HTTP error the Web can show; the code is in X-Sarae-Error."""
    status = getattr(exc, "status", 503)
    status = status if isinstance(status, int) and 400 <= status < 600 else 503
    return HTTPException(status, f"G-OmniVoice: {exc.message or exc.code}", headers={"X-Sarae-Error": exc.code})


class SpeakBody(BaseModel):
    # The Windows Agent whose OBS plays the speech; required, the server never guesses it.
    agent_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=450)
    voice: str | None = Field(default=None, min_length=1, max_length=100)
    obs_source: str = Field(default=VOICE_SOURCE, min_length=1, max_length=100)

@router.post("/speak")
def speak_on_agent(body: SpeakBody, owner: Owner):
    """Read the text aloud on the OBS of one of the OWNER's Windows Agents."""
    from app.owner_agents import send_command
    from app.speech_text import spoken
    # Numbers, prices and short forms are read as words ("200k" -> "hai trăm nghìn").
    text = spoken(body.text)[:450]
    config = voice_config(owner["id"])
    if config.provider == "omnivoice":
        from app import tts_orchestrator
        from app.owner_agents import owned_agent
        agent = owned_agent(owner, body.agent_id, online=True)
        try:
            done = tts_orchestrator.speak(owner["id"], agent["id"], text=text, intro=None, ttl_seconds=120, duck=True,
                                          session_id=None, reply_id=None, source="manual")
            monitors(owner["id"]).record("TTS_QUEUED", f"omnivoice/{config.voice}", f"Gửi tới Agent {agent['hostname']}: {text}")
            return {"status": "sent_to_agent", "agent": {k: agent[k] for k in ("id", "hostname", "platform", "online", "last_seen")},
                    "command_id": done["command"]["command_id"], "voice": config.voice, "text": text,
                    "utterances": done["utterances"]}
        except tts_orchestrator.SpeakRefused as exc:
            mode, spec = tts_orchestrator.fallback(owner["id"], "manual")
            if mode != "provider" or not exc.fallback_ok:
                raise tts_http_error(exc) from exc
            config = VoiceConfig.model_validate({**spec, "fallback": None})
            monitors(owner["id"]).record("TTS_FALLBACK", exc.code, f"Đọc bằng {config.provider} thay cho G-OmniVoice")
    payload = {"text": text, "provider": config.provider, "model": config.model,
               "voice": body.voice if body.voice and config.provider != "omnivoice" else config.voice,
               "rate": config.rate, "settings": config.settings}
    sent = send_command(owner, body.agent_id, "tts_speak", payload)
    monitors(owner["id"]).record("TTS_QUEUED", f"{config.provider}/{config.model}/{payload['voice']}",
                                   f"Gửi tới Agent {sent['agent']['hostname']}: {text}")
    return {"status": "sent_to_agent", "agent": sent["agent"], "command_id": sent["command_id"],
            "voice": payload["voice"], "text": text}


class PreviewBody(BaseModel):
    voice_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    text: str = Field(min_length=1, max_length=1000)
    voice: str | None = Field(default=None, min_length=1, max_length=100)
    rate: int | None = Field(default=None, ge=-30, le=50)

@router.post("/preview")
def preview(body: PreviewBody, owner: Owner):
    """Generate a Vietnamese MP3 for browser playback; OBS and LIVE are not required."""
    config = voice_config(owner["id"])
    if body.voice_id is not None:
        config = VoiceConfig(provider="omnivoice", model=OMNIVOICE_PRESET, voice=body.voice_id, rate=0, settings={})
    voice = body.voice or config.voice
    rate = body.rate if body.rate is not None else config.rate
    if config.provider == "omnivoice":
        return _omnivoice_preview(owner, config, body.text)
    if voice not in (VOICE, "vi-VN-NamMinhNeural"):
        raise HTTPException(422, "Giọng Edge TTS không hợp lệ")
    monitor = monitors(owner["id"])
    voice_lock = preview_locks(owner["id"])
    # Previews of one owner wait for their turn (several "Nghe" clicks in a row) instead of failing.
    if not voice_lock.acquire(timeout=40):
        raise HTTPException(409, "Một yêu cầu tạo giọng nói khác đang chạy")
    try:
        try:
            import edge_tts
        except ImportError as exc:
            raise HTTPException(503, "Chưa cài edge-tts: python -m pip install edge-tts") from exc
        async def synthesize():
            # The Edge service now and then answers with no audio (NoAudioReceived, about 1 request in 10 for the
            # Vietnamese voices); the same request a moment later works, so it is retried a few times.
            for attempt in range(SYNTH_ATTEMPTS):
                try:
                    chunks = []
                    async for chunk in edge_tts.Communicate(body.text, voice, rate=f"{rate:+d}%").stream():
                        if chunk["type"] == "audio":
                            chunks.append(chunk["data"])
                    return b"".join(chunks)
                except edge_tts.exceptions.NoAudioReceived:
                    if attempt == SYNTH_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))
        try:
            audio = asyncio.run(synthesize())
            if not audio:
                raise ValueError("Không nhận được âm thanh từ TTS")
            monitor.record("TTS_PREVIEW", voice, f"Đã tạo âm thanh thử ({len(audio)} bytes).")
            return Response(content=audio, media_type="audio/mpeg",
                            headers={"Cache-Control": "no-store"})
        except Exception as exc:
            monitor.record("TTS_ERROR", type(exc).__name__, str(exc)[:250])
            raise HTTPException(502, f"Lỗi tạo giọng nói: {type(exc).__name__}: {str(exc)[:250]}") from exc
    finally:
        voice_lock.release()


def _omnivoice_preview(owner: dict, config: VoiceConfig, text: str):
    """Web -> Backend -> voice server -> WAV -> Web, with the owner's selected profile at its current revision.
    No fallback: a preview must sound like the chosen voice or fail with a clear code."""
    from app import tts_orchestrator, tts_server, voice_profiles
    from app.voice_profiles import ensure_synced, owned
    text = text.strip()
    if len(text) > 450:
        raise HTTPException(422, "Câu nghe thử G-OmniVoice tối đa 450 ký tự", headers={"X-Sarae-Error": "TEXT_TOO_LONG"})
    monitor = monitors(owner["id"])
    try:
        with voice_profiles.SessionLocal() as db:
            profile = owned(db, owner["id"], config.voice)
            ensure_synced(db, profile)
            profile_id, revision = profile.id, profile.revision
        audio, headers = tts_server.synthesize(profile_id, text, OMNIVOICE_PRESET)
    except (tts_server.TtsUnavailable, tts_server.TtsError, tts_orchestrator.SpeakRefused) as exc:
        monitor.record("TTS_ERROR", getattr(exc, "code", "TTS_ERROR"), str(getattr(exc, "message", ""))[:250])
        raise tts_http_error(exc) from exc
    monitor.record("TTS_PREVIEW", f"omnivoice/{profile_id}", f"Đã tạo âm thanh thử ({len(audio)} bytes).")
    return Response(content=audio, media_type="audio/wav",
                    headers={"Cache-Control": "no-store", "X-Voice-Revision": str(revision),
                             "X-Inference-Ms": headers.get("x-inference-ms", ""),
                             "X-Audio-Ms": headers.get("x-audio-ms", "")})
