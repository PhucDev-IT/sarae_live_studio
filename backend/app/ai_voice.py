"""Operator-approved Vietnamese TTS played through a separate OBS Media Source.

Create a Media Source named 'Sarae Voice' in OBS and enable its audio in the mixer.
This is not automatic TikTok replying and does not touch the video media input.
"""
import asyncio
from uuid import uuid4
from threading import RLock

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import DATA_DIR
from app.live import state
from app.comment_processing import monitor

router = APIRouter(prefix="/api/ai/voice", tags=["AI LIVE Voice"])
VOICE_DIR = DATA_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)
VOICE_SOURCE = "Sarae Voice"
VOICE = "vi-VN-HoaiMyNeural"
voice_lock = RLock()

class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=450)
    voice: str = Field(default=VOICE, pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")
    obs_source: str = Field(default=VOICE_SOURCE, min_length=1, max_length=100)

@router.post("/speak")
def speak(body: SpeakBody):
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
