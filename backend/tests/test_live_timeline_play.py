"""Single Video Timeline LIVE, step 3: starting/resuming a session plays its main video on the Windows Agent with the
active pin timeline; a version activated during LIVE is pushed without touching playback; what the Agent's pin
scheduler did lands in the session history. The Agent is simulated through the real command endpoints."""
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from support import SaasTestCase


class SingleVideoBase(SaasTestCase):
    """A Single Video session of OWNER A on an online Agent (no test of its own)."""
    def setUp(self):
        super().setUp()
        self.ao, self.quan, self.vay = self.tiktok_products(self.owner_a, "Áo", "Quần", "Váy")
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.headers = {"X-Sarae-Agent-Key": self.agent["agent_credential"]}
        # The Console cart has Áo and Quần only: Váy is missing, which must not block a Single Video start.
        self.account = self.console_sync(self.agent, product_ids=(self.ao, self.quan))
        self.main, = self.library_videos(self.owner_a, "main.mp4")
        for target in ("app.live_sessions.LIVE_WAIT_SECONDS", "app.live.CONTROL_WAIT_SECONDS"):
            patcher = patch(target, 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        created = self.client.post("/api/live/sessions", json={"name": "Single", "tiktok_account_id": self.account,
                                                               "playback_mode": "SINGLE_VIDEO_TIMELINE", "main_media_id": self.main})
        self.sid = created.json()["id"]
        self.base = f"/api/live/sessions/{self.sid}"
        self.active_version = None

    def activate(self, markers):
        tid = self.client.post(f"{self.base}/timelines", json={}).json()["id"]
        saved = self.client.put(f"{self.base}/timelines/{tid}/markers", json={"markers": markers})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.post(f"{self.base}/timelines/{tid}/approve").status_code, 200)
        activated = self.client.post(f"{self.base}/timelines/{tid}/activate")
        self.assertEqual(activated.status_code, 200, activated.text)
        self.active_version = activated.json()["version"]
        return activated.json()

    def media_ready(self):
        self.heartbeat(self.agent, {"running": False}, media_cache=self.cache_report(self.owner_a, self.sid))

    def start(self):
        self.media_ready()
        started = self.client.post(f"{self.base}/start", json={"agent_id": self.agent["agent_id"]})
        self.assertEqual(started.status_code, 202, started.text)
        commands = self.run_agent(self.agent)
        # The Agent now runs the version it was sent (sarae_agent/timeline status in the heartbeat).
        self.heartbeat(self.agent, {"running": True, "session_id": self.sid, "session_mode": "video", "connected": True,
                                    "timeline": {"session_id": self.sid, "version": self.active_version}})
        return commands

    def session(self):
        return self.client.get(self.base).json()

    def events(self):
        return [e["event_type"] for e in self.client.get(f"{self.base}/events").json()]



class SingleVideoPlayTests(SingleVideoBase):
    def test_manifest_is_the_main_video_only(self):
        from app.agent_media import session_manifest
        with self.Session() as db:
            manifest = session_manifest(db, self.owner_a["id"], self.sid)
        self.assertEqual([i["media_id"] for i in manifest["items"]], [self.main])
        started = self.client.post(f"{self.base}/start", json={"agent_id": self.agent["agent_id"]})
        self.assertEqual((started.status_code, started.headers.get("X-Sarae-Error")), (409, "MEDIA_NOT_READY"))

    def test_start_plays_the_main_video_with_the_active_timeline(self):
        self.activate([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao},
                       {"at_ms": 4_000, "action": "NO_ACTION"},
                       {"at_ms": 6_000, "action": "PIN_PRODUCT", "product_id": self.vay},
                       {"at_ms": 9_000, "action": "UNPIN_PRODUCT"}])
        commands = self.start()
        self.assertEqual([c["kind"] for c in commands], ["video_play"])
        payload = commands[0]["payload"]
        self.assertEqual((payload["session_id"], payload["media_id"], payload["loop"], payload["start_ms"]),
                         (self.sid, self.main, True, 0))
        timeline = payload["timeline"]
        self.assertEqual([(m["at_ms"], m["action"], m["product_id"]) for m in timeline["markers"]],
                         [(0, "PIN_PRODUCT", self.ao), (6_000, "PIN_PRODUCT", self.vay), (9_000, "UNPIN_PRODUCT", None)])
        targets = {p["sarae_product_id"]: p for p in timeline["products"]}
        self.assertEqual((targets[self.ao]["tiktok_product_id"], targets[self.ao]["name"]), ("9001", "Áo"))
        self.assertEqual(timeline["expected_user_id"], targets[self.ao]["expected_user_id"])
        self.assertTrue(timeline["expected_user_id"])
        session = self.session()
        self.assertEqual((session["status"], session["agent_id"], session["current_product_id"]),
                         ("RUNNING", self.agent["agent_id"], None))
        self.assertIn("STARTED", self.events())

    def test_start_without_a_timeline_plays_the_video_alone(self):
        commands = self.start()
        self.assertIsNone(commands[0]["payload"]["timeline"])
        self.assertEqual(self.session()["status"], "RUNNING")

    def test_start_needs_a_main_video_and_a_fresh_console_sync(self):
        bare = self.client.post("/api/live/sessions", json={"name": "S", "tiktok_account_id": self.account,
                                                            "playback_mode": "SINGLE_VIDEO_TIMELINE"}).json()["id"]
        self.assertEqual(self.client.post(f"/api/live/sessions/{bare}/start", json={"agent_id": self.agent["agent_id"]}).status_code, 409)
        from app.tiktok_products import connection
        with connection() as db:
            db.execute("UPDATE snapshots SET scanned_at='2020-01-01T00:00:00+00:00'")
            db.commit()
        self.media_ready()
        stale = self.client.post(f"{self.base}/start", json={"agent_id": self.agent["agent_id"]})
        self.assertEqual(stale.status_code, 409)
        self.assertIn("đồng bộ", stale.json()["detail"])

    def test_a_version_activated_during_live_is_pushed_without_restarting_playback(self):
        self.activate([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao}])
        self.start()
        activated = self.activate([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.quan}])
        self.assertEqual((activated["status"], activated["sent"]), ("ACTIVE", True))
        commands = self.run_agent(self.agent)
        self.assertEqual([c["kind"] for c in commands], ["timeline_set"], "no video_play: playback is not touched")
        self.assertEqual((commands[0]["payload"]["timeline"]["version"],
                          commands[0]["payload"]["timeline"]["markers"][0]["product_id"]), (2, self.quan))

    def test_resume_continues_from_a_position_not_a_product(self):
        self.start()
        self.heartbeat(self.agent, {"running": False})  # Agent restarted: nothing plays
        self.assertEqual(self.client.post(f"{self.base}/resume", json={"product_id": self.ao}).status_code, 409)
        resumed = self.client.post(f"{self.base}/resume", json={"position_ms": 7_000})
        self.assertEqual(resumed.status_code, 202, resumed.text)
        commands = self.run_agent(self.agent)
        self.assertEqual((commands[0]["kind"], commands[0]["payload"]["start_ms"]), ("video_play", 7_000))
        self.assertIn("RESUMED", self.events())

    def test_pin_scheduler_events_land_in_the_history(self):
        self.start()
        events = [{"seq": 1, "type": "PIN_APPLIED", "session_id": self.sid, "product_id": self.ao,
                   "detail": {"action": "PIN_PRODUCT", "marker_id": 3, "position_ms": 1000, "dry_run": True}},
                  {"seq": 2, "type": "PIN_FAILED", "session_id": self.sid, "product_id": self.vay,
                   "detail": {"error": "Không tìm thấy card", "final": True}},
                  {"seq": 3, "type": "LIVE_GATE_CHANGED", "session_id": self.sid, "product_id": None,
                   "detail": {"state": "LIVE"}}]
        posted = self.client.post("/api/agents/events", headers=self.headers,
                                  json={"agent_id": self.agent["agent_id"], "events": events})
        self.assertEqual(posted.status_code, 200, posted.text)
        history = self.client.get(f"{self.base}/events").json()
        pins = [(e["event_type"], e["product_id"]) for e in history if e["event_type"].startswith(("PIN_", "LIVE_"))]
        self.assertEqual(pins, [("PIN_APPLIED", self.ao), ("PIN_FAILED", self.vay), ("LIVE_GATE_CHANGED", None)])
        self.assertIn("dry_run=True", next(e["detail"] for e in history if e["event_type"] == "PIN_APPLIED"))

    def test_finish_stops_the_agent(self):
        self.start()
        finished = self.client.post(f"{self.base}/finish")
        self.assertEqual(finished.json()["status"], "ENDED")
        self.assertEqual([c["kind"] for c in self.run_agent(self.agent)], ["playback_stop"])

    def test_agent_payloads_are_validated(self):
        from app.cloud_agents import validate_payload
        with self.assertRaises(HTTPException):
            validate_payload("video_play", {"session_id": 1, "media_id": "x"})
        with self.assertRaises(HTTPException):
            validate_payload("timeline_set", {"session_id": 1, "timeline": {"version": 1, "markers": [
                {"id": 1, "at_ms": 0, "action": "SWITCH_VIDEO"}]}})
        ok = validate_payload("timeline_set", {"session_id": 1, "timeline": None})
        self.assertEqual(ok, {"session_id": 1, "timeline": None})
        json.dumps(ok)


if __name__ == "__main__":
    unittest.main()
