"""Runtime state (AI, chat, Agents) is per OWNER: one owner's settings or actions never reach another's."""
import unittest
from unittest.mock import patch

from app.ai_live_batches import context, states
from app.catalog import Product
from app.comment_processing import monitors
from app.live_sessions import LiveSession
from app.per_owner import live_key
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

    def test_chat_reader_and_processing_log_are_per_live_desk(self):
        a1, a2 = self.activate_agent(self.owner_a, "PC-A1"), self.activate_agent(self.owner_a, "PC-A2")
        b1 = self.activate_agent(self.owner_b, "PC-B1")
        readers(live_key(self.owner_a["id"], a1["agent_id"])).username = "shop_a"
        monitors(live_key(self.owner_a["id"], a1["agent_id"])).record("TEST", "a", "chỉ của A1")
        on = lambda agent: {"agent_id": agent["agent_id"]}  # noqa: E731
        self.assertEqual(self.client.get("/api/tiktok-chat/status", params=on(a1)).json()["username"], "shop_a")
        self.assertEqual(self.client.get("/api/tiktok-chat/status", params=on(a2)).json()["username"], "", "other PC of A")
        self.assertEqual(self.client.get("/api/tiktok-chat/processing", params=on(a2)).json()["logs"], [])
        self.assertEqual(self.client.get("/api/tiktok-chat/status", headers=self.B, params=on(a1)).status_code, 404)
        self.assertEqual(self.client.get("/api/tiktok-chat/status", headers=self.B, params=on(b1)).json()["username"], "")

    def test_ai_context_uses_the_owners_running_session(self):
        with self.Session() as db:
            product = Product(name="Áo A", sku="A", owner_id=self.owner_a["id"])
            db.add(product)
            db.flush()
            db.add(LiveSession(name="LIVE A1", status="RUNNING", owner_id=self.owner_a["id"], agent_id="agt_a1", current_product_id=product.id))
            db.commit()
        self.assertEqual(context(self.owner_a["id"], "agt_a1")["product"]["name"], "Áo A")
        self.assertIsNone(context(self.owner_a["id"], "agt_a2"), "A's other PC is not LIVE")
        self.assertIsNone(context(self.owner_b["id"], "agt_a1"), "B cannot borrow A's session")
        self.assertIsNot(states(live_key(self.owner_a["id"], "agt_a1")), states(live_key(self.owner_a["id"], "agt_a2")))

    def test_agents_and_commands_stay_with_their_owner(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        on = {"agent_id": agent["agent_id"]}
        mine = self.client.get("/api/owner/agents").json()
        self.assertEqual((mine["agents"][0]["id"], mine["agents"][0]["online"]), (agent["agent_id"], True))
        self.assertEqual(self.client.get("/api/owner/agents", headers=self.B).json()["agents"], [])
        # A's commands go to A's Agent; B cannot address it.
        spoken = self.client.post("/api/ai/voice/speak", json={"text": "Xin chào", **on})
        self.assertEqual(spoken.status_code, 200, spoken.text)
        self.assertEqual(spoken.json()["agent"]["id"], agent["agent_id"])
        self.assertEqual(self.client.post("/api/ai/voice/speak", json={"text": "Xin chào", **on}, headers=self.B).status_code, 404)
        self.assertEqual(self.client.post("/api/verification-monitor/start", params=on).status_code, 200)
        self.assertEqual(self.client.get("/api/verification-monitor/status", params=on).json()["agent"]["id"], agent["agent_id"])
        self.assertEqual(self.client.get("/api/verification-monitor/status", headers=self.B, params=on).status_code, 404)

    def test_offline_agent_is_reported(self):
        agent = self.activate_agent(self.owner_a, "PC-A", online=False)
        on = {"agent_id": agent["agent_id"]}
        self.assertEqual(self.client.post("/api/verification-monitor/start", params=on).status_code, 409)
        self.assertEqual(self.client.get("/api/verification-monitor/status", params=on).json()["state"]["status"], "agent_offline")

    def test_telegram_test_is_owner_scoped_and_never_returns_token(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        on = {"agent_id": agent["agent_id"]}
        endpoint = "/api/verification-monitor/telegram/test"
        payload = {"enabled": True, "chat_id": "-100777", "bot_token": "123:SECRET"}
        saved = self.client.put("/api/verification-monitor/telegram/config", params=on, json=payload)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn("123:SECRET", saved.text)
        status = self.client.get("/api/verification-monitor/status", params=on)
        self.assertEqual(status.json()["telegram_status"], "Sẵn sàng")
        self.assertNotIn("123:SECRET", status.text)
        from app.cloud_agents import DB
        self.assertNotIn(b"123:SECRET", DB.read_bytes())
        denied_save = self.client.put("/api/verification-monitor/telegram/config", headers=self.B,
                                      params=on, json=payload)
        self.assertEqual(denied_save.status_code, 404)
        with patch("app.cloud_agents.send_telegram") as sender:
                response = self.client.post(endpoint, params=on)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(sender.call_args.args[0], "-100777")
                self.assertIn("PC-A", sender.call_args.args[1])
                self.assertEqual(sender.call_args.args[2], "123:SECRET")
                self.assertNotIn("123:SECRET", response.text)
                denied = self.client.post(endpoint, headers=self.B, params=on)
                self.assertEqual(denied.status_code, 404)
        with patch("app.cloud_agents.send_telegram", side_effect=OSError("Telegram down; 123:SECRET")):
                failed = self.client.post(endpoint, params=on)
                self.assertEqual(failed.status_code, 502)
                self.assertIn("Telegram down", failed.text)
                self.assertNotIn("123:SECRET", failed.text)
        self.client.put("/api/verification-monitor/telegram/config", params=on,
                        json={"enabled": True, "chat_id": "-100777", "clear_token": True})
        missing = self.client.post(endpoint, params=on)
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(self.client.get("/api/verification-monitor/status", params=on).json()["telegram_status"], "Chưa có Bot Token")

    def test_monitor_interval_validation(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        on = {"agent_id": agent["agent_id"]}
        endpoint = "/api/verification-monitor/config"
        for interval in (179, 241):
            self.assertEqual(self.client.put(endpoint, params=on,
                                             json={"interval_seconds": interval, "threshold": 0.82}).status_code, 422)
        for interval in (180, 240):
            response = self.client.put(endpoint, params=on,
                                       json={"interval_seconds": interval, "threshold": 0.82})
            self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
