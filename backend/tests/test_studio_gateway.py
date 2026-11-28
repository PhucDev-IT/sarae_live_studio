"""Studio gateway (app.gateway_main, docs/HYBRID_STUDIO_SERVER.md). Run from backend/:
python -m unittest tests.test_studio_gateway   (or python -m pytest tests/test_studio_gateway.py)"""
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import identity, studio_gateway, tts_server
from app.config import settings
from app.gateway_main import app
from tools import studio_admin

TOKEN = "gateway-test-voice-token"
WAV = b"RIFF" + (2036).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 2028


class FakeVoiceServer:
    """GET /health/ready, GET /v1/voices, POST /v1/tts/synthesize of sarae-voice-server."""

    def __init__(self):
        self.online = True
        self.state = "ready"
        self.audio = WAV
        self.synth_status = 200
        self.synth_error = None
        self.voices = [{"voice_id": "linh_phuong", "name": "Linh Phương", "rev": "abc123rev",
                        "prompt": {"compat": "c1", "tokens": 172}, "transcript": "câu mẫu bí mật",
                        "ref_wav_path": "F:/voices/linh_phuong/ref.wav", "owner_ref": "owner-secret"},
                       {"voice_id": "bad id!", "name": "x"}]
        self.synth_bodies = []
        self.block = None              # threading.Event: synthesize waits on it
        self.entered = threading.Event()

    def transport(self):
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "tts.test", request.url
        if not self.online:
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/health/ready":
            return httpx.Response(200 if self.state == "ready" else 503, json={"state": self.state})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"code": "TTS_AUTH"}})
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json={"voices": self.voices})
        if request.url.path == "/v1/tts/synthesize":
            body = json.loads(request.content)
            self.synth_bodies.append(body)
            self.entered.set()
            if self.block is not None:
                self.block.wait(5)
            if self.synth_error:
                status, code = self.synth_error
                return httpx.Response(status, json={"error": {"code": code, "message": code}})
            if body["voice_id"] not in {v["voice_id"] for v in self.voices}:
                return httpx.Response(404, json={"error": {"code": "VOICE_NOT_FOUND"}})
            return httpx.Response(200, content=self.audio, headers={"content-type": "audio/wav", "X-Voice-Rev": "abc123rev"})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        folder = Path(self.tmp.name)
        self.patches = [patch.object(identity, "DB", folder / "identity.sqlite3"),
                        patch.object(studio_gateway, "USAGE_DB", folder / "studio_gateway.sqlite3"),
                        patch.object(settings, "sarae_tts_server_url", "http://tts.test"),
                        patch.object(settings, "sarae_tts_server_token", TOKEN),
                        patch.object(settings, "sarae_gateway_tts_concurrency", 1)]
        for p in self.patches:
            p.start()
        studio_gateway.reset_state()
        self.voice = FakeVoiceServer()
        tts_server.set_transport(self.voice.transport())
        self.client = TestClient(app)
        self.expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        self.owner_a = studio_admin.create_customer("a@example.test", 2, self.expiry)
        self.owner_b = studio_admin.create_customer("b@example.test", 2, None)

    def tearDown(self):
        if self.voice.block is not None:
            self.voice.block.set()
        self.client.close()
        tts_server.set_transport(None)
        for p in reversed(self.patches):
            p.stop()
        studio_gateway.reset_state()
        self.tmp.cleanup()

    def activate(self, email="a@example.test", hostname="PC-A", ip="203.0.113.1"):
        key = studio_admin.issue_key(email)
        response = self.client.post("/api/identity/activate", json={"activation_key": key, "hostname": hostname,
                                                                   "platform": "WINDOWS"},
                                    headers={"CF-Connecting-IP": ip})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def auth(device):
        return {"X-Sarae-Agent": device["agent_id"], "Authorization": "Bearer " + device["agent_credential"]}

    def assertError(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers.get("X-Sarae-Error"), code)
        self.assertIsInstance(response.json()["detail"], str)

    def speak(self, device, text="Chào cả nhà", voice_id="linh_phuong", **extra):
        return self.client.post("/api/studio/v1/tts/synthesize", headers=self.auth(device),
                                json={"voice_id": voice_id, "text": text, "preset": "live_fast", **extra})

    def usage(self, owner_id):
        return studio_gateway.usage_rows(owner_id, 1)


class AppSurfaceTests(GatewayTestCase):
    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "UP")
        self.assertEqual(response.json()["service"], "sarae-studio-gateway")

    def test_legacy_routes_and_docs_absent(self):
        self.assertEqual(self.client.post("/api/identity/login", json={"email": "x", "password": "y"}).status_code, 404)
        for path in ("/api/products", "/api/identity/owners", "/api/identity/me", "/api/agents/heartbeat",
                     "/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        self.assertNotIn("access-control-allow-origin",
                         self.client.get("/api/health", headers={"Origin": "https://evil.test"}).headers)

    def test_oversized_body_rejected(self):
        response = self.client.post("/api/identity/activate", content=b"x" * 70000,
                                    headers={"content-type": "application/json"})
        self.assertError(response, 413, "INVALID_PARAMS")


class ActivationTests(GatewayTestCase):
    def test_activation_flow_and_key_errors(self):
        key = studio_admin.issue_key("a@example.test")
        body = {"activation_key": key, "hostname": "PC-A", "platform": "WINDOWS"}
        response = self.client.post("/api/identity/activate", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        device = response.json()
        self.assertTrue(device["agent_id"].startswith("agt_"))
        self.assertEqual(device["owner_id"], self.owner_a)
        self.assertEqual(device["platform"], "WINDOWS")
        self.assertError(self.client.post("/api/identity/activate", json=body), 403, "ACTIVATION_KEY_USED")
        self.assertError(self.client.post("/api/identity/activate", json={**body, "activation_key": "SARAE-ACT-" + "x" * 30}),
                         403, "ACTIVATION_KEY_INVALID")
        self.assertError(self.client.post("/api/identity/activate", json={**body, "activation_key": "short"}),
                         403, "ACTIVATION_KEY_INVALID")
        self.assertError(self.client.post("/api/identity/activate", json={"hostname": "x"}), 422, "INVALID_PARAMS")

    def test_device_limit(self):
        self.activate()
        self.activate(hostname="PC-A2")
        key = studio_admin.issue_key("a@example.test")
        response = self.client.post("/api/identity/activate", json={"activation_key": key, "hostname": "PC-A3"})
        self.assertError(response, 409, "AGENT_LIMIT_REACHED")

    def test_expired_license_cannot_activate(self):
        studio_admin.set_expiry("a@example.test", "2020-01-01T00:00:00+00:00")
        key = studio_admin.issue_key("a@example.test")
        self.assertError(self.client.post("/api/identity/activate", json={"activation_key": key, "hostname": "PC"}),
                         403, "LICENSE_EXPIRED")

    def test_activation_rate_limit_per_ip(self):
        bogus = {"activation_key": "SARAE-ACT-" + "z" * 30, "hostname": "PC"}
        for _ in range(10):
            self.assertError(self.client.post("/api/identity/activate", json=bogus, headers={"CF-Connecting-IP": "198.51.100.7"}),
                             403, "ACTIVATION_KEY_INVALID")
        limited = self.client.post("/api/identity/activate", json=bogus, headers={"CF-Connecting-IP": "198.51.100.7"})
        self.assertError(limited, 429, "RATE_LIMITED")
        self.assertGreater(int(limited.headers["Retry-After"]), 0)
        # X-Forwarded-For is used when CF-Connecting-IP is absent, and another IP is not affected.
        self.assertError(self.client.post("/api/identity/activate", json=bogus,
                                          headers={"X-Forwarded-For": "198.51.100.8, 10.0.0.1"}), 403, "ACTIVATION_KEY_INVALID")
        self.activate(ip="198.51.100.9")


class AuthAndLicenseTests(GatewayTestCase):
    def test_license_payload(self):
        device = self.activate()
        response = self.client.get("/api/studio/v1/license", headers=self.auth(device))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["valid"], True)
        self.assertEqual(body["email"], "a@example.test")
        self.assertEqual(body["expires_at"], self.expiry)
        self.assertEqual(body["max_agents"], 2)
        self.assertEqual(body["agent_id"], device["agent_id"])
        self.assertEqual(body["hostname"], "PC-A")
        self.assertEqual(body["grace_hours"], 72)
        datetime.fromisoformat(body["server_time"])
        self.assertNotIn("agent_credential", response.text)

    def test_license_never_expires(self):
        device = self.activate("b@example.test")
        self.assertIsNone(self.client.get("/api/studio/v1/license", headers=self.auth(device)).json()["expires_at"])

    def test_auth_error_codes(self):
        device = self.activate()
        url = "/api/studio/v1/license"
        self.assertError(self.client.get(url), 401, "AGENT_NOT_ACTIVATED")
        self.assertError(self.client.get(url, headers={"X-Sarae-Agent": device["agent_id"]}), 401, "AGENT_NOT_ACTIVATED")
        self.assertError(self.client.get(url, headers={"Authorization": "Bearer " + device["agent_credential"]}),
                         401, "AGENT_NOT_ACTIVATED")
        self.assertError(self.client.get(url, headers={**self.auth(device), "Authorization": "Bearer wrong"}),
                         403, "AGENT_UNAUTHORIZED")
        self.assertError(self.client.get(url, headers={**self.auth(device), "X-Sarae-Agent": "agt_other"}),
                         403, "AGENT_UNAUTHORIZED")
        for path in ("/api/studio/v1/voices",):
            self.assertError(self.client.get(path), 401, "AGENT_NOT_ACTIVATED")
        self.assertError(self.client.post("/api/studio/v1/tts/synthesize", json={}), 401, "AGENT_NOT_ACTIVATED")

    def test_expired_disabled_revoked(self):
        device = self.activate()
        url = "/api/studio/v1/license"
        studio_admin.set_expiry("a@example.test", "2020-01-01T00:00:00+00:00")
        self.assertError(self.client.get(url, headers=self.auth(device)), 403, "LICENSE_EXPIRED")
        studio_admin.set_expiry("a@example.test", None)
        self.assertEqual(self.client.get(url, headers=self.auth(device)).status_code, 200)
        studio_admin.set_enabled("a@example.test", False)
        self.assertError(self.client.get(url, headers=self.auth(device)), 403, "LICENSE_EXPIRED")
        studio_admin.set_enabled("a@example.test", True)
        self.assertEqual(self.client.get(url, headers=self.auth(device)).status_code, 200)
        studio_admin.revoke_device(device["agent_id"])
        self.assertError(self.client.get(url, headers=self.auth(device)), 403, "AGENT_REVOKED")

    def test_agent_rate_limit(self):
        device = self.activate()
        with patch.object(studio_gateway, "agent_limiter", studio_gateway.SlidingWindow(3, 60)):
            for _ in range(3):
                self.assertEqual(self.client.get("/api/studio/v1/license", headers=self.auth(device)).status_code, 200)
            limited = self.client.get("/api/studio/v1/license", headers=self.auth(device))
            self.assertError(limited, 429, "RATE_LIMITED")
            self.assertIn("Retry-After", limited.headers)
            other = self.activate("b@example.test")
            self.assertEqual(self.client.get("/api/studio/v1/license", headers=self.auth(other)).status_code, 200)


class VoiceTests(GatewayTestCase):
    def test_voices_online_without_leaks(self):
        device = self.activate()
        response = self.client.get("/api/studio/v1/voices", headers=self.auth(device))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"online": True, "voices": [
            {"voice_id": "linh_phuong", "name": "Linh Phương", "rev": "abc123rev", "ready": True}]})
        for secret in ("prompt", "transcript", "ref.wav", "owner-secret", "bí mật", TOKEN, "bad id"):
            self.assertNotIn(secret, response.text)

    def test_voices_offline_uses_last_list(self):
        device = self.activate()
        self.voice.online = False
        empty = self.client.get("/api/studio/v1/voices", headers=self.auth(device))
        self.assertEqual(empty.json(), {"online": False, "voices": []})
        self.voice.online = True
        self.client.get("/api/studio/v1/voices", headers=self.auth(device))
        self.voice.online = False
        cached = self.client.get("/api/studio/v1/voices", headers=self.auth(device)).json()
        self.assertFalse(cached["online"])
        self.assertEqual(cached["voices"], [{"voice_id": "linh_phuong", "name": "Linh Phương", "rev": "abc123rev",
                                             "ready": False}])

    def test_voices_not_ready_while_model_loads(self):
        device = self.activate()
        self.voice.state = "loading"
        body = self.client.get("/api/studio/v1/voices", headers=self.auth(device)).json()
        self.assertFalse(body["online"])
        self.assertEqual([v["ready"] for v in body["voices"]], [False])


class SynthesizeTests(GatewayTestCase):
    def test_success_and_usage(self):
        device = self.activate()
        response = self.speak(device, text="  Chào cả nhà  ")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["X-Voice-Revision"], "abc123rev")
        self.assertEqual(response.content, WAV)
        self.assertEqual(self.voice.synth_bodies, [{"voice_id": "linh_phuong", "text": "Chào cả nhà",
                                                    "preset": "live_fast", "seed": 42}])
        self.assertError(self.speak(device, voice_id="missing_voice"), 404, "VOICE_NOT_FOUND")
        rows = self.usage(self.owner_a)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["agent_id"], row["requests"], row["failures"], row["chars"], row["audio_bytes"]),
                         (device["agent_id"], 2, 1, 22, len(WAV)))
        self.assertEqual(row["day"], datetime.now(timezone(timedelta(hours=7))).date().isoformat())

    def test_invalid_params(self):
        device = self.activate()
        cases = [dict(voice_id="a"), dict(voice_id="../etc/passwd"), dict(text="   "), dict(text="x" * 451),
                 dict(preset="demo_default"), dict(text=123)]
        for case in cases:
            self.assertError(self.speak(device, **case), 422, "INVALID_PARAMS")
        self.assertEqual(self.speak(device, text="x" * 450).status_code, 200)
        self.assertEqual(self.voice.synth_bodies[-1]["text"], "x" * 450)
        self.assertEqual(len(self.voice.synth_bodies), 1)
        self.assertEqual(self.usage(self.owner_a)[0]["failures"], len(cases))

    def test_unavailable(self):
        device = self.activate()
        self.voice.online = False
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.voice.online = True
        self.voice.synth_error = (503, "TTS_NOT_READY")
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.voice.synth_error = (500, "TTS_INFERENCE_FAILED")
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.voice.synth_error = None
        with patch.object(settings, "sarae_tts_server_url", ""):
            self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.assertEqual(self.usage(self.owner_a)[0]["failures"], 4)

    def test_bad_audio(self):
        device = self.activate()
        self.voice.audio = b"<html>not audio</html>"
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.voice.audio = b"RIFF" + b"\x00" * 2000          # RIFF without WAVE
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")
        self.voice.audio = WAV + b"\x00" * (10 * 1024 * 1024)
        self.assertError(self.speak(device), 503, "TTS_UNAVAILABLE")

    def _in_background(self, device, results, key):
        def run():
            results[key] = self.speak(device)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_owner_busy(self):
        device = self.activate()
        second = self.activate(hostname="PC-A2")
        self.voice.block = threading.Event()
        results = {}
        thread = self._in_background(device, results, "first")
        self.assertTrue(self.voice.entered.wait(5))
        busy = self.speak(second)
        self.assertError(busy, 429, "TTS_BUSY")
        self.voice.block.set()
        thread.join(10)
        self.assertEqual(results["first"].status_code, 200)
        self.assertEqual(self.speak(second).status_code, 200)

    def test_queue_timeout_and_fifo(self):
        a = self.activate()
        b = self.activate("b@example.test", hostname="PC-B")
        self.voice.block = threading.Event()
        results = {}
        thread = self._in_background(a, results, "a")
        self.assertTrue(self.voice.entered.wait(5))
        with patch.object(studio_gateway, "QUEUE_TIMEOUT", 0.2):
            started = time.monotonic()
            self.assertError(self.speak(b), 503, "TTS_QUEUE_TIMEOUT")
            self.assertLess(time.monotonic() - started, 3)
        self.voice.block.set()
        thread.join(10)
        self.assertEqual(results["a"].status_code, 200)
        self.assertEqual(self.speak(b).status_code, 200)
        row = self.usage(self.owner_b)[0]
        self.assertEqual((row["requests"], row["failures"]), (2, 1))

    def test_voice_server_busy_maps_to_queue_timeout(self):
        device = self.activate()
        self.voice.synth_error = (429, "TTS_BUSY")
        self.assertError(self.speak(device), 503, "TTS_QUEUE_TIMEOUT")


class FifoGateTests(unittest.TestCase):
    def test_order_and_timeout(self):
        gate = studio_gateway.FifoGate(1)
        self.assertTrue(gate.acquire(1))
        order, threads = [], []
        for i in range(3):
            def wait(i=i):
                if gate.acquire(5):
                    order.append(i)
                    time.sleep(0.02)
                    gate.release()
            threads.append(threading.Thread(target=wait))
            threads[-1].start()
            while gate.waiting() < i + 1:
                time.sleep(0.005)
        self.assertFalse(gate.acquire(0.05))          # times out and leaves the queue
        gate.release()
        for t in threads:
            t.join(5)
        self.assertEqual(order, [0, 1, 2])
        self.assertEqual((gate.active, gate.waiting()), (0, 0))


class AdminCliTests(GatewayTestCase):
    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = studio_admin.main(list(argv))
        return code, out.getvalue()

    def test_cli_commands(self):
        code, out = self.cli("create-customer", "--email", "C@Example.test", "--max-devices", "1", "--expires", "2027-01-31")
        self.assertEqual(code, 0)
        self.assertIn("c@example.test", out)
        code, out = self.cli("issue-key", "--email", "c@example.test")
        key = out.strip().splitlines()[-1]
        self.assertTrue(key.startswith("SARAE-ACT-"))
        device = self.client.post("/api/identity/activate", json={"activation_key": key, "hostname": "PC-C"}).json()
        license_ = self.client.get("/api/studio/v1/license", headers=self.auth(device)).json()
        self.assertEqual(license_["expires_at"], "2027-01-31T16:59:59+00:00")
        _, out = self.cli("list")
        self.assertIn("c@example.test", out)
        self.assertIn("1/1", out)
        _, out = self.cli("devices", "--email", "c@example.test")
        self.assertIn(device["agent_id"], out)
        self.assertEqual(self.cli("extend", "--email", "c@example.test", "--never")[0], 0)
        self.assertIsNone(self.client.get("/api/studio/v1/license", headers=self.auth(device)).json()["expires_at"])
        self.cli("disable", "--email", "c@example.test")
        self.assertError(self.client.get("/api/studio/v1/license", headers=self.auth(device)), 403, "LICENSE_EXPIRED")
        self.cli("enable", "--email", "c@example.test")
        self.speak(device)
        _, out = self.cli("usage", "--email", "c@example.test", "--days", "7")
        self.assertIn("Tổng: 1 câu, 0 lỗi", out)
        self.cli("revoke-device", "--agent-id", device["agent_id"])
        self.assertError(self.client.get("/api/studio/v1/license", headers=self.auth(device)), 403, "AGENT_REVOKED")
        self.assertEqual(self.cli("issue-key", "--email", "nobody@example.test")[0], 1)
        self.assertEqual(self.cli("create-customer", "--email", "c@example.test", "--never")[0], 1)


if __name__ == "__main__":
    unittest.main()
