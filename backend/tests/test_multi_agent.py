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
        self.tiktok_products(self.owner_a, "Áo", "Quần")
        self.a1 = self.activate_agent(self.owner_a, "PC-1")
        self.a2 = self.activate_agent(self.owner_a, "PC-2")
        # Two TikTok accounts of the shop, each logged in on its own PC's LIVE Console.
        self.acc1 = self.console_sync(self.a1, user_id="7494647113328657914", product_ids=(1, 2))
        self.acc2 = self.console_sync(self.a2, user_id="7495962653689350865", product_ids=(1, 2))
        # One Media Library shared by both PCs.
        self.v1 = self.library_videos(self.owner_a, "ao.mp4", "quan.mp4")
        self.v2 = self.library_videos(self.owner_a, "ao-2.mp4")
        # Two browser tabs of the same OWNER, each working with a different PC.
        self.tab1, self.tab2 = self.client, TestClient(app, headers=self.A)
        self.on1, self.on2 = {"agent_id": self.a1["agent_id"]}, {"agent_id": self.a2["agent_id"]}
        for target in ("app.live_sessions.LIVE_WAIT_SECONDS", "app.live.CONTROL_WAIT_SECONDS"):
            patcher = patch(target, 0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tab2.close()
        super().tearDown()

    def kinds(self, agent):
        """What that Agent receives when it polls now."""
        return [(c["kind"], c["payload"]) for c in self.run_agent(agent)]

    def test_both_agents_use_the_same_media_library(self):
        self.assertEqual({a["id"] for a in self.tab1.get("/api/owner/agents").json()["agents"]}, {self.a1["agent_id"], self.a2["agent_id"]})
        self.link_videos(1, [self.v1[0]])
        for tab, on in ((self.tab1, self.on1), (self.tab2, self.on2)):
            counts = {p["id"]: p["video_count"] for p in tab.get("/api/live/products", params=on).json()}
            self.assertEqual((counts[1], counts[2]), (1, 0), "the same library is seen from every PC")

    def test_session_is_bound_to_the_agent_it_was_created_on(self):
        self.link_videos(1, [self.v1[0]])
        self.link_videos(2, [self.v1[1]])
        sid = self.tab1.post("/api/live/sessions", json={"name": "PC-1 tối nay", "product_ids": [1, 2], "tiktok_account_id": self.acc1}).json()["id"]
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{sid}/start", json=self.on1).status_code, 202)
        self.assertEqual([k for k, _ in self.kinds(self.a1)], ["product_play"])
        self.heartbeat(self.a1, {"running": True, "session_id": sid, "product_id": 1}, hostname="PC-1")
        # The other tab (working with PC-2) switches the product of that session: it still goes to PC-1.
        self.assertEqual(self.tab2.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"}).status_code, 202)
        self.assertEqual(self.kinds(self.a2), [], "PC-2 receives nothing")
        self.assertEqual([p["product_id"] for k, p in self.kinds(self.a1)], [2])
        # A running LIVE never moves to another PC.
        self.assertEqual(self.tab2.patch(f"/api/live/sessions/{sid}", json=self.on2).status_code, 409)
        self.assertEqual(self.tab1.get(f"/api/live/sessions/{sid}").json()["agent_id"], self.a1["agent_id"])

    def test_draft_changes_account_and_picks_its_agent_only_when_starting(self):
        sid = self.tab1.post("/api/live/sessions", json={"name": "Nháp", "product_ids": [1], "tiktok_account_id": self.acc1}).json()["id"]
        moved = self.tab1.patch(f"/api/live/sessions/{sid}", json={"tiktok_account_id": self.acc2}).json()
        self.assertEqual((moved["tiktok_account_id"], moved["agent_id"]), (self.acc2, None))
        self.link_videos(1, [self.v2[0]])
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{sid}/start", json=self.on1).status_code, 409,
                         "PC-1 has the other account logged in")
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{sid}/start", json=self.on2).status_code, 202)
        self.assertEqual(self.tab1.get(f"/api/live/sessions/{sid}").json()["agent_id"], self.a2["agent_id"])

    def test_two_agents_can_run_two_sessions_at_once(self):
        self.link_videos(1, [self.v1[0]])
        s1 = self.tab1.post("/api/live/sessions", json={"name": "PC-1", "product_ids": [1], "tiktok_account_id": self.acc1}).json()["id"]
        s2 = self.tab2.post("/api/live/sessions", json={"name": "PC-2", "product_ids": [1], "tiktok_account_id": self.acc2}).json()["id"]
        self.assertEqual(self.tab1.post(f"/api/live/sessions/{s1}/start", json=self.on1).status_code, 202)
        self.assertEqual(self.tab2.post(f"/api/live/sessions/{s2}/start", json=self.on2).status_code, 202)
        # Both PCs play the product's videos from the shared library.
        self.assertEqual([p["media_ids"] for _, p in self.kinds(self.a1)], [[self.v1[0]]])
        self.assertEqual([p["media_ids"] for _, p in self.kinds(self.a2)], [[self.v1[0]]])
        self.assertEqual([self.tab1.get(f"/api/live/sessions/{sid}").json()["status"] for sid in (s1, s2)], ["RUNNING", "RUNNING"])
        # Playback controls name their Agent.
        self.tab2.post("/api/live/pause", params=self.on2)
        self.assertEqual(self.kinds(self.a1), [])
        self.assertEqual(self.kinds(self.a2), [("playback_pause", {})])

    def test_an_agent_only_receives_its_own_commands(self):
        self.assertEqual(self.tab1.post("/api/verification-monitor/start", params=self.on2).status_code, 200)
        stolen = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a1["agent_credential"]})
        self.assertEqual(stolen.status_code, 403)
        mine = self.client.get(f"/api/agents/commands/{self.a1['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a1["agent_credential"]})
        self.assertIsNone(mine.json()["command"])
        theirs = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a2["agent_credential"]})
        self.assertEqual(theirs.json()["command"]["kind"], "monitor_start")

    def test_revoked_agent_loses_access_both_ways(self):
        self.assertEqual(self.client.delete(f"/api/identity/agents/{self.a2['agent_id']}").status_code, 200)
        self.assertEqual(self.tab2.get("/api/live/products", params=self.on2).status_code, 403)
        draft = self.tab2.post("/api/live/sessions", json={"name": "x", "product_ids": [1], "tiktok_account_id": self.acc2}).json()["id"]
        self.assertEqual(self.tab2.post(f"/api/live/sessions/{draft}/start", json=self.on2).status_code, 403)
        polled = self.client.get(f"/api/agents/commands/{self.a2['agent_id']}/next", headers={"X-Sarae-Agent-Key": self.a2["agent_credential"]})
        self.assertEqual(polled.status_code, 403)
        self.assertNotIn(self.a2["agent_id"], {a["id"] for a in self.tab1.get("/api/owner/agents").json()["agents"]})

    def test_super_admin_cannot_operate_live(self):
        self.assertEqual(self.client.get("/api/live/status", params=self.on1, headers=self.ADMIN).status_code, 403)
        self.assertEqual(self.client.post("/api/live/sessions", json={"name": "x", **self.on1}, headers=self.ADMIN).status_code, 403)
        self.assertEqual(self.client.post("/api/live/pause", params=self.on1, headers=self.ADMIN).status_code, 403)

    def test_manual_test_playlists_are_removed(self):
        """The Web "Playlist" page and manual test playback were removed; LIVE plays by session/product only."""
        self.assertEqual(self.tab1.get("/api/agent-playlists", params=self.on1).status_code, 404)
        self.assertEqual(self.tab1.post("/api/agent-playlists", json={**self.on1, "name": "Test", "media_ids": self.v1}).status_code, 404)

    def test_agent_list_summarises_each_pc(self):
        self.heartbeat(self.a1, {"connected": True, "running": True, "session_id": 7})
        rows = {a["id"]: a for a in self.tab1.get("/api/owner/agents").json()["agents"]}
        self.assertEqual((rows[self.a1["agent_id"]]["obs_connected"], rows[self.a1["agent_id"]]["playing"]), (True, True))
        self.assertEqual(rows[self.a2["agent_id"]]["playing"], False)
        self.assertNotIn("status", rows[self.a1["agent_id"]], "raw heartbeat is not exposed")


if __name__ == "__main__":
    unittest.main()
