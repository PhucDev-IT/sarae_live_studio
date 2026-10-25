"""Voice configuration and Agent-only credential delivery, without paid API calls."""
from unittest.mock import patch

from app import credentials
from support import SaasTestCase


class VoiceSettingsTests(SaasTestCase):
    def test_config_and_key_are_owner_scoped_and_not_echoed(self):
        key = "eleven-test-key-secret"
        saved = self.client.put("/api/ai/voice/key", json={"api_key": key})
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn(key, saved.text)
        config = {"provider": "elevenlabs", "model": "eleven_flash_v2_5", "voice": "voice123",
                  "rate": 0, "settings": {"stability": 0.5, "speed": 1.0}}
        response = self.client.put("/api/ai/voice/config", json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["has_api_key"])
        self.assertNotIn(key, response.text)
        self.assertEqual(self.client.get("/api/ai/voice/config").json()["model"], "eleven_flash_v2_5")
        self.assertFalse(self.client.get("/api/ai/voice/config", headers=self.B).json()["has_api_key"])
        self.assertEqual(credentials.secret(self.owner_a["id"], "elevenlabs"), key)

    def test_only_activated_owner_agent_can_fetch_key(self):
        self.client.put("/api/ai/voice/key", json={"api_key": "eleven-test-key-secret"})
        agent_a = self.activate_agent(self.owner_a)
        agent_b = self.activate_agent(self.owner_b)
        path = f"/api/agents/voice-key/{agent_a['agent_id']}"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get(path, headers={"X-Sarae-Agent-Key": agent_b["agent_credential"]}).status_code, 403)
        response = self.client.get(path, headers={"X-Sarae-Agent-Key": agent_a["agent_credential"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["api_key"], "eleven-test-key-secret")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_catalog_filters_non_tts_models_with_mock_api(self):
        self.client.put("/api/ai/voice/key", json={"api_key": "eleven-test-key-secret"})
        import httpx
        def handler(request):
            self.assertEqual(request.headers["xi-api-key"], "eleven-test-key-secret")
            if request.url.path == "/v1/models":
                return httpx.Response(200, json=[{"model_id": "eleven_flash_v2_5", "can_do_text_to_speech": True},
                                                 {"model_id": "other", "can_do_text_to_speech": False}])
            return httpx.Response(200, json={"voices": [{"voice_id": "voice123", "name": "Demo"}]})
        with patch("app.ai_voice.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handler))):
            response = self.client.get("/api/ai/voice/catalog")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([m["id"] for m in response.json()["models"]], ["eleven_flash_v2_5"])
        self.assertEqual(response.json()["voices"][0]["id"], "voice123")

    def test_elevenlabs_preview_streams_mock_audio(self):
        import httpx
        self.client.put("/api/ai/voice/key", json={"api_key": "eleven-test-key-secret"})
        self.client.put("/api/ai/voice/config", json={"provider": "elevenlabs", "model": "eleven_flash_v2_5",
            "voice": "voice123", "rate": 0, "settings": {"speed": 1.05}})
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=b"ID3preview")
        with patch("app.ai_voice.httpx.Client", return_value=httpx.Client(transport=httpx.MockTransport(handler))):
            response = self.client.post("/api/ai/voice/preview", json={"text": "Xin chào"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ID3preview")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(requests[0].headers["xi-api-key"], "eleven-test-key-secret")
        self.assertIn(b'"model_id":"eleven_flash_v2_5"', requests[0].read())
