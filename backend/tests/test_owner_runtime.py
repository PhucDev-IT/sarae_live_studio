"""Runtime state (AI, chat, Agents) is per OWNER: one owner's settings or actions never reach another's."""
import unittest
from unittest.mock import patch

from app.ai_live_batches import context, states
from app.catalog import Product
from app.comment_processing import monitors
from app.live_sessions import LiveSession
from app.tiktok_chat import readers
from support import SaasTestCase

AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 5}


class OwnerRuntimeTests(SaasTestCase):
    def test_ai_settings_are_per_owner(self):
        self.assertEqual(self.client.put("/api/ai/config", json=AI).status_code, 200)
        self.assertEqual(self.client.get("/api/ai/config").json()["model"], "gpt-test")
        self.assertNotEqual(self.client.get("/api/ai/config", headers=self.B).json()["model"], "gpt-test")
        self.client.put("/api/ai/live/config", json={"enabled": True, "window_seconds": 3, "max_comments": 4})
        self.assertFalse(self.client.get("/api/ai/live/config", headers=self.B).json()["enabled"])

    def test_chat_reader_and_processing_log_are_per_owner(self):
        readers(self.owner_a["id"]).username = "shop_a"
        monitors(self.owner_a["id"]).record("TEST", "a", "chỉ của A")
        self.assertEqual(self.client.get("/api/tiktok-chat/status").json()["username"], "shop_a")
        self.assertEqual(self.client.get("/api/tiktok-chat/status", headers=self.B).json()["username"], "")
        self.assertEqual(self.client.get("/api/tiktok-chat/processing", headers=self.B).json()["logs"], [])

    def test_ai_context_uses_the_owners_running_session(self):
        with self.Session() as db:
            product = Product(name="Áo A", sku="A", owner_id=self.owner_a["id"])
            db.add(product)
            db.flush()
            db.add(LiveSession(name="LIVE A", status="RUNNING", owner_id=self.owner_a["id"], current_product_id=product.id))
            db.commit()
        with patch("app.ai_live_batches.SessionLocal", self.Session):
            self.assertEqual(context(self.owner_a["id"])["product"]["name"], "Áo A")
            self.assertIsNone(context(self.owner_b["id"]), "B has no running session")
        self.assertIsNot(states(self.owner_a["id"]), states(self.owner_b["id"]))

    def test_agents_and_commands_stay_with_their_owner(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        mine = self.client.get("/api/owner/agents").json()
        self.assertEqual((mine["active_agent_id"], mine["agents"][0]["online"]), (agent["agent_id"], True))
        self.assertEqual(self.client.get("/api/owner/agents", headers=self.B).json()["agents"], [])
        self.assertEqual(self.client.put("/api/owner/active-agent", json={"agent_id": agent["agent_id"]}, headers=self.B).status_code, 404)
        # A's commands go to A's Agent; B has none.
        spoken = self.client.post("/api/ai/voice/speak", json={"text": "Xin chào"})
        self.assertEqual(spoken.status_code, 200, spoken.text)
        self.assertEqual(spoken.json()["agent"]["id"], agent["agent_id"])
        self.assertEqual(self.client.post("/api/ai/voice/speak", json={"text": "Xin chào"}, headers=self.B).status_code, 409)
        self.assertEqual(self.client.post("/api/verification-monitor/start").status_code, 200)
        self.assertEqual(self.client.get("/api/verification-monitor/status").json()["agent"]["id"], agent["agent_id"])
        self.assertEqual(self.client.get("/api/verification-monitor/status", headers=self.B).json()["state"]["status"], "no_agent")

    def test_offline_agent_is_reported(self):
        self.activate_agent(self.owner_a, "PC-A", online=False)
        self.assertEqual(self.client.post("/api/verification-monitor/start").status_code, 409)
        self.assertEqual(self.client.get("/api/verification-monitor/status").json()["state"]["status"], "agent_offline")


if __name__ == "__main__":
    unittest.main()
