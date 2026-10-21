"""LIVE sessions run on the session's Windows Agent through tracked commands.

The web call answers 200 when the Agent confirmed in time, else 202 with the command; the session changes only when the
command's outcome is applied (whenever the Agent acks). Waits are 0 here, so every action shows the 202 path; the
simulated Agent (support.run_agent) then claims and acks through the real endpoints.
"""
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.catalog import Product
from app.live_sessions import LiveSession, play_product
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
        self.on_agent = {"agent_id": self.agent["agent_id"]}
        self.ao, self.quan = self.sync_videos(self.agent, "ao.mp4", "quan.mp4")
        self.client.put("/api/product-videos/1", json={"media_ids": [self.ao]}, params=self.on_agent)
        self.client.put("/api/product-videos/2", json={"media_ids": [self.quan]}, params=self.on_agent)
        for target in ("app.live_sessions.LIVE_WAIT_SECONDS", "app.live.CONTROL_WAIT_SECONDS"):
            patcher = patch(target, 0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, product_ids=(1, 2), name="Tối nay"):
        return self.client.post("/api/live/sessions", json={"name": name, "product_ids": list(product_ids), **self.on_agent}).json()["id"]

    def playing(self, session_id, product_id=1, **extra):
        self.heartbeat(self.agent, {"running": True, "session_id": session_id, "product_id": product_id,
                                    "connected": True, "transition_count": 3, **extra})

    def start(self, product_ids=(1, 2)):
        sid = self.create(product_ids)
        started = self.client.post(f"/api/live/sessions/{sid}/start", json={})
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(started.json()["session"]["status"], "DRAFT", "not RUNNING before the Agent confirms")
        self.run_agent(self.agent)
        self.playing(sid)
        return sid

    def session(self, sid):
        return self.client.get(f"/api/live/sessions/{sid}").json()

    def events(self, sid):
        return [e["event_type"] for e in self.client.get(f"/api/live/sessions/{sid}/events").json()]

    def test_draft_can_be_created_empty_but_not_started(self):
        created = self.client.post("/api/live/sessions", json={"name": "Tối nay", **self.on_agent})
        self.assertEqual((created.json()["product_ids"], created.json()["agent_id"]), ([], self.agent["agent_id"]))
        self.assertEqual(self.client.post(f"/api/live/sessions/{created.json()['id']}/start", json={}).status_code, 409)
        self.assertEqual(self.client.post("/api/live/sessions", json={"name": "Không Agent"}).status_code, 422)

    def test_rename_duplicate_and_delete(self):
        sid = self.create((2, 1), "A")
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"name": "  B  "}).json()["name"], "B")
        copy = self.client.post(f"/api/live/sessions/{sid}/duplicate").json()
        self.assertEqual((copy["product_ids"], copy["status"], copy["agent_id"]), ([2, 1], "DRAFT", self.agent["agent_id"]))
        self.assertEqual(self.client.delete(f"/api/live/sessions/{sid}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").status_code, 404)

    def test_start_becomes_running_when_the_agent_confirms(self):
        sid = self.create()
        self.client.post(f"/api/live/sessions/{sid}/start", json={})
        (command,) = self.run_agent(self.agent)
        self.assertEqual(command["payload"], {"session_id": sid, "product_id": 1, "media_ids": [self.ao], "mode": "immediate"})
        session = self.session(sid)
        self.assertEqual((session["status"], session["current_product_id"]), ("RUNNING", 1))
        self.assertEqual(self.events(sid)[-1], "STARTED")
        self.playing(sid)
        status = self.client.get("/api/live/status", params=self.on_agent).json()
        self.assertEqual((status["session_id"], status["running"], status["agent"]["id"]), (sid, True, self.agent["agent_id"]))

    def test_failed_start_keeps_the_draft_and_records_why(self):
        sid = self.create()
        self.client.post(f"/api/live/sessions/{sid}/start", json={})
        self.run_agent(self.agent, lambda c: (False, "OBS chưa kết nối"))
        self.assertEqual(self.session(sid)["status"], "DRAFT")
        events = self.client.get(f"/api/live/sessions/{sid}/events").json()
        self.assertEqual(events[-1]["event_type"], "START_FAILED")
        self.assertIn("OBS chưa kết nối", events[-1]["detail"])

    def test_stale_agent_video_index_rescans_then_retries_product_play(self):
        sid = self.create((1,))
        product_commands = []

        def command(agent_id, owner_id, kind, payload=None, **options):
            if kind == "media_rescan":
                return {"status": "SUCCESS", "result": {"count": 1}}
            product_commands.append((payload, options))
            if len(product_commands) == 1:
                return {"status": "FAILED", "command_id": "first", "error": "Sản phẩm #1 chưa có video hợp lệ"}
            return {"status": "SUCCESS", "command_id": "retry", "result": {"ok": True}}

        with self.Session() as db, patch("app.cloud_agents.run_command", side_effect=command):
            item = db.get(LiveSession, sid)
            result = play_product(db, self.owner_a, {"id": self.agent["agent_id"], "hostname": "PC-A"},
                                  item, 1, "immediate", "start", "click-1")

        self.assertEqual(result["command_id"], "retry")
        self.assertEqual([payload["media_ids"] for payload, _ in product_commands], [[self.ao], [self.ao]])
        self.assertNotEqual(product_commands[0][1]["idempotency_key"], product_commands[1][1]["idempotency_key"])

    def test_stale_agent_video_index_reports_when_rescan_finds_no_file(self):
        from app.agent_library import connection

        sid = self.create((1,))
        calls = []

        def command(agent_id, owner_id, kind, payload=None, **options):
            calls.append(kind)
            if kind == "media_rescan":
                with connection() as library_db:
                    library_db.execute("UPDATE agent_media SET available=0 WHERE agent_id=?", (agent_id,))
                    library_db.commit()
                return {"status": "SUCCESS", "result": {"count": 0}}
            return {"status": "FAILED", "command_id": "first", "error": "Sản phẩm #1 chưa có video hợp lệ"}

        with self.Session() as db:
            item = db.get(LiveSession, sid)
            with patch("app.cloud_agents.run_command", side_effect=command):
                with self.assertRaises(HTTPException) as raised:
                    play_product(db, self.owner_a, {"id": self.agent["agent_id"], "hostname": "PC-A"},
                                 item, 1, "immediate", "start", "click-2")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Đã quét lại thư viện", raised.exception.detail)
        self.assertEqual(calls, ["product_play", "media_rescan"])

    def test_double_click_never_starts_twice(self):
        sid = self.create()
        first = self.client.post(f"/api/live/sessions/{sid}/start", json={}, headers={**self.A, "Idempotency-Key": "click-1"})
        retry = self.client.post(f"/api/live/sessions/{sid}/start", json={}, headers={**self.A, "Idempotency-Key": "click-1"})
        self.assertEqual(first.json()["command"]["command_id"], retry.json()["command"]["command_id"])
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/start", json={}).status_code, 409, "a start is already on its way")
        self.assertEqual(len(self.run_agent(self.agent)), 1)

    def test_product_without_videos_cannot_start(self):
        sid = self.create((3,))
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/start", json={}).status_code, 409)
        self.assertEqual(self.session(sid)["status"], "DRAFT")

    def test_offline_agent_cannot_start(self):
        sid = self.create((1,))
        from app.cloud_agents import connection
        with connection() as db:
            db.execute("UPDATE agent_status SET last_seen='2000-01-01T00:00:00+00:00'")
            db.commit()
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/start", json={}).status_code, 409)

    def test_immediate_switch_outside_lineup_adds_and_records_it(self):
        sid = self.start(product_ids=(1,))
        switched = self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"})
        self.assertEqual(switched.status_code, 202, switched.text)
        (command,) = self.run_agent(self.agent)
        self.assertEqual(command["payload"]["mode"], "immediate")
        session = self.session(sid)
        self.assertEqual((session["product_ids"], session["current_product_id"]), ([1, 2], 2))
        self.assertEqual(self.events(sid)[-2:], ["PRODUCT_ADDED", "PRODUCT_SWITCHED"])

    def test_deferred_switch_is_recorded_when_the_agent_reports_it(self):
        sid = self.start()
        self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "after_current"})
        self.run_agent(self.agent)
        self.assertEqual((self.session(sid)["current_product_id"], self.events(sid)[-1]), (1, "SWITCH_QUEUED"))
        posted = self.client.post("/api/agents/events", headers={"X-Sarae-Agent-Key": self.agent["agent_credential"]}, json={
            "agent_id": self.agent["agent_id"],
            "events": [{"seq": 1, "type": "PRODUCT_SWITCHED", "session_id": sid, "product_id": 2, "detail": {"trigger": "video_end"}}]})
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertEqual(self.session(sid)["current_product_id"], 2)
        events = self.client.get(f"/api/live/sessions/{sid}/events").json()
        self.assertEqual((events[-1]["event_type"], events[-1]["detail"]), ("PRODUCT_SWITCHED", "trigger=video_end"))

    def test_finish_ends_now_cancels_queued_commands_and_stops_the_agent(self):
        sid = self.start()
        self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"})
        finished = self.client.post(f"/api/live/sessions/{sid}/finish")
        self.assertEqual(finished.json()["status"], "ENDED")
        self.assertIn("transitions=3", self.client.get(f"/api/live/sessions/{sid}/events").json()[-1]["detail"])
        processed = [c["kind"] for c in self.run_agent(self.agent)]
        self.assertEqual(processed, ["playback_stop"], "the queued switch was cancelled, only the stop runs")
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/finish").json()["status"], "ENDED", "finishing twice is harmless")

    def test_agent_still_playing_an_ended_session_is_stopped_on_heartbeat(self):
        sid = self.start()
        self.heartbeat(self.agent, {"running": False})  # Agent drops off: finishing cannot reach it
        self.client.post(f"/api/live/sessions/{sid}/finish")
        self.assertEqual(self.run_agent(self.agent), [])
        self.playing(sid)  # it comes back, still playing the ended session
        self.assertEqual([c["kind"] for c in self.run_agent(self.agent)], ["playback_stop"])
        self.assertEqual(self.events(sid)[-1], "RECONCILE_STOP")
        self.playing(sid)
        self.assertEqual(self.run_agent(self.agent), [], "not stopped again within the same minute")

    def test_reconnect_reconciles_the_product_actually_playing(self):
        sid = self.start()
        self.playing(sid, product_id=2)
        self.assertEqual((self.session(sid)["current_product_id"], self.events(sid)[-1]), (2, "RECONCILED"))

    def test_interrupted_session_closes_or_resumes_on_its_own_agent(self):
        with self.Session() as db:
            db.add(LiveSession(name="Treo", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent["agent_id"], current_product_id=2))
            db.commit()
        self.heartbeat(self.agent, {"running": False})
        self.assertEqual(self.client.delete("/api/live/sessions/1").status_code, 409)
        self.assertEqual(self.client.post("/api/live/sessions/1/resume", json={}).status_code, 202)
        (command,) = self.run_agent(self.agent)
        self.assertEqual(command["payload"]["product_id"], 2)
        self.assertEqual(self.events(1)[-1], "RESUMED")
        self.heartbeat(self.agent, {"running": False})
        closed = self.client.post("/api/live/sessions/1/finish")
        self.assertEqual((closed.json()["status"], self.client.get("/api/live/sessions/1/events").json()[-1]["detail"]), ("ENDED", "stale=true"))
        self.assertEqual(self.run_agent(self.agent), [], "nothing to stop on the Agent")

    def test_ended_session_is_read_only(self):
        with self.Session() as db:
            db.add(LiveSession(name="Xong", status="ENDED", owner_id=self.owner_a["id"], agent_id=self.agent["agent_id"]))
            db.commit()
        self.assertEqual(self.client.patch("/api/live/sessions/1", json={"name": "X"}).status_code, 409)
        self.assertEqual(self.client.put("/api/live/sessions/1/products", json={"product_ids": [1]}).status_code, 409)

    def test_playback_controls_are_tracked_commands_on_the_named_agent(self):
        paused = self.client.post("/api/live/pause", params=self.on_agent)
        self.assertEqual(paused.status_code, 202)
        seek = self.client.post("/api/live/seek", json={"milliseconds": 5000}, params=self.on_agent, headers={**self.A, "Idempotency-Key": "seek-1"})
        again = self.client.post("/api/live/seek", json={"milliseconds": 5000}, params=self.on_agent, headers={**self.A, "Idempotency-Key": "seek-1"})
        self.assertEqual(seek.json()["command"]["command_id"], again.json()["command"]["command_id"])
        self.assertEqual([(c["kind"], c["payload"]) for c in self.run_agent(self.agent)],
                         [("playback_pause", {}), ("playback_seek", {"ms": 5000})])
        command_id = paused.json()["command"]["command_id"]
        polled = self.client.get(f"/api/agents/{self.agent['agent_id']}/commands/{command_id}").json()
        self.assertEqual(polled["status"], "SUCCESS")
        self.assertEqual(self.client.post("/api/live/pause", headers=self.B, params=self.on_agent).status_code, 404, "not B's Agent")
        self.assertEqual(self.client.get("/api/live/status", headers=self.B, params=self.on_agent).status_code, 404)


if __name__ == "__main__":
    unittest.main()
