"""Operator-approved Vietnamese TTS, played by the OWNER's Windows Agent on a separate OBS Media Source.

Create a Media Source named 'Sarae Voice' in OBS and enable its audio in the mixer.
This is not automatic TikTok replying and does not touch the video media input.
"""
import asyncio
from threading import RLock
from typing import Literal

import httpx

from fastapi import APIRouter, Depends, HTTPException

from app.identity import current_owner, owner_settings, update_owner_settings
from app.ownership import Owner
from app.per_owner import PerOwner
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.comment_processing import monitors
from app import credentials

router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"], dependencies=[Depends(current_owner)])


VOICE_SOURCE = "Sarae Voice"
VOICE = "vi-VN-HoaiMyNeural"
SYNTH_ATTEMPTS = 4
# Previews are generated on the shared server; one at a time per OWNER.
preview_locks = PerOwner(lambda _owner_id: RLock())


class VoiceConfig(BaseModel):
    provider: Literal["edge", "elevenlabs"] = "edge"
    model: str = Field(default="edge-neural", min_length=1, max_length=100)
    voice: str = Field(default=VOICE, min_length=1, max_length=100)
    rate: int = Field(default=5, ge=-30, le=50)
    settings: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_spec(self):
        if self.provider == "edge":
            if self.model != "edge-neural" or self.voice not in (VOICE, "vi-VN-NamMinhNeural"):
                raise ValueError("Giọng hoặc model Edge TTS không hợp lệ")
            if self.settings:
                raise ValueError("Edge TTS không nhận voice settings")
        elif not all(c.isalnum() or c in "_-" for c in self.model + self.voice):
            raise ValueError("Model ID hoặc Voice ID ElevenLabs không hợp lệ")
        allowed = {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
        if self.settings.keys() - allowed:
            raise ValueError("Voice settings không hợp lệ")
        for name in ("stability", "similarity_boost", "style"):
            value = self.settings.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1):
                raise ValueError(f"{name} phải từ 0 đến 1")
        speed = self.settings.get("speed")
        if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.7 <= speed <= 1.2):
            raise ValueError("speed phải từ 0.7 đến 1.2")
        boost = self.settings.get("use_speaker_boost")
        if boost is not None and not isinstance(boost, bool):
            raise ValueError("use_speaker_boost không hợp lệ")
        return self


def voice_config(owner_id: str) -> VoiceConfig:
    saved = owner_settings(owner_id)
    if saved.get("voice"):
        return VoiceConfig.model_validate(saved["voice"])
    old = saved.get("ai_reply") or {}
    return VoiceConfig(voice=old.get("voice", VOICE), rate=old.get("rate", 5))


@router.get("/config")
def read_voice_config(owner: Owner):
    config = voice_config(owner["id"])
    return {**config.model_dump(), "has_api_key": bool(credentials.secret(owner["id"], "elevenlabs"))}


@router.put("/config")
def save_voice_config(body: VoiceConfig, owner: Owner):
    update_owner_settings(owner["id"], voice=body.model_dump())
    return read_voice_config(owner)


class VoiceKeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=400)


@router.put("/key")
def save_voice_key(body: VoiceKeyBody, owner: Owner):
    credentials.save(owner["id"], "elevenlabs", body.api_key)
    return {"configured": True}


@router.delete("/key")
def delete_voice_key(owner: Owner):
    credentials.delete(owner["id"], "elevenlabs")
    return {"configured": False}


@router.get("/catalog")
def voice_catalog(owner: Owner):
    """Fetch current models and account voices; no API key or provider payload is returned."""
    key = credentials.secret(owner["id"], "elevenlabs")
    if not key:
        return {"models": [], "voices": [], "error": "Chưa cấu hình ElevenLabs API key"}
    try:
        with httpx.Client(timeout=8) as client:
            headers = {"xi-api-key": key}
            models_response = client.get("https://api.elevenlabs.io/v1/models", headers=headers)
            voices_response = client.get("https://api.elevenlabs.io/v2/voices", headers=headers,
                                         params={"page_size": 100})
            models_response.raise_for_status()
            voices_response.raise_for_status()
            models = [{"id": m["model_id"], "name": m.get("name") or m["model_id"],
                       "can_use_style": bool(m.get("can_use_style")),
                       "can_use_speaker_boost": bool(m.get("can_use_speaker_boost"))}
                      for m in models_response.json() if m.get("can_do_text_to_speech")]
            voices = [{"id": v["voice_id"], "name": v.get("name") or v["voice_id"]}
                      for v in voices_response.json().get("voices", [])]
            return {"models": models, "voices": voices}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"models": [], "voices": [], "error": f"Không tải được danh sách ElevenLabs: {type(exc).__name__}"}

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
    payload = {"text": text, "provider": config.provider, "model": config.model,
               "voice": body.voice or config.voice, "rate": config.rate, "settings": config.settings}
    sent = send_command(owner, body.agent_id, "tts_speak", payload)
    monitors(owner["id"]).record("TTS_QUEUED", f"{config.provider}/{config.model}/{payload['voice']}",
                                   f"Gửi tới Agent {sent['agent']['hostname']}: {text}")
    return {"status": "sent_to_agent", "agent": sent["agent"], "command_id": sent["command_id"],
            "voice": payload["voice"], "text": text}


class PreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    voice: str | None = Field(default=None, min_length=1, max_length=100)
    rate: int | None = Field(default=None, ge=-30, le=50)

@router.post("/preview")
def preview(body: PreviewBody, owner: Owner):
    """Generate a Vietnamese MP3 for browser playback; OBS and LIVE are not required."""
    config = voice_config(owner["id"])
    voice = body.voice or config.voice
    rate = body.rate if body.rate is not None else config.rate
    if config.provider == "elevenlabs":
        if not all(c.isalnum() or c in "_-" for c in voice):
            raise HTTPException(422, "Voice ID ElevenLabs không hợp lệ")
        key = credentials.secret(owner["id"], "elevenlabs")
        if not key:
            raise HTTPException(409, "Chưa cấu hình ElevenLabs API key")
        client = httpx.Client(timeout=httpx.Timeout(25, connect=6))
        try:
            request = client.build_request("POST", f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream",
                headers={"xi-api-key": key, "Accept": "audio/mpeg"},
                params={"output_format": "mp3_44100_128"},
                json={"text": body.text, "model_id": config.model,
                      "voice_settings": {"speed": round(1 + rate / 100, 2), **config.settings}})
            response = client.send(request, stream=True)
            if response.status_code >= 400:
                code = response.status_code
                response.close()
                client.close()
                reason = {401: "API key không hợp lệ", 402: "Không đủ credits", 403: "Không có quyền dùng model hoặc voice",
                          429: "Đang bị giới hạn tốc độ"}.get(code, f"HTTP {code}")
                raise HTTPException(502, f"ElevenLabs: {reason}")
        except httpx.HTTPError as exc:
            client.close()
            raise HTTPException(502, f"Không kết nối được ElevenLabs: {type(exc).__name__}") from exc

        def chunks():
            try:
                yield from response.iter_bytes(8192)
            finally:
                response.close()
                client.close()
        return StreamingResponse(chunks(), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})
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
