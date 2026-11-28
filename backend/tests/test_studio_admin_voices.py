"""Admin voice page (app.admin_main): loopback only, custom header, and voice CRUD proxied to the voice server."""
import base64
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import admin_main, tts_server
from app.config import settings

TOKEN = "orchestrator-token"
WAV = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\0" * 28
LOCAL = "http://127.0.0.1:8001"
HEADERS = {"X-Sarae-Admin": "1"}


class FakeVoiceServer:
    def __init__(self):
        self.online = True
        self.profiles = {"linh": {"voice_id": "linh", "name": "Linh", "rev": "r1", "owner_ref": "admin",
                                  "updated_at": "2026-11-28T00:00:00Z", "ref_wav_path": "F:/secret/ref.wav",
                                  "wav": {"duration_s": 8.2, "sample_rate": 24000},
                                  "transcript": {"mode": "asr", "manual_text": None, "asr_text": "xin chào"},
                                  "qc": {"warnings": ["clipping"]}, "prompt": {"compat": "c1"}}}
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.online:
            raise httpx.ConnectError("offline", request=request)
        path = request.url.path
        if path == "/health/ready":
            return httpx.Response(200, json={"state": "ready"})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"code": "TTS_AUTH"}})
        if path == "/v1/voices":
            return httpx.Response(200, json={"voices": list(self.profiles.values())})
        if path == "/v1/tts/synthesize":
            return httpx.Response(200, content=WAV, headers={"X-Voice-Rev": "r1"})
        parts = path.split("/")
        voice_id = parts[3] if len(parts) > 3 else ""
        if path.endswith("/transcript") and request.method == "PUT":
            profile = self.profiles[voice_id]
            text = json.loads(request.content)["transcript"]
            profile["transcript"] = {"mode": "manual" if text else "asr", "manual_text": text, "asr_text": "xin chào"}
            return httpx.Response(200, json=profile)
        if request.method == "GET":
            if voice_id in self.profiles:
                return httpx.Response(200, json=self.profiles[voice_id])
            return httpx.Response(404, json={"error": {"code": "VOICE_NOT_FOUND"}})
        if request.method == "PUT":
            assert b"RIFF" in request.content and b'name="owner_ref"' in request.content
            self.profiles[voice_id] = {"voice_id": voice_id, "name": voice_id, "rev": "new", "wav": {"duration_s": 5},
                                       "transcript": {"mode": "asr", "asr_text": "mới"}, "qc": {}, "prompt": None}
            return httpx.Response(201, json=self.profiles[voice_id])
        if request.method == "DELETE":
            self.profiles.pop(voice_id, None)
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})


class AdminVoiceTests(unittest.TestCase):
    def setUp(self):
        self.voice = FakeVoiceServer()
        self.patches = [patch.object(settings, "sarae_tts_server_url", "http://tts.test"),
                        patch.object(settings, "sarae_tts_server_token", TOKEN)]
        for p in self.patches:
            p.start()
        tts_server.set_transport(httpx.MockTransport(self.voice.handler))
        self.client = TestClient(admin_main.app, base_url=LOCAL, client=("127.0.0.1", 50000))

    def tearDown(self):
        tts_server.set_transport(None)
        for p in self.patches:
            p.stop()

    def test_page_and_api_are_loopback_only(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        remote = TestClient(admin_main.app, base_url=LOCAL, client=("203.0.113.9", 50000))
        self.assertEqual(remote.get("/").status_code, 403)
        rebinding = TestClient(admin_main.app, base_url="http://evil.example:8001", client=("127.0.0.1", 50000))
        self.assertEqual(rebinding.get("/admin/api/voices", headers=HEADERS).status_code, 403)

    def test_api_needs_custom_header(self):
        self.assertEqual(self.client.get("/admin/api/voices").status_code, 403)
        self.assertEqual(self.client.delete("/admin/api/voices/linh").status_code, 403)
        self.assertIn("linh", self.voice.profiles)

    def test_list_shows_admin_fields_without_paths(self):
        data = self.client.get("/admin/api/voices", headers=HEADERS).json()
        self.assertTrue(data["online"])
        voice = data["voices"][0]
        self.assertEqual((voice["voice_id"], voice["asr_text"], voice["ready"], voice["warnings"]),
                         ("linh", "xin chào", True, ["clipping"]))
        self.assertNotIn("ref_wav_path", json.dumps(data))
        self.assertNotIn("F:/secret", json.dumps(data))

    def test_list_when_voice_server_offline(self):
        self.voice.online = False
        data = self.client.get("/admin/api/voices", headers=HEADERS).json()
        self.assertFalse(data["online"])
        self.assertEqual(data["voices"], [])
        self.assertTrue(data["error"])

    def test_create_voice(self):
        body = {"voice_id": "nam_minh", "name": "Nam", "wav_base64": base64.b64encode(WAV).decode()}
        response = self.client.post("/admin/api/voices", headers=HEADERS, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("nam_minh", self.voice.profiles)

    def test_create_refuses_existing_id_unless_replace(self):
        body = {"voice_id": "linh", "wav_base64": base64.b64encode(WAV).decode()}
        self.assertEqual(self.client.post("/admin/api/voices", headers=HEADERS, json=body).status_code, 409)
        body["replace"] = True
        self.assertEqual(self.client.post("/admin/api/voices", headers=HEADERS, json=body).status_code, 200)

    def test_create_validates_id_and_wav(self):
        bad_id = {"voice_id": "x y", "wav_base64": base64.b64encode(WAV).decode()}
        self.assertEqual(self.client.post("/admin/api/voices", headers=HEADERS, json=bad_id).status_code, 422)
        not_wav = {"voice_id": "abc", "wav_base64": base64.b64encode(b"ID3" + b"\0" * 100).decode()}
        self.assertEqual(self.client.post("/admin/api/voices", headers=HEADERS, json=not_wav).status_code, 422)
        garbage = {"voice_id": "abc", "wav_base64": "@@@"}
        self.assertEqual(self.client.post("/admin/api/voices", headers=HEADERS, json=garbage).status_code, 422)

    def test_transcript_manual_and_back_to_whisper(self):
        r = self.client.put("/admin/api/voices/linh/transcript", headers=HEADERS, json={"transcript": "câu đúng"})
        self.assertEqual(r.json()["transcript_mode"], "manual")
        r = self.client.put("/admin/api/voices/linh/transcript", headers=HEADERS, json={"transcript": None})
        self.assertEqual(r.json()["transcript_mode"], "asr")

    def test_preview_returns_wav_with_live_preset(self):
        r = self.client.post("/admin/api/voices/linh/preview", headers=HEADERS, json={"text": "Dạ chị"})
        self.assertEqual((r.status_code, r.content[:4]), (200, b"RIFF"))
        synth = [q for q in self.voice.requests if q.url.path == "/v1/tts/synthesize"][-1]
        self.assertEqual(json.loads(synth.content)["preset"], "live_fast")
        self.assertEqual(self.client.post("/admin/api/voices/linh/preview", headers=HEADERS,
                                          json={"text": " "}).status_code, 422)

    def test_delete(self):
        self.assertEqual(self.client.delete("/admin/api/voices/linh", headers=HEADERS).status_code, 204)
        self.assertNotIn("linh", self.voice.profiles)

    def test_voice_server_down_is_503(self):
        self.voice.online = False
        r = self.client.post("/admin/api/voices/linh/preview", headers=HEADERS, json={"text": "Dạ"})
        self.assertEqual(r.status_code, 503)


class AdminCustomerTests(unittest.TestCase):
    """Customer management on the admin page: same operations as the CLI, on temp DBs."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app import identity, studio_gateway
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        folder = Path(self.tmp.name)
        self.patches = [patch.object(identity, "DB", folder / "identity.sqlite3"),
                        patch.object(studio_gateway, "USAGE_DB", folder / "studio_gateway.sqlite3")]
        for p in self.patches:
            p.start()
        self.gateway = studio_gateway
        self.client = TestClient(admin_main.app, base_url=LOCAL, client=("127.0.0.1", 50000))

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def customer(self, email):
        rows = self.client.get("/admin/api/customers", headers=HEADERS).json()["customers"]
        return next(c for c in rows if c["email"] == email)

    def test_needs_header_and_loopback(self):
        self.assertEqual(self.client.get("/admin/api/customers").status_code, 403)
        remote = TestClient(admin_main.app, base_url=LOCAL, client=("203.0.113.9", 50000))
        self.assertEqual(remote.post("/admin/api/customers", headers=HEADERS, json={"email": "x@y.vn"}).status_code, 403)

    def test_create_returns_a_key_and_lists_customer(self):
        r = self.client.post("/admin/api/customers", headers=HEADERS,
                             json={"email": "Shop@Example.vn", "max_devices": 2, "expires": "2027-06-30"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["activation_key"].startswith("SARAE-ACT-"))
        c = self.customer("shop@example.vn")
        self.assertEqual((c["status"], c["devices"], c["max_devices"], c["priority"]), ("hợp lệ", 0, 2, False))
        self.assertEqual(c["expires_at"], "2027-06-30T16:59:59+00:00")
        dup = self.client.post("/admin/api/customers", headers=HEADERS, json={"email": "shop@example.vn"})
        self.assertEqual(dup.status_code, 422)

    def test_username_instead_of_email(self):
        r = self.client.post("/admin/api/customers", headers=HEADERS, json={"username": "Shop_Linh"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["username"], "shop_linh")
        self.assertEqual(self.customer("shop_linh")["status"], "hợp lệ")
        key = self.client.post("/admin/api/customers/shop_linh/keys", headers=HEADERS)
        self.assertEqual(key.status_code, 200)
        for bad in ("ab", "có dấu", "a b c", "-start", "x" * 65):
            self.assertEqual(self.client.post("/admin/api/customers", headers=HEADERS,
                                              json={"username": bad}).status_code, 422, bad)

    def test_update_priority_devices_expiry_and_lock(self):
        self.client.post("/admin/api/customers", headers=HEADERS, json={"email": "a@shop.vn"})
        url = "/admin/api/customers/a%40shop.vn"
        self.assertEqual(self.client.patch(url, headers=HEADERS, json={"priority": True, "max_devices": 3,
                                                                       "expires": "2027-01-01"}).status_code, 200)
        c = self.customer("a@shop.vn")
        self.assertEqual((c["priority"], c["max_devices"], c["expires_at"]), (True, 3, "2027-01-01T16:59:59+00:00"))
        self.client.patch(url, headers=HEADERS, json={"expires": None, "enabled": False, "priority": False})
        c = self.customer("a@shop.vn")
        self.assertEqual((c["expires_at"], c["status"], c["enabled"], c["priority"]), (None, "đang khóa", False, False))
        self.assertEqual(self.client.patch(url, headers=HEADERS, json={"max_devices": 0}).status_code, 422)
        self.assertEqual(self.client.patch("/admin/api/customers/none%40x.vn", headers=HEADERS,
                                           json={"enabled": True}).status_code, 422)

    def test_new_key_devices_and_revoke(self):
        from app import gateway_main
        self.client.post("/admin/api/customers", headers=HEADERS, json={"email": "b@shop.vn"})
        key = self.client.post("/admin/api/customers/b%40shop.vn/keys", headers=HEADERS).json()["activation_key"]
        self.gateway.reset_state()
        with TestClient(gateway_main.app) as gw:
            device = gw.post("/api/identity/activate", json={"activation_key": key, "hostname": "PC-B"}).json()
        devices = self.client.get("/admin/api/customers/b%40shop.vn/devices", headers=HEADERS).json()["devices"]
        self.assertEqual((devices[0]["agent_id"], devices[0]["hostname"], devices[0]["enabled"]),
                         (device["agent_id"], "PC-B", True))
        self.assertEqual(self.customer("b@shop.vn")["devices"], 1)
        self.assertEqual(self.client.delete(f"/admin/api/devices/{device['agent_id']}", headers=HEADERS).status_code, 204)
        self.assertEqual(self.customer("b@shop.vn")["devices"], 0)
        self.assertEqual(self.client.delete("/admin/api/devices/agt_missing", headers=HEADERS).status_code, 422)


if __name__ == "__main__":
    unittest.main()
