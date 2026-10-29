"""Single Video Timeline LIVE, step 4: manual pins hold the Agent's timeline (same command lane, before the pin),
resuming it, the wanted/actual pin state the Agent reports, and re-sending the active version when the Agent runs
another one."""
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

    def test_manual_pin_holds_the_timeline_first(self):
        self.running()
        pinned = self.client.post("/api/live/pin", params={"agent_id": self.agent["agent_id"]},
                                  json={"product_id": self.quan, "timeline_policy": "RESUME_AT_NEXT_MARKER"})
        self.assertIn(pinned.status_code, (200, 202), pinned.text)
        commands = self.kinds()
        self.assertEqual([k for k, _ in commands], ["timeline_hold", "PIN_PRODUCT"])
        self.assertEqual(commands[0][1], {"session_id": self.sid, "product_id": self.quan, "policy": "RESUME_AT_NEXT_MARKER"})
        unpinned = self.client.post("/api/live/unpin", params={"agent_id": self.agent["agent_id"]})
        self.assertIn(unpinned.status_code, (200, 202), unpinned.text)
        commands = self.kinds()
        self.assertEqual([k for k, _ in commands], ["timeline_hold", "UNPIN_PRODUCT"])
        self.assertEqual(commands[0][1]["policy"], "HOLD_UNTIL_RESUME", "default: never overwrite the operator")
        self.assertEqual(self.client.post("/api/live/pin", params={"agent_id": self.agent["agent_id"]},
                                          json={"product_id": self.quan, "timeline_policy": "FOREVER"}).status_code, 422)

    def test_resume_timeline(self):
        self.assertEqual(self.client.post(f"{self.base}/timeline/resume").status_code, 409, "not LIVE yet")
        self.running()
        resumed = self.client.post(f"{self.base}/timeline/resume")
        self.assertIn(resumed.status_code, (200, 202), resumed.text)
        self.assertEqual(self.kinds(), [("timeline_resume", {"session_id": self.sid})])
        self.assertIn("TIMELINE_RESUME_REQUESTED", self.events())

    def test_pin_state_is_what_the_agent_reported(self):
        self.running({"position_ms": 7_000, "iteration": 1, "desired_product_id": self.quan,
                      "actual_product_id": self.ao, "actual_known": True, "mode": "MANUAL_HOLD",
                      "manual_policy": "HOLD_UNTIL_RESUME", "live": True, "dry_run": True})
        state = self.client.get(f"{self.base}/pin-state").json()
        self.assertEqual((state["active_version"], state["agent_version"], state["position_ms"], state["media_duration_ms"]),
                         (1, 1, 7_000, 12_500))
        self.assertEqual((state["desired_product_name"], state["actual_product_name"], state["mode"], state["dry_run"]),
                         ("Quần", "Áo", "MANUAL_HOLD", True))
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

    def test_legacy_sessions_never_get_a_hold(self):
        from app.live import hold_timeline
        legacy = self.client.post("/api/live/sessions", json={"name": "L", "tiktok_account_id": self.account}).json()["id"]
        hold_timeline(self.owner_a, self.agent["agent_id"], legacy, 1, "HOLD_UNTIL_RESUME", None)
        self.assertEqual(self.kinds(), [])


if __name__ == "__main__":
    unittest.main()
