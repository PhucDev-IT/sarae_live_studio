"""Single Video Timeline LIVE: the timeline always wins (no manual pin on the Web, a pin changed on the Console is
reported and pinned back by the Agent), the wanted/actual pin state the Agent reports, and re-sending the active
version when the Agent runs another one."""
import unittest

from test_live_timeline_play import SingleVideoBase


class ManualPinTests(SingleVideoBase):
    def running(self, timeline_state=None):
        self.activate([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao},
                       {"at_ms": 6_000, "action": "PIN_PRODUCT", "product_id": self.quan}])
        self.start()
        self.run_agent(self.agent)  # anything the first heartbeats asked for
        self.playing(timeline_state)

    def playing(self, timeline_state=None):
        state = {"session_id": self.sid, "version": 1, **(timeline_state or {})}
        self.heartbeat(self.agent, {"running": True, "session_id": self.sid, "session_mode": "video", "connected": True,
                                    "media_duration": 12_500, "timeline": state})

    def kinds(self):
        return [(c["kind"], c["payload"]) for c in self.run_agent(self.agent)]

    def test_no_manual_pin_on_a_single_video_session(self):
        """The timeline always wins: pins of a Single Video session only come from its schedule."""
        self.running()
        pinned = self.client.post("/api/live/pin", params={"agent_id": self.agent["agent_id"]}, json={"product_id": self.quan})
        unpinned = self.client.post("/api/live/unpin", params={"agent_id": self.agent["agent_id"]})
        self.assertEqual((pinned.status_code, unpinned.status_code), (409, 409))
        self.assertIn("lịch ghim", pinned.json()["detail"])
        self.assertEqual(self.kinds(), [], "nothing reaches the Agent")
        self.assertEqual(self.client.post(f"{self.base}/timeline/resume").status_code, 404, "no hold, nothing to resume")

    def test_drift_reported_by_the_agent_lands_in_the_history(self):
        self.running()
        posted = self.client.post("/api/agents/events", headers=self.headers, json={"agent_id": self.agent["agent_id"], "events": [
            {"seq": 1, "type": "PIN_DRIFT", "session_id": self.sid, "product_id": None,
             "detail": {"tiktok_product_id": "123", "wanted_product_id": self.ao}}]})
        self.assertEqual(posted.status_code, 200, posted.text)
        self.assertIn("PIN_DRIFT", self.events())

    def test_pin_state_is_what_the_agent_reported(self):
        self.running({"position_ms": 7_000, "iteration": 1, "desired_product_id": self.quan,
                      "actual_product_id": self.ao, "actual_known": True, "live": True, "dry_run": True})
        state = self.client.get(f"{self.base}/pin-state").json()
        self.assertEqual((state["active_version"], state["agent_version"], state["position_ms"], state["media_duration_ms"]),
                         (1, 1, 7_000, 12_500))
        self.assertEqual((state["desired_product_name"], state["actual_product_name"], state["dry_run"]), ("Quần", "Áo", True))
        self.assertEqual(self.client.get(f"{self.base}/pin-state", headers=self.B).status_code, 404)

    def test_an_agent_running_another_version_gets_the_active_one_again(self):
        self.running()
        self.assertEqual(self.kinds(), [], "same version: nothing to send")
        self.playing({"version": None})  # e.g. the Agent restarted its timeline without the activation
        commands = self.kinds()
        self.assertEqual([(k, p["timeline"]["version"]) for k, p in commands], [("timeline_set", 1)])
        self.playing({"version": None})
        self.assertEqual(self.kinds(), [], "at most once a minute")
        self.assertIn("TIMELINE_RECONCILED", self.events())

    def test_legacy_sessions_still_pin_by_hand(self):
        legacy = self.client.post("/api/live/sessions", json={"name": "L", "tiktok_account_id": self.account,
                                                                "product_ids": [self.ao]}).json()["id"]
        from app.live import refuse_on_single
        with self.Session() as db:
            refuse_on_single(db, legacy)  # no error


if __name__ == "__main__":
    unittest.main()
