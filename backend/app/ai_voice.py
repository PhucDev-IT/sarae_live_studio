"""Operator-approved Vietnamese TTS played through a separate OBS Media Source.

Create a Media Source named 'Sarae Voice' in OBS and enable its audio in the mixer.
This is not automatic TikTok replying and does not touch the video media input.
"""
import asyncio
from uuid import uuid4
from threading import RLock

from fastapi import APIRouter, Depends, Header, HTTPException

from app.identity import current_owner
from app.ownership import Owner
from app.per_owner import PerOwner
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.database import DATA_DIR
from app.live import state
from app.comment_processing import monitor, monitors

router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"], dependencies=[Depends(current_owner)])


VOICE_DIR = DATA_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)
VOICE_SOURCE = "Sarae Voice"
VOICE = "vi-VN-HoaiMyNeural"
voice_lock = RLock()
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


def speak(body: SpeakBody):
    """Generate the MP3 and play it on the local OBS 'Sarae Voice' source (runs on the Windows Agent)."""
    # Do not queue unlimited overlapping speech; explicit operator action only.
    if not voice_lock.acquire(blocking=False):
        raise HTTPException(409, "TTS đang xử lý một yêu cầu khác")
    try:
        try:
            import edge_tts
        except ImportError as exc:
            raise HTTPException(503, "Chưa cài edge-tts: python -m pip install edge-tts") from exc
        output = (VOICE_DIR / f"voice_{uuid4().hex}.mp3").resolve()
        async def generate():
            stream = edge_tts.Communicate(body.text, body.voice)
            await stream.save(str(output))
        monitor.record("TTS_START", body.voice, body.text)
        try:
            asyncio.run(generate())
            with state.lock:
                client = state.client()
                try:
                    # The source must already exist in OBS; never touch video sources.
                    client.get_input_settings(body.obs_source)
                    client.set_input_settings(body.obs_source, {
                        "local_file": str(output), "is_local_file": True,
                        "looping": False, "close_when_inactive": False
                    }, overlay=True)
                    client.set_input_mute(body.obs_source, False)
                    client.trigger_media_input_action(body.obs_source, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
                finally:
                    client.base_client.ws.close()
            monitor.record("TTS_PLAY", "obs", f"Đã gửi phát âm thanh tại nguồn OBS '{body.obs_source}'.")
            return {"status": "sent_to_obs", "source": body.obs_source, "voice": body.voice, "text": body.text}
        except Exception as exc:
            monitor.record("TTS_ERROR", type(exc).__name__, str(exc)[:250])
            raise HTTPException(502, f"Không phát được TTS: {type(exc).__name__}: {str(exc)[:250]}") from exc
    finally:
        voice_lock.release()

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
