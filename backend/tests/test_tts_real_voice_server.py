"""Optional: Sarae Backend against a REAL voice-server (G-OmniVoice on the GPU). Skipped unless configured:

    SARAE_TTS_REAL_URL=http://127.0.0.1:8783  SARAE_TTS_REAL_TOKEN=<orchestrator token>
    SARAE_TTS_REAL_REF=C:\\path\\voice_ref_pause_7s_test.wav
Covers profile upload with Whisper, preview, a LIVE-reply job + command for an Agent announcing OmniVoice, the relay
download and a LIVE lease. No TikTok, no OBS: the Agent is the test (it reads its command and downloads the audio).
"""
import io
import json
import os
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app import storage_providers, tts_orchestrator, tts_server, voice_profiles
from app.config import settings
from memory_storage import MemoryStorageProvider, MemoryStore
from support import SaasTestCase

URL, TOKEN, REF = (os.environ.get(k) for k in ("SARAE_TTS_REAL_URL", "SARAE_TTS_REAL_TOKEN", "SARAE_TTS_REAL_REF"))


@unittest.skipUnless(URL and TOKEN and REF, "set SARAE_TTS_REAL_URL, SARAE_TTS_REAL_TOKEN and SARAE_TTS_REAL_REF")
class RealVoiceServerTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        MemoryStorageProvider.store = MemoryStore()
        storage_providers.register("MEMORY", MemoryStorageProvider)
        self.p = patch.multiple(settings, sarae_tts_server_url=URL, sarae_tts_server_token=TOKEN, sarae_tts_agent_url="",
                                sarae_storage_provider="MEMORY", sarae_tts_timeout_seconds=30)
        self.p.start()
        tts_server.set_transport(None)
        tts_orchestrator._leased.clear()
        voice_profiles._verified.clear()

    def tearDown(self):
        self.p.stop()
        storage_providers.unregister("MEMORY")
        super().tearDown()

    def test_end_to_end_with_real_model(self):
        wav = Path(REF).read_bytes()
        t0 = time.perf_counter()
        r = self.client.post("/api/ai/voice/profiles", files={"wav": ("ref.wav", wav, "audio/wav")}, data={"name": "Thật"})
        self.assertEqual(r.status_code, 201, r.text)
        profile = r.json()
        print(f"\nprofile sync {time.perf_counter() - t0:.1f}s, state {profile['sync']['state']}, "
              f"asr: {profile['transcript']['asr']}")
        self.assertTrue(profile["sync"]["up_to_date"])
        self.assertTrue(profile["transcript"]["asr"])
        cfg = {"provider": "omnivoice", "model": "demo_default", "voice": profile["id"], "rate": 0, "settings": {}}
        self.assertEqual(self.client.put("/api/ai/voice/config", json=cfg).status_code, 200)

        t0 = time.perf_counter()
        preview = self.client.post("/api/ai/voice/preview", json={"text": "Dạ có size Mờ nha chị."})
        self.assertEqual(preview.status_code, 200, preview.text)
        with wave.open(io.BytesIO(preview.content)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (24000, 1, 2))
        print(f"preview {time.perf_counter() - t0:.2f}s, inference {preview.headers.get('x-inference-ms')} ms")

        agent = self.activate_agent(self.owner_a, "GPU-PC")
        caps = {"providers": ["edge", "omnivoice"], "tts_job": 1, "audio_path": ["relay"]}
        self.client.post("/api/agents/heartbeat", headers={"X-Sarae-Agent-Key": agent["agent_credential"]},
                         json={"agent_id": agent["agent_id"], "hostname": "GPU-PC", "voice": {"capabilities": caps}})
        ready = self.client.post("/api/ai/voice/readiness", json={"agent_id": agent["agent_id"]}).json()
        print("readiness", json.dumps({k: ready.get(k) for k in ("ready", "warnings", "warmup")}, ensure_ascii=False))
        self.assertTrue(ready["ready"], ready)

        t0 = time.perf_counter()
        spoken = self.client.post("/api/ai/voice/speak", json={"agent_id": agent["agent_id"],
                                                               "text": "Chị cao một mét sáu thì lấy size Mờ nha."})
        self.assertEqual(spoken.status_code, 200, spoken.text)
        orchestration = time.perf_counter() - t0
        utterance = spoken.json()["utterances"][0]["id"]
        t1 = time.perf_counter()
        audio = self.client.get(f"/api/agents/tts/audio/{utterance}", params={"agent_id": agent["agent_id"], "wait": 20},
                                headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        self.assertEqual(audio.status_code, 200, audio.text)
        with wave.open(io.BytesIO(audio.content)) as w:
            seconds = w.getnframes() / w.getframerate()
        print(f"speak orchestration {orchestration * 1000:.0f} ms (profile check + job + command); "
              f"relay download {time.perf_counter() - t1:.2f}s for {seconds:.2f}s audio")

        from app.live_sessions import LiveSession
        with self.Session() as db:
            item = LiveSession(name="LIVE", status="RUNNING", owner_id=self.owner_a["id"], agent_id=agent["agent_id"])
            db.add(item)
            db.commit()
            sid = item.id
        tts_orchestrator.sync_leases()
        adm = tts_server.admission()
        self.assertTrue(adm["live_active"])
        tts_orchestrator.session_ended(self.owner_a["id"], sid)
        self.assertFalse(tts_server.admission()["live_active"])
        tts_server.delete_voice(profile["id"])
