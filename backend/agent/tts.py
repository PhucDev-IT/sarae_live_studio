"""Operator-approved Vietnamese TTS played through the 'Sarae Voice' OBS Media Source on the Agent.

Same behaviour as app.ai_voice.speak, but uses the Agent's playback engine OBS connection
instead of app.live (which owns a VPS-side OBS state and monitor thread).
"""
import asyncio
from threading import Lock
from uuid import uuid4

from pydantic import BaseModel, Field

from app.database import DATA_DIR

VOICE_DIR = DATA_DIR / "voice"
_lock = Lock()


class SpeakBody(BaseModel):
    # Mirrors app.ai_voice.SpeakBody (importing that module would pull in app.live).
    text: str = Field(min_length=1, max_length=450)
    voice: str = Field(default="vi-VN-HoaiMyNeural", pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")
    obs_source: str = Field(default="Sarae Voice", min_length=1, max_length=100)


def speak(engine, payload: dict):
    body = SpeakBody.model_validate(payload)
    if not _lock.acquire(blocking=False):
        raise ValueError("TTS đang xử lý một yêu cầu khác")
    try:
        import edge_tts
        VOICE_DIR.mkdir(parents=True, exist_ok=True)
        output = (VOICE_DIR / f"voice_{uuid4().hex}.mp3").resolve()

        async def generate():
            await edge_tts.Communicate(body.text, body.voice).save(str(output))

        asyncio.run(generate())
        with engine.lock:
            client = engine.client()
            try:
                # The source must already exist in OBS; never touch video sources.
                client.get_input_settings(body.obs_source)
                client.set_input_settings(body.obs_source, {
                    "local_file": str(output), "is_local_file": True,
                    "looping": False, "close_when_inactive": False}, overlay=True)
                client.set_input_mute(body.obs_source, False)
                client.trigger_media_input_action(body.obs_source, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
            finally:
                client.base_client.ws.close()
        return {"status": "sent_to_obs", "source": body.obs_source, "voice": body.voice, "text": body.text}
    finally:
        _lock.release()
