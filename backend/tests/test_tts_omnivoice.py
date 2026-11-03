"""Phase 3: Sarae Backend <-> G-OmniVoice voice server, with a fake voice server (tests/fake_voice_server.py).

Voice profiles (storage, revisions, owner isolation, sync, offline), preview, utterance/job/command lifecycle,
idempotency, reconciliation, LIVE end and leases, readiness, fallback policies, old Agents, relay and tokens.
No GPU, no real voice server, no TikTok.
"""
import io
import json
import logging
import threading
import time
import wave
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app import tts_orchestrator, tts_server, voice_profiles
from app.config import settings
from app import storage_providers
from fake_voice_server import TOKEN, FakeVoiceServer
from memory_storage import MemoryStorageProvider, MemoryStore
from support import SaasTestCase


def make_wav(seconds: float = 4.0, rate: int = 24000, tone: int = 3000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frame = tone.to_bytes(2, "little", signed=True)
        w.writeframes(frame * int(rate * seconds))
    return buf.getvalue()


class OmniVoiceTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        MemoryStorageProvider.store = MemoryStore()
        storage_providers.register("MEMORY", MemoryStorageProvider)
        self.settings_patch = patch.multiple(settings, sarae_tts_server_url="http://tts.test",
                                             sarae_tts_server_token=TOKEN, sarae_tts_agent_url="",
                                             sarae_storage_provider="MEMORY", sarae_tts_relay_max_bytes=32 * 1024 * 1024)
        self.settings_patch.start()
        self.fake = FakeVoiceServer()
        tts_server.set_transport(self.fake.transport())
        tts_orchestrator._leased.clear()
        tts_orchestrator._tokens.clear()
        voice_profiles._verified.clear()

    def tearDown(self):
        tts_server.set_transport(None)
        self.settings_patch.stop()
        storage_providers.unregister("MEMORY")
        super().tearDown()

    # --- helpers --------------------------------------------------------------------------------------
    def profile(self, headers=None, wav=None, name="Giọng shop", transcript=None):
        data = {"name": name, **({"transcript": transcript} if transcript else {})}
        r = self.client.post("/api/ai/voice/profiles", headers=headers or self.A,
                             files={"wav": ("ref.wav", wav or make_wav(), "audio/wav")}, data=data)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def select(self, profile_id, headers=None, fallback=None):
        body = {"provider": "omnivoice", "model": "demo_default", "voice": profile_id, "rate": 0, "settings": {}}
        if fallback:
            body["fallback"] = fallback
        r = self.client.put("/api/ai/voice/config", headers=headers or self.A, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def agent(self, owner=None, capable=True, hostname="PC"):
        agent = self.activate_agent(owner or self.owner_a, hostname)
        voice = {"queue": 0, **({"capabilities": {"providers": ["edge", "omnivoice"], "tts_job": 1,
                                                  "audio_path": ["relay", "direct"]}} if capable else {})}
        r = self.client.post("/api/agents/heartbeat", headers={"X-Sarae-Agent-Key": agent["agent_credential"]},
                             json={"agent_id": agent["agent_id"], "hostname": hostname, "voice": voice})
        self.assertEqual(r.status_code, 200, r.text)
        return agent

    def commands(self, agent_id):
        from app.cloud_agents import _db
        with _db() as db:
            return [{"kind": r["kind"], "payload": json.loads(r["payload"]), "id": r["command_id"]}
                    for r in db.execute("SELECT kind,payload,command_id FROM agent_commands WHERE agent_id=? ORDER BY created_at",
                                        (agent_id,))]

    def utterances(self):
        with self.Session() as db:
            return db.query(tts_orchestrator.TtsUtterance).order_by(tts_orchestrator.TtsUtterance.created_at).all()

    def running_session(self, owner=None, agent_id=None, status="RUNNING"):
        from app.live_sessions import LiveSession
        with self.Session() as db:
            item = LiveSession(name="LIVE", status=status, owner_id=(owner or self.owner_a)["id"], agent_id=agent_id)
            db.add(item)
            db.commit()
            return item.id

    def speak(self, agent, text="Dạ có size Mờ nha chị."):
        return self.client.post("/api/ai/voice/speak", json={"agent_id": agent["agent_id"], "text": text})

    def test_phase6_preview_override_does_not_change_default_or_live(self):
        first, other = self.profile(), self.profile(name="Giọng thử")
        self.select(first["id"])
        agent = self.agent()
        self.running_session(agent_id=agent["agent_id"])
        before = self.client.get("/api/ai/voice/config").json()
        r = self.client.post("/api/ai/voice/preview", json={"text": "Dạ có size Mờ nha chị.", "voice_id": other["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/ai/voice/config").json(), before)
        self.assertEqual(self.client.post("/api/ai/voice/preview", headers=self.B,
            json={"text": "Xin chào", "voice_id": other["id"]}).status_code, 404)

    def test_phase6_running_and_pending_start_protect_used_voice(self):
        selected, unused = self.profile(), self.profile(name="Giọng chưa dùng")
        self.select(selected["id"])
        agent = self.agent()
        sid = self.running_session(agent_id=agent["agent_id"])
        for status in ("RUNNING", "DRAFT"):
            with self.Session() as db:
                from app.live_sessions import LiveSession
                row = db.get(LiveSession, sid);row.status = status;db.commit()
            r = self.client.put("/api/ai/voice/config",json={"provider":"edge","model":"edge-neural","voice":"vi-VN-HoaiMyNeural","rate":5,"settings":{}})
            self.assertEqual(r.status_code,409,r.text)
            r = self.client.patch("/api/ai/voice/profiles/"+selected["id"],json={"transcript":"Nội dung mới"})
            self.assertEqual(r.status_code,409,r.text)
            r = self.client.put("/api/ai/voice/profiles/"+selected["id"]+"/wav", files={"wav":("new.wav",make_wav(tone=2000),"audio/wav")})
            self.assertEqual(r.status_code,409,r.text)
            self.assertEqual(self.client.patch("/api/ai/voice/profiles/"+unused["id"],json={"transcript":"Giọng độc lập"}).status_code,200)
            self.assertEqual(self.client.patch("/api/ai/voice/profiles/"+selected["id"],json={"name":"Đổi tên an toàn"}).status_code,200)

    def test_phase6_owner_lock_serializes_concurrent_start_and_mutation(self):
        from app.voice_policy import owner_voice_lock
        from app.live_sessions import LiveSession
        self.select(self.profile()["id"])
        agent=self.agent()
        entered,release=threading.Event(),threading.Event()
        def pending_start():
            with owner_voice_lock(self.owner_a["id"]):
                entered.set();release.wait(5)
                with self.Session() as db:
                    db.add(LiveSession(name="Pending",owner_id=self.owner_a["id"],status="DRAFT",agent_id=agent["agent_id"]))
                    db.commit()
        starter=threading.Thread(target=pending_start);starter.start();self.assertTrue(entered.wait(2))
        results=[]
        def mutate():
            results.append(self.client.put("/api/ai/voice/config",json={"provider":"edge","model":"edge-neural","voice":"vi-VN-HoaiMyNeural","rate":5,"settings":{}}))
        writer=threading.Thread(target=mutate);writer.start();time.sleep(.05);self.assertFalse(results)
        release.set();starter.join(5);writer.join(5)
        self.assertEqual(results[0].status_code,409)

    def test_phase6_clear_waits_for_inflight_job_publication(self):
        self.file_database()
        self.select(self.profile()["id"])
        agent=self.agent()
        entered,release=threading.Event(),threading.Event()
        create=tts_server.create_job
        def slow_create(**kwargs):
            entered.set();release.wait(5);return create(**kwargs)
        result=[]
        def speak():
            result.append(tts_orchestrator.speak(self.owner_a["id"],agent["agent_id"],text="Câu đang tạo nha.",
                intro=None,ttl_seconds=30,duck=False,session_id=None,reply_id=None,source="manual"))
        with patch.object(tts_server,"create_job",slow_create):
            speaker=threading.Thread(target=speak);speaker.start();self.assertTrue(entered.wait(2))
            cleared=[]
            clearer=threading.Thread(target=lambda:cleared.append(tts_orchestrator.cancel_agent(self.owner_a["id"],agent["agent_id"])))
            clearer.start();time.sleep(.05);self.assertFalse(cleared)
            release.set();speaker.join(5);clearer.join(5)
        self.assertEqual(len(result),1)
        self.assertEqual(self.utterances()[0].state,"CANCELLED")

    # --- voice profiles ---------------------------------------------------------------------------------
    def test_phase4_playback_events_authorization_and_terminal_state(self):
        self.select(self.profile()["id"])
        agent = self.agent()
        self.assertEqual(self.speak(agent).status_code, 200)
        u = self.utterances()[0]
        def send(seq, status, job=None):
            return self.client.post("/api/agents/events", headers={"X-Sarae-Agent-Key": agent["agent_credential"]},
                json={"agent_id": agent["agent_id"], "events": [{"seq": seq, "type": "tts_playback",
                    "detail": {"utterance_id": u.id, "job_id": job or u.tts_job_id, "status": status}}]})
        self.assertEqual(send(1, "played", "wrong_job").status_code, 200)
        self.assertEqual(self.utterances()[0].state, "SENT")
        self.assertEqual(send(2, "playing").status_code, 200)
        self.assertEqual(self.utterances()[0].state, "PLAYING")
        send(3, "played")
        send(3, "played")
        send(4, "playing")
        self.assertEqual(self.utterances()[0].state, "PLAYED")

    def test_phase4_owner_fallback_is_carried_in_command(self):
        self.select(self.profile()["id"], fallback={"manual": "provider", "provider": {
            "provider": "edge", "voice": "vi-VN-HoaiMyNeural", "model": "edge-neural", "rate": 5}})
        agent = self.agent()
        self.assertEqual(self.speak(agent).status_code, 200)
        policy = self.commands(agent["agent_id"])[0]["payload"]["fallback"]
        self.assertEqual(policy["mode"], "provider")
        self.assertEqual(policy["provider"]["rate"], 5)

    def test_profile_crud_storage_and_owner_isolation(self):
        wav = make_wav()
        p = self.profile(wav=wav)
        self.assertEqual(p["revision"], 1)
        self.assertEqual(p["sync"]["state"], "SYNCED")
        self.assertTrue(p["sync"]["up_to_date"])
        self.assertEqual(p["transcript"]["source"], "asr")
        self.assertEqual(p["transcript"]["asr"], self.fake.asr_text)          # Whisper text kept by the Backend
        self.assertIn(wav, MemoryStorageProvider.store.objects.values())     # WAV in storage, not in SQLite
        self.assertNotIn("transcript", self.fake.puts()[0])                   # no transcript sent: Whisper decides
        self.assertEqual(self.fake.voices[p["id"]]["owner_ref"], self.owner_a["id"])
        self.assertEqual(self.client.get(f"/api/ai/voice/profiles/{p['id']}/wav").content, wav)
        self.assertEqual(len(self.client.get("/api/ai/voice/profiles").json()["profiles"]), 1)
        self.assertEqual(self.client.get("/api/ai/voice/profiles", headers=self.B).json()["profiles"], [])
        for method, suffix in (("get", ""), ("patch", ""), ("delete", ""), ("get", "/wav"), ("post", "/sync")):
            kwargs = {"json": {"name": "x"}} if method == "patch" else {}
            r = getattr(self.client, method)(f"/api/ai/voice/profiles/{p['id']}{suffix}", headers=self.B, **kwargs)
            self.assertEqual(r.status_code, 404, (method, suffix))
        self.assertEqual(self.client.delete(f"/api/ai/voice/profiles/{p['id']}").status_code, 204)
        self.assertNotIn(wav, MemoryStorageProvider.store.objects.values())
        self.assertNotIn(p["id"], self.fake.voices)

    def test_invalid_reference_rejected(self):
        for wav, status in ((b"not a wav", 422), (make_wav(0.5), 422), (make_wav(25, rate=8000), 422),
                            (b"RIFF" + b"\x00" * (6 * 1024 * 1024), 413)):
            r = self.client.post("/api/ai/voice/profiles", files={"wav": ("x.wav", wav, "audio/wav")},
                                 data={"name": "x"})
            self.assertEqual(r.status_code, status)
        self.assertEqual(MemoryStorageProvider.store.objects, {})

    def test_transcript_and_wav_changes_bump_revision_and_resync(self):
        p = self.profile()
        r = self.client.patch(f"/api/ai/voice/profiles/{p['id']}", json={"transcript": "Xin chào cả nhà."}).json()
        self.assertEqual((r["revision"], r["transcript"]["source"], r["sync"]["up_to_date"]), (2, "manual", True))
        self.assertEqual(self.fake.puts()[-1]["transcript"], "Xin chào cả nhà.".encode())
        r = self.client.patch(f"/api/ai/voice/profiles/{p['id']}", json={"transcript": None}).json()
        self.assertEqual((r["revision"], r["transcript"]["source"]), (3, "asr"))
        self.assertNotIn("transcript", self.fake.puts()[-1])
        same = self.client.patch(f"/api/ai/voice/profiles/{p['id']}", json={"name": "Đổi tên"}).json()
        self.assertEqual(same["revision"], 3)
        objects_before = set(MemoryStorageProvider.store.objects)
        new = self.client.put(f"/api/ai/voice/profiles/{p['id']}/wav",
                              files={"wav": ("n.wav", make_wav(5, tone=1500), "audio/wav")}).json()
        self.assertEqual(new["revision"], 4)
        self.assertEqual(new["transcript"]["source"], "asr")
        self.assertFalse(objects_before & set(MemoryStorageProvider.store.objects))   # old object removed

    def test_offline_profile_stays_readable_then_syncs_when_back(self):
        self.fake.online = False
        p = self.profile()
        self.assertEqual(p["sync"]["state"], "PENDING")
        self.assertEqual(self.client.get(f"/api/ai/voice/profiles/{p['id']}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/ai/voice/profiles/{p['id']}/sync").headers["x-sarae-error"],
                         "TTS_BACKEND_UNREACHABLE")
        self.fake.online = True
        tts_server.set_transport(self.fake.transport())          # clears the cached health
        tts_orchestrator.tick()
        self.assertTrue(self.client.get(f"/api/ai/voice/profiles/{p['id']}").json()["sync"]["up_to_date"])

    def test_new_gpu_machine_gets_the_profile_again(self):
        p = self.profile()
        agent = self.agent()
        self.select(p["id"])
        self.fake.voices.clear()                                  # voice server replaced or wiped
        voice_profiles._verified.clear()
        r = self.client.post("/api/ai/voice/readiness", json={"agent_id": agent["agent_id"]}).json()
        self.assertIn(p["id"], self.fake.voices)
        self.assertEqual(len(self.fake.puts()), 2)
        self.assertEqual(r["warmup"]["cache"], "ram")
        self.assertTrue(r["ready"], r)

    def test_concurrent_sync_uploads_once(self):
        self.file_database()
        self.fake.online = False
        p = self.profile()
        self.fake.online = True
        tts_server.set_transport(self.fake.transport())
        before = len(self.fake.puts())                               # attempts made while offline
        def sync():
            with voice_profiles.SessionLocal() as db:
                voice_profiles.ensure_synced(db, db.get(voice_profiles.VoiceProfile, p["id"]))
        threads = [threading.Thread(target=sync) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.fake.puts()) - before, 1)

    # --- configuration and preview ------------------------------------------------------------------------
    def test_voice_config_validation(self):
        mine = self.profile()
        theirs = self.profile(headers=self.B)
        bad = {"provider": "omnivoice", "model": "demo_default", "voice": theirs["id"], "rate": 0, "settings": {}}
        self.assertEqual(self.client.put("/api/ai/voice/config", json=bad).status_code, 404)
        for change in ({"rate": 5}, {"model": "fast"}, {"settings": {"speed": 1.1}}, {"voice": "vi-VN-HoaiMyNeural"}):
            self.assertEqual(self.client.put("/api/ai/voice/config", json={**bad, "voice": mine["id"], **change}).status_code, 422)
        self.assertEqual(self.client.put("/api/ai/voice/config", json={**bad, "voice": mine["id"],
                                         "fallback": {"live_reply": "provider"}}).status_code, 422)
        cfg = self.select(mine["id"], fallback={"live_reply": "provider", "provider": {
            "provider": "edge", "model": "edge-neural", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "settings": {}}})
        self.assertEqual(cfg["fallback"]["provider"]["provider"], "edge")
        self.assertTrue(cfg["omnivoice_configured"])
        self.assertEqual(self.client.delete(f"/api/ai/voice/profiles/{mine['id']}").status_code, 409)   # in use
        default = self.client.get("/api/ai/voice/config", headers=self.B).json()
        self.assertEqual((default["provider"], default["fallback"]["live_reply"]), ("edge", "none"))

    def test_preview_returns_wav_of_selected_profile_without_fallback(self):
        p = self.profile()
        self.select(p["id"], fallback={"manual": "provider", "provider": {
            "provider": "edge", "model": "edge-neural", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "settings": {}}})
        r = self.client.post("/api/ai/voice/preview", json={"text": "Xin chào cả nhà"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "audio/wav")
        self.assertEqual(r.content, self.fake.audio)
        self.assertEqual(r.headers["x-voice-revision"], "1")
        self.assertEqual(json.loads(self.fake.requests[-1].content)["voice_id"], p["id"])
        self.fake.online = False
        tts_server.set_transport(self.fake.transport())
        r = self.client.post("/api/ai/voice/preview", json={"text": "Xin chào"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.headers["x-sarae-error"], "TTS_BACKEND_UNREACHABLE")
        self.assertEqual(self.client.post("/api/ai/voice/preview", json={"text": "a" * 451}).status_code, 422)

    def test_edge_never_touches_the_voice_server(self):
        agent = self.agent()
        r = self.speak(agent)
        self.assertEqual(r.status_code, 200, r.text)
        payload = self.commands(agent["agent_id"])[-1]["payload"]
        self.assertEqual(payload["provider"], "edge")
        self.assertNotIn("tts", payload)
        self.assertEqual(self.fake.requests, [])

    # --- old Agents ---------------------------------------------------------------------------------------
    def test_old_agent_never_receives_omnivoice_payload(self):
        p = self.profile()
        self.select(p["id"])
        old = self.agent(capable=False)
        r = self.speak(old)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.headers["x-sarae-error"], "AGENT_NO_OMNIVOICE")
        self.assertEqual(self.commands(old["agent_id"]), [])
        self.assertEqual(self.fake.jobs, {})
        from app.cloud_agents import create_command
        payload = {"text": "hi", "provider": "omnivoice", "model": "demo_default", "voice": p["id"], "rate": 0,
                   "settings": {}, "tts": {"utterance_id": "a" * 32, "job_id": "jobid123", "voice_rev": 1,
                                           "deadline_at": "2030-01-01T00:00:00+00:00", "audio_path": "relay"}}
        with self.assertRaises(HTTPException) as ctx:
            create_command(old["agent_id"], self.owner_a["id"], "tts_speak", payload)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.commands(old["agent_id"]), [])

    def test_omnivoice_payload_schema(self):
        from app.cloud_agents import validate_payload
        base = {"text": "hi", "provider": "omnivoice", "model": "demo_default", "voice": "b" * 32, "rate": 0,
                "settings": {}, "tts": {"utterance_id": "a" * 32, "job_id": "jobid123", "voice_rev": 2,
                                        "deadline_at": "2030-01-01T00:00:00+00:00", "audio_path": "auto"}}
        self.assertEqual(validate_payload("tts_speak", base)["tts"]["job_id"], "jobid123")
        for bad in ({"tts": None}, {"rate": 5}, {"settings": {"speed": 1}}, {"intro": "Lan ơi,"},
                    {"tts": {**base["tts"], "job_id": "../x"}}, {"tts": {**base["tts"], "audio_path": "http://x"}},
                    {"voice": "vi-VN-HoaiMyNeural"}):
            with self.assertRaises(HTTPException):
                validate_payload("tts_speak", {**base, **bad})
        prep = {"texts": ["a", "b"], "provider": "omnivoice", "model": "demo_default", "voice": "b" * 32,
                "settings": {}, "tts_jobs": [{"utterance_id": "c" * 32, "job_id": "job1abc"}]}
        with self.assertRaises(HTTPException):
            validate_payload("tts_prepare", prep)
        prep["tts_jobs"].append({"utterance_id": "d" * 32, "job_id": "job2abc"})
        self.assertEqual(len(validate_payload("tts_prepare", prep)["tts_jobs"]), 2)

    # --- utterance lifecycle --------------------------------------------------------------------------------
    def test_speak_creates_job_and_command_without_token(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        r = self.speak(agent)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("readtok", r.text)
        cmd = self.commands(agent["agent_id"])[-1]
        self.assertEqual(cmd["kind"], "tts_speak")
        self.assertEqual(cmd["payload"]["provider"], "omnivoice")
        self.assertNotIn("readtok", json.dumps(cmd["payload"]))
        [u] = self.utterances()
        self.assertEqual((u.state, u.command_id, u.voice_rev, u.priority), ("SENT", cmd["id"], 1, "live"))
        self.assertEqual(cmd["payload"]["tts"]["utterance_id"], u.id)
        self.assertEqual(cmd["payload"]["tts"]["audio_path"], "relay")
        job_request = next(rq for rq in self.fake.requests if rq.url.path == "/v1/tts/jobs")
        self.assertEqual(job_request.headers["idempotency-key"], u.id)
        self.assertIn(f"agent:{agent['agent_id']}", self.fake.jobs[u.tts_job_id]["tags"])
        listed = self.client.get("/api/ai/voice/utterances").text
        self.assertIn(u.id, listed)
        self.assertNotIn("readtok", listed)
        self.assertEqual(self.client.get("/api/ai/voice/utterances", headers=self.B).json()["utterances"], [])

    def test_job_creation_retry_reuses_the_same_job(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        self.fake.drop_next_create = 1                       # first attempt never reaches the server
        self.assertEqual(self.speak(agent).status_code, 200)
        self.assertEqual(len(self.fake.jobs), 1)
        keys = [rq.headers["idempotency-key"] for rq in self.fake.requests if rq.url.path == "/v1/tts/jobs"]
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 1)
        # A replay of the same key on the voice server returns the same job, never a second inference.
        u = self.utterances()[0]
        again = tts_server.create_job(idempotency_key=u.id, voice_id=p["id"], text="x", priority="live",
                                      deadline_at=datetime.now(timezone.utc).isoformat(), reader_agent_id=agent["agent_id"],
                                      owner_ref=self.owner_a["id"], tags=[])
        self.assertEqual(again["job_id"], u.tts_job_id)
        self.assertEqual(len(self.fake.jobs), 1)

    def test_command_failure_after_job_cancels_the_job(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        with patch("app.cloud_agents.create_command", side_effect=HTTPException(409, "Agent đang offline")):
            self.assertEqual(self.speak(agent).status_code, 409)
        [u] = self.utterances()
        self.assertEqual((u.state, u.error_code), ("ABORTED", "COMMAND_FAILED"))
        self.assertEqual(self.fake.jobs[u.tts_job_id]["state"], "cancelled")

    def test_voice_server_refusal_fails_utterance_and_no_command(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        self.fake.fail_next_create = (429, "TTS_BUSY")
        r = self.speak(agent)
        self.assertEqual(r.headers["x-sarae-error"], "TTS_BUSY")
        self.assertEqual(self.utterances()[0].state, "FAILED")
        self.assertEqual(self.commands(agent["agent_id"]), [])

    def test_restart_reconciliation_orphans_never_resends(self):
        p = self.profile()
        agent = self.agent()
        job = tts_server.create_job(idempotency_key="f" * 32, voice_id=p["id"], text="x", priority="live",
                                    deadline_at=datetime.now(timezone.utc).isoformat(), reader_agent_id=agent["agent_id"],
                                    owner_ref=self.owner_a["id"], tags=[])
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        with self.Session() as db:
            db.add(tts_orchestrator.TtsUtterance(id="f" * 32, owner_id=self.owner_a["id"], agent_id=agent["agent_id"],
                   source="live_reply", part="body", voice_id=p["id"], voice_rev=1, text_sha256="0" * 64, chars=1,
                   priority="live", deadline_at=old, state="JOB_CREATED", tts_job_id=job["job_id"],
                   created_at=old, updated_at=old))
            db.commit()
        self.assertEqual(tts_orchestrator.reconcile(), 1)
        u = self.utterances()[0]
        self.assertEqual(u.state, "ORPHANED")
        self.assertEqual(self.fake.jobs[job["job_id"]]["state"], "cancelled")
        self.assertEqual(self.commands(agent["agent_id"]), [])

    def file_database(self):
        """A real SQLite file (one connection per session), as in production: the shared in-memory connection of
        SaasTestCase is not safe for several threads at once."""
        from pathlib import Path
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        engine = create_engine(f"sqlite:///{(Path(self.tmp.name) / 'concurrency.db').as_posix()}",
                               connect_args={"check_same_thread": False, "timeout": 30})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        for module in ("app.voice_profiles", "app.tts_orchestrator", "app.live_sessions"):
            target = patch(f"{module}.SessionLocal", Session)
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(engine.dispose)
        self.Session = Session

    def test_concurrent_speaks_are_distinct_utterances(self):
        self.file_database()
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        results = []
        def go(i):
            results.append(tts_orchestrator.speak(self.owner_a["id"], agent["agent_id"], text=f"Câu {i} nha.",
                                                  intro=None, ttl_seconds=30, duck=True, session_id=None,
                                                  reply_id=i, source="live_reply"))
        threads = [threading.Thread(target=go, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 5)
        self.assertEqual(len(self.fake.jobs), 5)
        self.assertEqual(len({c["id"] for c in self.commands(agent["agent_id"])}), 5)
        self.assertEqual(len({u.tts_job_id for u in self.utterances()}), 5)

    # --- LIVE: director, end of session, leases, readiness ----------------------------------------------------
    def director_reply(self, session_id):
        return {"id": 7, "session_id": session_id, "say": "Dạ mẫu này còn màu đen nha chị", "text": "Còn màu đen ạ",
                "product_id": 1, "product_name": "Áo", "intent": "COLOR", "slots": {}, "comment": None,
                "voice": "queued", "_product": {"price": 200000, "original_price": None, "description": ""}}

    def test_live_reply_and_fallback_policies(self):
        from app import live_reply_director as director
        from app.ai_live_batches import live_key, states
        p = self.profile()
        agent = self.agent()
        self.select(p["id"])
        sid = self.running_session(agent_id=agent["agent_id"])
        st = states(live_key(self.owner_a["id"], agent["agent_id"]))
        self.addCleanup(states.discard, st.key)
        options = director.ReplyOptions()
        edge = {"provider": "edge", "model": "edge-neural", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "settings": {}}

        self.select(p["id"])
        reply = self.director_reply(sid)
        director.send_voice(st, reply, options, 1, ttl=20)
        self.assertEqual(reply["voice"], "sent")
        self.assertEqual(self.commands(agent["agent_id"])[-1]["payload"]["provider"], "omnivoice")

        self.fake.online = False
        tts_server.set_transport(self.fake.transport())
        expected = {"none": "failed", "comment_only": "skipped", "hold_for_operator": "review"}
        for mode, state in expected.items():
            self.select(p["id"], fallback={"live_reply": mode})
            reply = self.director_reply(sid)
            director.send_voice(st, reply, options, 1, ttl=20)
            self.assertEqual(reply["voice"], state, mode)
        self.assertEqual(reply["voice"], "review")
        before = len(self.commands(agent["agent_id"]))
        self.select(p["id"], fallback={"live_reply": "provider", "provider": edge})
        reply = self.director_reply(sid)
        director.send_voice(st, reply, options, 1, ttl=20)
        self.assertEqual(reply["voice"], "sent")
        self.assertEqual(self.commands(agent["agent_id"])[-1]["payload"]["provider"], "edge")
        self.assertEqual(len(self.commands(agent["agent_id"])), before + 1)

        # Ended session: refused and never answered by another voice, even with provider fallback.
        from app.live_sessions import LiveSession
        with self.Session() as db:
            db.get(LiveSession, sid).status = "ENDED"
            db.commit()
        self.fake.online = True
        tts_server.set_transport(self.fake.transport())
        reply = self.director_reply(sid)
        director.send_voice(st, reply, options, 1, ttl=20)
        self.assertEqual(reply["voice"], "failed")
        self.assertIn("SESSION_ENDED", reply["note"])
        self.assertEqual(len(self.commands(agent["agent_id"])), before + 1)

    def test_live_end_cancels_jobs_and_releases_lease(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        sid = self.running_session(agent_id=agent["agent_id"])
        tts_orchestrator.sync_leases()
        self.assertIn((self.owner_a["id"], str(sid)), self.fake.leases)
        tts_orchestrator.speak(self.owner_a["id"], agent["agent_id"], text="Câu một.", intro="Lan ơi,", ttl_seconds=30,
                               duck=True, session_id=sid, reply_id=1, source="live_reply")
        self.assertEqual(tts_orchestrator.session_ended(self.owner_a["id"], sid), 2)
        self.assertTrue(all(u.state == "CANCELLED" for u in self.utterances()))
        self.assertTrue(all(j["state"] == "cancelled" for j in self.fake.jobs.values()))
        self.assertNotIn((self.owner_a["id"], str(sid)), self.fake.leases)

    def test_leases_follow_running_sessions_of_omnivoice_owners(self):
        p = self.profile()
        self.select(p["id"])
        a1, a2 = self.agent(hostname="PC1"), self.agent(hostname="PC2")
        s1 = self.running_session(agent_id=a1["agent_id"])
        s2 = self.running_session(agent_id=a2["agent_id"])
        self.running_session(owner=self.owner_b)                       # owner B speaks with Edge: no lease
        draft = self.running_session(status="DRAFT")
        tts_orchestrator.sync_leases()
        self.assertEqual(set(self.fake.leases), {(self.owner_a["id"], str(s1)), (self.owner_a["id"], str(s2))})
        from app.live_sessions import LiveSession
        with self.Session() as db:
            db.get(LiveSession, s1).status = "ENDED"
            db.commit()
        tts_orchestrator.sync_leases()
        self.assertEqual(set(self.fake.leases), {(self.owner_a["id"], str(s2))})
        self.assertNotIn(str(draft), {s for _, s in self.fake.leases})
        self.fake.online = False                                       # Backend cannot renew: the voice server
        tts_orchestrator.sync_leases()                                 # drops the lease after its TTL by itself
        self.assertIn((self.owner_a["id"], s2), tts_orchestrator._leased)

    def test_readiness_reports_without_taking_a_lease(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        ok = self.client.post("/api/ai/voice/readiness", json={"agent_id": agent["agent_id"]}).json()
        self.assertTrue(ok["ready"], ok)
        self.assertEqual(self.fake.leases, {})
        self.fake.running = {"class": "long", "elapsed_s": 3.0, "estimated_remaining_s": 29.6}
        busy = self.client.post("/api/ai/voice/readiness", json={"agent_id": agent["agent_id"]}).json()
        self.assertFalse(busy["ready"])
        self.assertIn("LONG_JOB_RUNNING", busy["warnings"])
        self.assertIn("ước tính", busy["gpu"]["note"])
        old = self.agent(capable=False, hostname="OLD")
        self.assertIn("AGENT_NO_OMNIVOICE", tts_orchestrator.readiness(self.owner_a["id"], old["agent_id"])["warnings"])
        self.fake.online = False
        tts_server.set_transport(self.fake.transport())
        off = tts_orchestrator.readiness(self.owner_a["id"], agent["agent_id"])
        self.assertFalse(off["ready"])
        self.assertIn("TTS_BACKEND_UNREACHABLE", off["warnings"])
        self.assertEqual(tts_orchestrator.readiness(self.owner_b["id"], None)["applicable"], False)

    def test_stop_voice_cancels_omnivoice_jobs(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        self.speak(agent)
        r = self.client.post("/api/ai/live/voice/stop", params={"agent_id": agent["agent_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.utterances()[0].state, "CANCELLED")
        self.assertTrue(all(j["state"] == "cancelled" for j in self.fake.jobs.values()))
        self.assertEqual(self.commands(agent["agent_id"])[-1]["kind"], "tts_clear")

    def test_prepare_uses_prepare_priority_and_aligned_jobs(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        done = tts_orchestrator.prepare(self.owner_a["id"], agent["agent_id"], ["Câu một.", "Câu hai."], None)
        self.assertEqual(done["accepted"], 2)
        cmd = self.commands(agent["agent_id"])[-1]
        self.assertEqual(cmd["kind"], "tts_prepare")
        self.assertEqual(len(cmd["payload"]["tts_jobs"]), 2)
        self.assertTrue(all(j["priority"] == "prepare" for j in self.fake.jobs.values()))
        old = self.agent(capable=False, hostname="OLD")
        with self.assertRaises(tts_orchestrator.SpeakRefused):
            tts_orchestrator.prepare(self.owner_a["id"], old["agent_id"], ["Câu ba."], None)

    # --- relay and tokens -------------------------------------------------------------------------------
    def relay(self, agent, utterance_id, **params):
        return self.client.get(f"/api/agents/tts/audio/{utterance_id}", params={"agent_id": agent["agent_id"], **params},
                               headers={"X-Sarae-Agent-Key": agent["agent_credential"]})

    def test_relay_authorization_and_upstream_errors(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        self.speak(agent)
        u = self.utterances()[0]
        r = self.relay(agent, u.id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, self.fake.audio)
        self.assertEqual(r.headers["content-type"], "audio/wav")
        audio_req = self.fake.requests[-1]
        self.assertEqual(audio_req.url.path, f"/v1/tts/jobs/{u.tts_job_id}/audio")
        self.assertNotIn(TOKEN, r.text)
        other = self.agent(hostname="PC2")                        # same owner, another Agent
        self.assertEqual(self.relay(other, u.id).status_code, 404)
        foreign = self.agent(owner=self.owner_b, hostname="B")
        self.assertEqual(self.relay(foreign, u.id).status_code, 404)
        self.assertEqual(self.client.get(f"/api/agents/tts/audio/{u.id}", params={"agent_id": agent["agent_id"]}).status_code, 401)
        self.assertEqual(self.relay(agent, "0" * 32).status_code, 404)
        for mode, status, code in (("pending", 202, None), ("expired", 410, "JOB_EXPIRED"), ("failed", 409, "JOB_FAILED"),
                                   ("redirect", 502, "TTS_REDIRECT_REFUSED")):
            self.fake.audio_mode = mode
            r = self.relay(agent, u.id)
            self.assertEqual(r.status_code, status, mode)
            if code:
                self.assertEqual(r.headers["x-sarae-error"], code)
        self.assertFalse(any(rq.url.host != "tts.test" for rq in self.fake.requests))       # redirect never followed
        self.fake.audio_mode = "done"
        self.fake.online = False
        self.assertEqual(self.relay(agent, u.id).headers["x-sarae-error"], "TTS_BACKEND_UNREACHABLE")
        self.fake.online = True
        with patch.object(settings, "sarae_tts_relay_max_bytes", 100):
            r = self.relay(agent, u.id)                                # announced length above the cap
            self.assertEqual((r.status_code, r.headers["x-sarae-error"]), (502, "AUDIO_TOO_LARGE"))
        tts_orchestrator.cancel_agent(self.owner_a["id"], agent["agent_id"])
        self.assertEqual(self.relay(agent, u.id).status_code, 410)

    def test_relay_refuses_expired_utterance(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        self.speak(agent)
        with self.Session() as db:
            u = db.query(tts_orchestrator.TtsUtterance).one()
            u.deadline_at = datetime.now(timezone.utc) - timedelta(minutes=5)
            db.commit()
            uid = u.id
        r = self.relay(agent, uid)
        self.assertEqual((r.status_code, r.headers["x-sarae-error"]), (410, "UTTERANCE_EXPIRED"))
        self.assertEqual(len(self.fake.jobs), 1)                                 # never re-created with a new deadline

    def test_direct_token_only_for_own_utterance_and_never_in_web_responses(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        spoken = self.speak(agent).text
        u = self.utterances()[0]
        path = f"/api/agents/tts/audio/{u.id}/token"
        headers = {"X-Sarae-Agent-Key": agent["agent_credential"]}
        r = self.client.post(path, params={"agent_id": agent["agent_id"]}, headers=headers)
        self.assertEqual((r.status_code, r.headers["x-sarae-error"]), (404, "USE_RELAY"))     # no direct URL set
        with patch.object(settings, "sarae_tts_agent_url", "http://10.0.0.5:8780"):
            r = self.client.post(path, params={"agent_id": agent["agent_id"]}, headers=headers)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["read_token"], f"readtok.{u.tts_job_id}.sig")
            self.assertEqual(r.headers["cache-control"], "no-store")
            other = self.agent(hostname="PC2")
            self.assertEqual(self.client.post(path, params={"agent_id": other["agent_id"]},
                                              headers={"X-Sarae-Agent-Key": other["agent_credential"]}).status_code, 404)
        for text in (spoken, self.client.get("/api/ai/voice/utterances").text, self.client.get("/api/ai/voice/config").text,
                     self.client.get(f"/api/agents/{agent['agent_id']}/commands").text):
            self.assertNotIn("readtok", text)
            self.assertNotIn(TOKEN, text)

    def test_logs_never_contain_tokens(self):
        p = self.profile()
        self.select(p["id"])
        agent = self.agent()
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("app").debug("start")
            self.fake.drop_next_create = 1
            self.speak(agent)
            self.relay(agent, self.utterances()[0].id)
        text = "\n".join(logs.output)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("readtok", text)
