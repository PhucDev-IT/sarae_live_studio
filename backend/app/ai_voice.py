"""Operator-approved Vietnamese TTS, played by the OWNER's Windows Agent on a separate OBS Media Source.

Create a Media Source named 'Sarae Voice' in OBS and enable its audio in the mixer.
This is not automatic TikTok replying and does not touch the video media input.
"""
import asyncio
from threading import RLock

from fastapi import APIRouter, Depends, Header, HTTPException

from app.identity import current_owner
from app.ownership import Owner
from app.per_owner import PerOwner
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.comment_processing import monitors

router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"], dependencies=[Depends(current_owner)])


VOICE_SOURCE = "Sarae Voice"
VOICE = "vi-VN-HoaiMyNeural"
# Previews are generated on the shared server; one at a time per OWNER.
preview_locks = PerOwner(lambda _owner_id: RLock())

class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=450)
    voice: str = Field(default=VOICE, pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")
    obs_source: str = Field(default=VOICE_SOURCE, min_length=1, max_length=100)

@router.post("/speak")
def speak_on_agent(body: SpeakBody, owner: Owner, authorization: str | None = Header(None)):
    """Read the text aloud on the OBS of the OWNER's own Windows Agent (it runs `speak` there)."""
    from app.owner_agents import send_command
    sent = send_command(owner, authorization, "tts_speak", {"text": body.text, "voice": body.voice})
    monitors(owner["id"]).record("TTS_QUEUED", body.voice, f"Gửi tới Agent {sent['agent']['hostname']}: {body.text}")
    return {"status": "sent_to_agent", "agent": sent["agent"], "command_id": sent["command_id"],
            "voice": body.voice, "text": body.text}


class PreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    voice: str = Field(default=VOICE, pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")

@router.post("/preview")
def preview(body: PreviewBody, owner: Owner):
    """Generate a Vietnamese MP3 for browser playback; OBS and LIVE are not required."""
    monitor = monitors(owner["id"])
    voice_lock = preview_locks(owner["id"])
    if not voice_lock.acquire(blocking=False):
        raise HTTPException(409, "Một yêu cầu tạo giọng nói khác đang chạy")
    try:
        try:
            import edge_tts
        except ImportError as exc:
            raise HTTPException(503, "Chưa cài edge-tts: python -m pip install edge-tts") from exc
        async def synthesize():
            chunks = []
            async for chunk in edge_tts.Communicate(body.text, body.voice).stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)
        try:
            audio = asyncio.run(synthesize())
            if not audio:
                raise ValueError("Không nhận được âm thanh từ TTS")
            monitor.record("TTS_PREVIEW", body.voice, f"Đã tạo âm thanh thử ({len(audio)} bytes).")
            return Response(content=audio, media_type="audio/mpeg",
                            headers={"Cache-Control": "no-store"})
        except Exception as exc:
            monitor.record("TTS_ERROR", type(exc).__name__, str(exc)[:250])
            raise HTTPException(502, f"Lỗi tạo giọng nói: {type(exc).__name__}: {str(exc)[:250]}") from exc
    finally:
        voice_lock.release()
