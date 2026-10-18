"""One OWNER, several Windows Agents, several browser tabs: actions never cross Agents.

The server keeps no "Agent in use" per account: every Agent action names its Agent or comes from the session's Agent.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.catalog import Product
from app.main import app
from support import SaasTestCase


class MultiAgentTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        with self.Session() as db:
            db.add_all([Product(name="Áo", sku="A1", owner_id=self.owner_a["id"]),
                        Product(name="Quần", sku="Q1", owner_id=self.owner_a["id"])])
            db.commit()
        self.a1 = self.activate_agent(self.owner_a, "PC-1")
        self.a2 = self.activate_agent(self.owner_a, "PC-2")
        self.v1 = self.sync_videos(self.a1, "ao-pc1.mp4", "quan-pc1.mp4")
        self.v2 = self.sync_videos(self.a2, "ao-pc2.mp4")
        # Two browser tabs of the same OWNER, each working with a different PC.
        self.tab1, self.tab2 = self.client, TestClient(app, headers=self.A)
        self.on1, self.on2 = {"agent_id": self.a1["agent_id"]}, {"agent_id": self.a2["agent_id"]}
        self.rpc = patch("app.cloud_agents.rpc", side_effect=lambda agent_id, owner_id, kind, payload=None, timeout=10: {"ok": True})
        self.calls = self.rpc.start()

    def tearDown(self):
        self.rpc.stop()
        self.tab2.close()
        super().tearDown()

    def sent_to(self, kind):
        return [c.args[0] for c in self.calls.call_args_list if c.args[2] == kind]

    def test_each_tab_sees_its_own_agent_library(self):
        self.assertEqual({v["id"] for v in self.tab1.get("/api/media", params=self.on1).json()}, set(self.v1))
        self.assertEqual({v["id"] for v in self.tab2.get("/api/media", params=self.on2).json()}, set(self.v2))
        self.assertEqual({a["id"] for a in self.tab1.get("/api/owner/agents").json()["agents"]}, {self.a1["agent_id"], self.a2["agent_id"]})

    def test_video_setup_is_per_agent_and_ids_do_not_cross(self):
        self.assertEqual(self.tab1.put("/api/product-videos/1", json={"media_ids": [self.v1[0]]}, params=self.on1).status_code, 200)
        self.assertEqual(self.tab2.put("/api/product-videos/1", json={"media_ids": [self.v2[0]]}, params=self.on2).status_code, 200)
        self.assertEqual(self.tab1.get("/api/product-videos/1", params=self.on1).json()["media_ids"], [self.v1[0]])
        self.assertEqual(self.tab2.get("/api/product-videos/1", params=self.on2).json()["media_ids"], [self.v2[0]])
        # A1's file id is not a video of A2, even for the same owner.
        self.assertEqual(self.tab2.put("/api/product-videos/2", json={"media_ids": [self.v1[1]]}, params=self.on2).status_code, 422)
        self.assertEqual(self.tab2.patch(f"/api/media/{self.v1[0]}", json={"tags": "x"}, params=self.on2).status_code, 404)

    def test_session_is_bound_to_the_agent_it_was_created_on(self):
        self.tab1.put("/api/product-videos/1", json={"media_ids": [self.v1[0]]}, params=self.on1)
        self.tab1.put("/api/product-videos/2", json={"media_ids": [self.v1[1]]}, params=self.on1)
        sid = self.tab1.post("/api/live/sessions", json={"name": "PC-1 tối nay", "product_ids": [1, 2], **self.on1}).json()["id"]
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{sid}/start", json={}).status_code, 200)
        self.heartbeat(self.a1, {"running": True, "session_id": sid, "product_id": 1}, hostname="PC-1")
        # The other tab (working with PC-2) switches the product of that session: it still goes to PC-1.
        self.assertEqual(self.tab2.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"}).status_code, 200)
        self.assertEqual(set(self.sent_to("product_play")), {self.a1["agent_id"]})
        # A running LIVE never moves to another PC.
        self.assertEqual(self.tab2.patch(f"/api/live/sessions/{sid}", json=self.on2).status_code, 409)
        self.assertEqual(self.tab1.get(f"/api/live/sessions/{sid}").json()["agent_id"], self.a1["agent_id"])

    def test_draft_can_move_to_another_agent_explicitly(self):
        sid = self.tab1.post("/api/live/sessions", json={"name": "Nháp", **self.on1}).json()["id"]
        moved = self.tab1.patch(f"/api/live/sessions/{sid}", json=self.on2)
        self.assertEqual(moved.json()["agent_id"], self.a2["agent_id"])

    def test_two_agents_can_run_two_sessions_at_once(self):
        self.tab1.put("/api/product-videos/1", json={"media_ids": [self.v1[0]]}, params=self.on1)
        self.tab2.put("/api/product-videos/1", json={"media_ids": [self.v2[0]]}, params=self.on2)
        s1 = self.tab1.post("/api/live/sessions", json={"name": "PC-1", "product_ids": [1], **self.on1}).json()["id"]
        s2 = self.tab2.post("/api/live/sessions", json={"name": "PC-2", "product_ids": [1], **self.on2}).json()["id"]
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{s1}/start", json={}).status_code, 200)
        self.assertEqual(self.tab2.post(f"/api/live/sessions/{s2}/start", json={}).status_code, 200)
        played = [(c.args[0], c.args[3]["media_ids"]) for c in self.calls.call_args_list if c.args[2] == "product_play"]
        self.assertEqual(played, [(self.a1["agent_id"], [self.v1[0]]), (self.a2["agent_id"], [self.v2[0]])])
        # Playback controls name their Agent.
        self.tab2.post("/api/live/pause", params=self.on2)
        self.assertEqual(self.sent_to("playback_pause"), [self.a2["agent_id"]])

    def test_an_agent_only_receives_its_own_commands(self):
        self.rpc.stop()
        self.assertEqual(self.tab1.post("/api/verification-monitor/start", params=self.on2).status_code, 200)
        stolen = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a1["agent_credential"]})
        self.assertEqual(stolen.status_code, 403)
        mine = self.client.get(f"/api/agents/commands/{self.a1['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a1["agent_credential"]})
        self.assertIsNone(mine.json()["command"])
        theirs = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a2["agent_credential"]})
        self.assertEqual(theirs.json()["command"]["kind"], "monitor_start")
        self.rpc.start()

    def test_revoked_agent_loses_access_both_ways(self):
        self.assertEqual(self.client.delete(f"/api/identity/agents/{self.a2['agent_id']}").status_code, 200)
        self.assertEqual(self.tab2.get("/api/media", params=self.on2).status_code, 403)
        self.assertEqual(self.tab2.post("/api/live/sessions", json={"name": "x", **self.on2}).status_code, 403)
        polled = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a2["agent_credential"]})
        self.assertEqual(polled.status_code, 403)
        self.assertNotIn(self.a2["agent_id"], {a["id"] for a in self.tab1.get("/api/owner/agents").json()["agents"]})

    def test_super_admin_cannot_operate_live(self):
        self.assertEqual(self.client.get("/api/live/status", params=self.on1, headers=self.ADMIN).status_code, 403)
        self.assertEqual(self.client.post("/api/live/sessions", json={"name": "x", **self.on1}, headers=self.ADMIN).status_code, 403)
        self.assertEqual(self.client.post("/api/live/pause", params=self.on1, headers=self.ADMIN).status_code, 403)


if __name__ == "__main__":
    unittest.main()
