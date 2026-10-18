"""AI settings, credentials and runtime are separate: keys are encrypted, write-only, per OWNER, never echoed or logged;
runtime state is released with its Agent and dropped when idle."""
import json
import unittest
from threading import Timer
from unittest.mock import patch

from cryptography.fernet import Fernet

from app import ai_service, credentials, identity
from app.ai_live_batches import states
from app.comment_processing import monitors
from app.per_owner import PerOwner, live_key
from app import runtime
from app.tiktok_chat import readers
from support import SaasTestCase

KEY_A = "sk-owner-a-SECRET-0001"
KEY_B = "sk-owner-b-SECRET-0002"
AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 5}
REPLY = json.dumps({"intent": "ASK_PRICE", "confidence": 0.9, "reply": "199k", "reason": "", "needs_review": False})


class CredentialTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        for target in (patch.object(credentials, "_fernet", Fernet(Fernet.generate_key())),
                       patch.object(ai_service.settings, "openai_api_key", ""),
                       patch.object(ai_service.settings, "gemini_api_key", "")):
            target.start()
            self.addCleanup(target.stop)

    def put_key(self, key, headers=None):
        response = self.client.put("/api/ai/credentials", json={"provider": "openai", "api_key": key}, headers=headers or self.A)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_key_is_write_only_and_never_returned(self):
        response = self.put_key(KEY_A)
        self.assertNotIn(KEY_A, response.text)
        self.assertEqual(response.json()["credentials"][1], {"provider": "openai", "configured": True, "last4": "0001",
                                                             "updated_at": response.json()["credentials"][1]["updated_at"]})
        self.client.put("/api/ai/config", json=AI)
        for path in ("/api/ai/config", "/api/ai/credentials", "/api/ai/usage"):
            self.assertNotIn(KEY_A, self.client.get(path).text, path)
        config = self.client.get("/api/ai/config").json()
        self.assertEqual((config["has_api_key"], config["key_source"]), (True, "owner"))
        self.assertNotIn("api_key", config)

    def test_key_is_encrypted_at_rest_in_its_own_table(self):
        self.put_key(KEY_A)
        self.assertNotIn(KEY_A.encode(), identity.DB.read_bytes())
        self.assertNotIn("api_key", json.dumps(identity.owner_settings(self.owner_a["id"])), "settings hold no secret")
        self.assertEqual(credentials.secret(self.owner_a["id"], "openai"), KEY_A)
        with patch.object(credentials, "_fernet", Fernet(Fernet.generate_key())):
            self.assertIsNone(credentials.secret(self.owner_a["id"], "openai"), "a wrong master key reads nothing")

    def test_owners_have_their_own_keys(self):
        self.put_key(KEY_A)
        self.put_key(KEY_B, headers=self.B)
        self.assertEqual(self.client.get("/api/ai/credentials", headers=self.B).json()["credentials"][1]["last4"], "0002")
        used = []
        self.client.put("/api/ai/config", json=AI)
        self.client.put("/api/ai/config", json=AI, headers=self.B)
        with patch.object(ai_service, "call_openai", lambda config, text, key: used.append(key) or (REPLY, 1, 1)):
            self.assertEqual(self.client.post("/api/ai/analyze", json={"comment": "giá?"}).status_code, 200)
            self.assertEqual(self.client.post("/api/ai/analyze", json={"comment": "giá?"}, headers=self.B).status_code, 200)
        self.assertEqual(used, [KEY_A, KEY_B])
        self.assertEqual(self.client.delete("/api/ai/credentials/openai", headers=self.B).status_code, 200)
        self.assertEqual(credentials.secret(self.owner_a["id"], "openai"), KEY_A, "B deleting their key leaves A's")

    def test_platform_key_is_the_fallback(self):
        self.client.put("/api/ai/config", json=AI)
        self.assertEqual(self.client.get("/api/ai/config").json()["key_source"], None)
        self.assertEqual(self.client.post("/api/ai/analyze", json={"comment": "giá?"}).status_code, 503)
        with patch.object(ai_service.settings, "openai_api_key", "sk-platform-key-9999"):
            self.assertEqual(self.client.get("/api/ai/config").json()["key_source"], "platform")
            self.put_key(KEY_A)
            self.assertEqual(self.client.get("/api/ai/config").json()["key_source"], "owner", "owner key wins")

    def test_provider_errors_never_leak_the_key(self):
        self.put_key(KEY_A)
        self.client.put("/api/ai/config", json=AI)

        def failing(config, text, key):
            raise ValueError(f"Incorrect API key provided: {key}")
        with patch.object(ai_service, "call_openai", failing):
            response = self.client.post("/api/ai/analyze", json={"comment": "giá?"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(KEY_A, response.text)
        logs = json.dumps(monitors(self.owner_a["id"]).snapshot())
        self.assertNotIn(KEY_A, logs)

    def test_invalid_input_is_rejected(self):
        self.assertEqual(self.client.put("/api/ai/credentials", json={"provider": "other", "api_key": "x" * 20}).status_code, 422)
        self.assertEqual(self.client.put("/api/ai/credentials", json={"provider": "openai", "api_key": "short"}).status_code, 422)
        self.assertEqual(self.client.delete("/api/ai/credentials/openai").status_code, 404)
        self.assertEqual(self.client.get("/api/ai/credentials", headers=self.ADMIN).status_code, 403, "SUPER_ADMIN reads no keys")


class RuntimeLifecycleTests(SaasTestCase):
    def test_idle_items_are_pruned_busy_ones_kept(self):
        registry = PerOwner(lambda key: {"key": key, "busy": False})
        registry("idle")["busy"] = False
        registry("busy")["busy"] = True
        with patch("app.per_owner.time.monotonic", return_value=10 ** 9):
            self.assertEqual(registry.prune(60, lambda item: item["busy"]), ["idle"])
        self.assertEqual(registry.keys(), ["busy"])
        registry("fresh")
        self.assertEqual(registry.prune(60), [], "recently used")

    def test_janitor_keeps_a_connected_reader_and_pending_batch(self):
        connected, idle = live_key(self.owner_a["id"], "agt_1"), live_key(self.owner_a["id"], "agt_2")
        readers(connected).status = "connected"
        readers(idle).status = "idle"
        states(connected).pending["k"] = [{}]
        states(idle)
        with patch("app.per_owner.time.monotonic", return_value=10 ** 9):
            runtime.prune(60)
        self.assertIsNotNone(readers.get(connected))
        self.assertIsNone(readers.get(idle))
        self.assertIsNotNone(states.get(connected))
        self.assertIsNone(states.get(idle))
        readers.discard(connected)
        states.discard(connected)

    def test_revoking_an_agent_releases_its_runtime(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        other = self.activate_agent(self.owner_a, "PC-A2")
        key, kept = live_key(self.owner_a["id"], agent["agent_id"]), live_key(self.owner_a["id"], other["agent_id"])
        readers(key).status = "connected"
        timer = Timer(60, lambda: None)
        timer.start()
        states(key).timers["k"] = timer
        readers(kept)
        self.assertEqual(self.client.delete(f"/api/identity/agents/{agent['agent_id']}").status_code, 200)
        self.assertIsNone(readers.get(key))
        self.assertIsNone(states.get(key))
        self.assertTrue(timer.finished.is_set(), "pending batch timer cancelled")
        self.assertIsNotNone(readers.get(kept), "the owner's other Agent keeps its reader")
        readers.discard(kept)


if __name__ == "__main__":
    unittest.main()
