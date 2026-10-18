"""LIVE sessions run on the OWNER's Windows Agent: the server records them and sends product_play / playback_stop."""
import unittest
from unittest.mock import patch

from app.catalog import Product
from app.live_sessions import LiveSession
from support import SaasTestCase


class LiveSessionTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        with self.Session() as db:
            db.add_all([Product(name="Áo", sku="A1", owner_id=self.owner_a["id"]),
                        Product(name="Quần", sku="Q1", owner_id=self.owner_a["id"]),
                        Product(name="Váy", sku="V1", owner_id=self.owner_a["id"])])
            db.commit()
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.ao, self.quan = self.sync_videos(self.agent, "ao.mp4", "quan.mp4")
        self.client.put("/api/product-videos/1", json={"media_ids": [self.ao]})
        self.client.put("/api/product-videos/2", json={"media_ids": [self.quan]})
        self.rpc = patch("app.cloud_agents.rpc", side_effect=lambda agent_id, owner_id, kind, payload=None, timeout=10: {"ok": True, "kind": kind})
        self.calls = self.rpc.start()

    def tearDown(self):
        self.rpc.stop()
        super().tearDown()

    def playing(self, session_id, product_id=1, **extra):
        self.heartbeat(self.agent, {"running": True, "session_id": session_id, "product_id": product_id,
                                    "connected": True, "transition_count": 3, **extra})

    def sent(self, kind):
        return [(c.args[3] if len(c.args) > 3 else c.kwargs.get("payload")) for c in self.calls.call_args_list if c.args[2] == kind]

    def start(self, product_ids=(1, 2)):
        sid = self.client.post("/api/live/sessions", json={"name": "Tối nay", "product_ids": list(product_ids)}).json()["id"]
        started = self.client.post(f"/api/live/sessions/{sid}/start", json={})
        self.assertEqual(started.status_code, 200, started.text)
        self.playing(sid)
        return sid

    def test_draft_can_be_created_empty_but_not_started(self):
        created = self.client.post("/api/live/sessions", json={"name": "Tối nay"})
        self.assertEqual(created.json()["product_ids"], [])
        self.assertEqual(self.client.post(f"/api/live/sessions/{created.json()['id']}/start", json={}).status_code, 409)

    def test_rename_duplicate_and_delete(self):
        sid = self.client.post("/api/live/sessions", json={"name": "A", "product_ids": [2, 1]}).json()["id"]
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"name": "  B  "}).json()["name"], "B")
        copy = self.client.post(f"/api/live/sessions/{sid}/duplicate").json()
        self.assertEqual((copy["product_ids"], copy["status"]), ([2, 1], "DRAFT"))
        self.assertEqual(self.client.delete(f"/api/live/sessions/{sid}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").status_code, 404)

    def test_start_sends_the_products_videos_to_the_agent(self):
        sid = self.start()
        self.assertEqual(self.sent("product_play")[0], {"session_id": sid, "product_id": 1, "media_ids": [self.ao], "mode": "immediate"})
        session = self.client.get(f"/api/live/sessions/{sid}").json()
        self.assertEqual((session["status"], session["current_product_id"]), ("RUNNING", 1))
        status = self.client.get("/api/live/status").json()
        self.assertEqual((status["session_id"], status["running"], status["agent"]["id"]), (sid, True, self.agent["agent_id"]))

    def test_product_without_videos_cannot_start(self):
        sid = self.client.post("/api/live/sessions", json={"name": "S", "product_ids": [3]}).json()["id"]
        refused = self.client.post(f"/api/live/sessions/{sid}/start", json={})
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").json()["status"], "DRAFT")

    def test_offline_agent_cannot_start(self):
        sid = self.client.post("/api/live/sessions", json={"name": "S", "product_ids": [1]}).json()["id"]
        from app.cloud_agents import connection
        with connection() as db:
            db.execute("UPDATE agent_status SET last_seen='2000-01-01T00:00:00+00:00'")
            db.commit()
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/start", json={}).status_code, 409)

    def test_immediate_switch_outside_lineup_adds_and_records_it(self):
        sid = self.start(product_ids=(1,))
        switched = self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"})
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(self.sent("product_play")[-1]["mode"], "immediate")
        session = self.client.get(f"/api/live/sessions/{sid}").json()
        self.assertEqual((session["product_ids"], session["current_product_id"]), ([1, 2], 2))
        kinds = [e["event_type"] for e in self.client.get(f"/api/live/sessions/{sid}/events").json()]
        self.assertEqual(kinds[-2:], ["PRODUCT_ADDED", "PRODUCT_SWITCHED"])

    def test_deferred_switch_is_recorded_when_the_agent_reports_it(self):
        sid = self.start()
        queued = self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "after_current"})
        self.assertEqual(queued.json(), {"ok": True, "pending_product_id": 2})
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").json()["current_product_id"], 1)
        posted = self.client.post("/api/agents/events", headers={"X-Sarae-Agent-Key": self.agent["agent_credential"]}, json={
            "agent_id": self.agent["agent_id"],
            "events": [{"seq": 1, "type": "PRODUCT_SWITCHED", "session_id": sid, "product_id": 2, "detail": {"trigger": "video_end"}}]})
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").json()["current_product_id"], 2)
        events = self.client.get(f"/api/live/sessions/{sid}/events").json()
        self.assertEqual((events[-1]["event_type"], events[-1]["detail"]), ("PRODUCT_SWITCHED", "trigger=video_end"))

    def test_finish_stops_the_agent_and_keeps_counters(self):
        sid = self.start()
        finished = self.client.post(f"/api/live/sessions/{sid}/finish")
        self.assertEqual(finished.json()["status"], "ENDED")
        self.assertEqual(len(self.sent("playback_stop")), 1)
        self.assertIn("transitions=3", self.client.get(f"/api/live/sessions/{sid}/events").json()[-1]["detail"])

    def test_stale_session_closes_without_agent_and_can_resume(self):
        with self.Session() as db:
            db.add(LiveSession(name="Treo", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent["agent_id"], current_product_id=2))
            db.commit()
        self.heartbeat(self.agent, {"running": False})
        self.assertEqual(self.client.delete("/api/live/sessions/1").status_code, 409)
        resumed = self.client.post("/api/live/sessions/1/resume", json={})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(self.sent("product_play")[-1]["product_id"], 2)
        self.assertEqual(self.client.get("/api/live/sessions/1/events").json()[-1]["event_type"], "RESUMED")
        closed = self.client.post("/api/live/sessions/1/finish")
        self.assertEqual(closed.json()["status"], "ENDED")
        self.assertEqual(self.client.get("/api/live/sessions/1/events").json()[-1]["detail"], "stale=true")
        self.assertEqual(self.sent("playback_stop"), [], "nothing to stop on the Agent")

    def test_ended_session_is_read_only(self):
        with self.Session() as db:
            db.add(LiveSession(name="Xong", status="ENDED", owner_id=self.owner_a["id"]))
            db.commit()
        self.assertEqual(self.client.patch("/api/live/sessions/1", json={"name": "X"}).status_code, 409)
        self.assertEqual(self.client.put("/api/live/sessions/1/products", json={"product_ids": [1]}).status_code, 409)

    def test_playback_controls_go_to_the_owners_agent_only(self):
        self.assertEqual(self.client.post("/api/live/pause").status_code, 200)
        self.assertEqual(self.client.post("/api/live/seek", json={"milliseconds": 5000}).status_code, 200)
        self.assertEqual(self.sent("playback_seek"), [{"ms": 5000}])
        self.assertEqual(self.client.post("/api/live/pause", headers=self.B).status_code, 409, "B has no Agent")
        self.assertEqual(self.client.get("/api/live/status", headers=self.B).json()["agent"], None)


if __name__ == "__main__":
    unittest.main()
