"""Playback policy of a LIVE session (spec §6): stored per session, remembered per owner, pushed to the Agent with the
lineup, paused when the operator switches product by hand."""
import json
import unittest
from unittest.mock import patch

from app.live_sessions import LiveSession, SessionEvent
from app.playback_policy import validate_command
from support import SaasTestCase

MEDIA = "a" * 32
ROTATE = {"video_order": "shuffle", "product_rotation": "random", "rotate_after_videos": 2, "rotate_after_minutes": None,
          "switch_mode": "after_current", "auto": True}


class PlaybackPolicyTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        with self.Session() as db:
            live = LiveSession(name="LIVE", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent["agent_id"])
            draft = LiveSession(name="Nháp", status="DRAFT", owner_id=self.owner_a["id"])
            db.add_all([live, draft])
            db.commit()
            self.live, self.draft = live.id, draft.id
        self.sent: list[dict] = []

        def fake_command(agent_id, owner_id, kind, payload=None, **_):
            self.sent.append({"kind": kind, "payload": payload})
            return {"command_id": "c", "status": "PENDING"}
        patcher = patch("app.cloud_agents.create_command", fake_command)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.playback_policy.lineup", lambda db, owner_id, sid: [{"product_id": 1, "media_ids": [MEDIA]},
                                                                                {"product_id": 2, "media_ids": [MEDIA]}])
        patcher.start()
        self.addCleanup(patcher.stop)

    def url(self, sid):
        return f"/api/live/sessions/{sid}/playback-policy"

    def test_defaults_then_saved_policy_is_pushed_and_remembered(self):
        self.assertEqual(self.client.get(self.url(self.live)).json()["policy"]["video_order"], "sequential")
        saved = self.client.put(self.url(self.live), json=ROTATE).json()
        self.assertEqual((saved["policy"], saved["sent"], saved["agent_error"]), (ROTATE, True, None))
        self.assertEqual(self.sent[-1]["kind"], "playback_policy")
        self.assertEqual(self.sent[-1]["payload"]["lineup"][1]["product_id"], 2)
        self.assertEqual(self.client.get(self.url(self.draft)).json()["policy"]["video_order"], "shuffle",
                         "the owner's last policy is the default of other sessions")
        self.assertEqual(self.client.get(self.url(self.draft), headers=self.B).status_code, 404)
        draft = self.client.put(self.url(self.draft), json=ROTATE).json()
        self.assertEqual(draft["sent"], False, "a DRAFT session is not playing: stored only")

    def test_invalid_policies_are_refused(self):
        for bad in ({**ROTATE, "video_order": "chaos"}, {**ROTATE, "rotate_after_videos": None, "rotate_after_minutes": None},
                    {**ROTATE, "rotate_after_videos": 99}):
            self.assertEqual(self.client.put(self.url(self.live), json=bad).status_code, 422, bad)

    def test_manual_switch_pauses_rotation_until_resumed(self):
        from app.playback_policy import pause_auto
        self.client.put(self.url(self.live), json=ROTATE)
        pause_auto(self.owner_a["id"], self.live)
        policy = self.client.get(self.url(self.live)).json()["policy"]
        self.assertFalse(policy["auto"])
        self.assertFalse(self.sent[-1]["payload"]["policy"]["auto"])
        resumed = self.client.post(self.url(self.live) + "/resume").json()
        self.assertTrue(resumed["policy"]["auto"] and self.sent[-1]["payload"]["policy"]["auto"])
        with self.Session() as db:
            kinds = [e.event_type for e in db.query(SessionEvent).filter_by(session_id=self.live)]
        self.assertEqual(kinds, ["AUTO_PAUSED", "AUTO_RESUMED"])

    def test_no_pause_when_rotation_is_off(self):
        from app.playback_policy import pause_auto
        before = len(self.sent)
        pause_auto(self.owner_a["id"], self.live)
        self.assertEqual(len(self.sent), before)

    def test_session_start_pushes_a_custom_policy_only(self):
        from app.live_sessions import apply_command_outcome
        from app.playback_policy import push
        self.assertEqual(push(self.owner_a["id"], self.live), (False, None), "default policy: nothing to send")
        self.client.put(self.url(self.live), json=ROTATE)
        with self.Session() as db:
            db.get(LiveSession, self.live).status = "DRAFT"
            db.commit()
        self.sent.clear()
        with patch("app.tiktok_chat.follow_session"):
            apply_command_outcome({"status": "SUCCESS", "agent_id": self.agent["agent_id"], "command_id": "c1",
                                   "error": None, "late_ack": False},
                                  {"module": "live_sessions", "action": "start", "session_id": self.live, "product_id": 1})
        self.assertEqual(self.sent[-1]["kind"], "playback_policy")

    def test_agent_command_payload_is_validated(self):
        good = {"session_id": 3, "policy": ROTATE, "lineup": [{"product_id": 1, "media_ids": [MEDIA]}]}
        self.assertEqual(validate_command(good)["lineup"][0]["media_ids"], [MEDIA])
        for bad in ({**good, "session_id": 0}, {**good, "lineup": [{"product_id": 1, "media_ids": ["x"]}]},
                    {**good, "lineup": [{"product_id": 1, "media_ids": [MEDIA] * 3001}]},
                    {**good, "policy": {**ROTATE, "switch_mode": "later"}}):
            with self.assertRaises(Exception):
                validate_command(bad)
        json.dumps(validate_command(good))


if __name__ == "__main__":
    unittest.main()
