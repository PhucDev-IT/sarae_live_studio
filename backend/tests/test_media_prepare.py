"""Phase 3: before a LIVE plays, its Agent downloads EVERY video of the session into its cache.

The Cloud asks with a `media_prepare` command, follows progress through heartbeats (`media_cache`) and refuses to
start (or to switch to a product added later) until the Agent reports every video with the current revision.
"""
from unittest.mock import patch

from support import SaasTestCase


class MediaPrepareTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.tiktok_products(self.owner_a, "Áo", "Quần", "Váy")
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.on_agent = {"agent_id": self.agent["agent_id"]}
        self.account = self.console_sync(self.agent, product_ids=(1, 2, 3))
        self.ao, self.ao2, self.quan, self.vay = self.library_videos(self.owner_a, "ao.mp4", "ao-2.mp4", "quan.mp4", "vay.mp4")
        self.link_videos(1, [self.ao, self.ao2])
        self.link_videos(2, [self.quan])
        self.link_videos(3, [self.vay])
        for target in ("app.live_sessions.LIVE_WAIT_SECONDS", "app.live.CONTROL_WAIT_SECONDS"):
            patcher = patch(target, 0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sid = self.client.post("/api/live/sessions", json={"name": "Tối nay", "product_ids": [1, 2],
                                                                 "tiktok_account_id": self.account}).json()["id"]

    def readiness(self, sid=None, headers=None):
        response = self.client.get(f"/api/live/sessions/{sid or self.sid}/media-readiness", params=self.on_agent, headers=headers or self.A)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self):
        return self.client.post(f"/api/live/sessions/{self.sid}/start", json=self.on_agent)

    def test_prepare_sends_one_background_command_with_the_session(self):
        first = self.client.post(f"/api/live/sessions/{self.sid}/prepare-media", json=self.on_agent)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual((first.json()["state"], first.json()["total"]), ("QUEUED", 3))
        again = self.client.post(f"/api/live/sessions/{self.sid}/prepare-media", json=self.on_agent)
        self.assertEqual(again.json()["command"]["command_id"], first.json()["command"]["command_id"], "a double click queues once")
        commands = self.run_agent(self.agent)
        self.assertEqual([(c["kind"], c["payload"]) for c in commands], [("media_prepare", {"session_id": self.sid})])

    def test_progress_comes_from_heartbeats_and_start_waits_for_every_video(self):
        self.assertEqual(self.readiness()["state"], "NOT_STARTED")
        refused = self.start()
        self.assertEqual((refused.status_code, refused.headers["X-Sarae-Error"]), (409, "MEDIA_NOT_READY"))

        partial = self.cache_report(self.owner_a, self.sid, state="PREPARING", only={self.ao})
        partial["bytes_done"] = 1000
        self.heartbeat(self.agent, media_cache=partial)
        status = self.readiness()
        self.assertEqual((status["state"], status["ready_count"], status["total"], status["bytes_done"]), ("DOWNLOADING", 1, 3, 1000))
        self.assertEqual({p["product_id"]: (p["ready"], p["total"]) for p in status["products"]}, {1: (1, 2), 2: (0, 1)})
        self.assertEqual(self.start().status_code, 409, "random playback may pick any video: all of them are required")

        self.heartbeat(self.agent, media_cache=self.cache_report(self.owner_a, self.sid))
        self.assertEqual(self.readiness()["state"], "READY")
        self.assertEqual(self.start().status_code, 202)
        self.assertEqual([c["kind"] for c in self.run_agent(self.agent)], ["product_play"])

    def test_only_this_session_this_agent_and_current_revisions_count(self):
        other = self.client.post("/api/live/sessions", json={"name": "Khác", "product_ids": [1, 2], "tiktok_account_id": self.account}).json()["id"]
        self.heartbeat(self.agent, media_cache=self.cache_report(self.owner_a, other))
        self.assertEqual(self.readiness()["state"], "NOT_STARTED", "a report for another session never counts")

        self.heartbeat(self.agent, media_cache=self.cache_report(self.owner_a, self.sid, revision="old-revision"))
        stale = self.readiness()
        self.assertEqual((stale["state"], stale["ready_count"]), ("INCOMPLETE", 0), "an older copy of the videos is downloaded again")

        second_pc = self.activate_agent(self.owner_a, "PC-B")
        self.heartbeat(second_pc, media_cache=self.cache_report(self.owner_a, self.sid))
        self.assertNotEqual(self.readiness()["state"], "READY", "PC-B's cache does not make PC-A ready")

    def test_failed_download_is_reported_with_the_video_name(self):
        report = self.cache_report(self.owner_a, self.sid, state="FAILED", only={self.ao})
        report["failed"] = [{"media_id": self.quan, "error": "Video tải xuống không khớp dung lượng/checksum"}]
        self.heartbeat(self.agent, media_cache=report)
        status = self.readiness()
        self.assertEqual(status["state"], "FAILED")
        self.assertEqual(status["failed"], [{"media_id": self.quan, "filename": "quan.mp4", "error": "Video tải xuống không khớp dung lượng/checksum"}])
        self.assertEqual(set(status["missing"]), {self.ao2, self.quan})

    def test_heartbeat_keeps_no_local_path(self):
        report = self.cache_report(self.owner_a, self.sid)
        report["ready"][0]["path"] = r"C:\Users\shop\AppData\Local\SaraeAgent\cloud-media-cache\x.mp4"
        self.heartbeat(self.agent, media_cache=report)
        import json
        from app.cloud_agents import _db
        with _db() as db:
            stored = db.execute("SELECT status_json FROM agent_status WHERE agent_id=?", (self.agent["agent_id"],)).fetchone()[0]
        self.assertNotIn("AppData", stored)
        self.assertEqual(json.loads(stored)["media_cache"]["state"], "READY")

    def test_session_without_videos_cannot_prepare(self):
        bare = self.tiktok_products(self.owner_a, "Mũ", seller="7001")[0]
        sid = self.client.post("/api/live/sessions", json={"name": "Trống", "product_ids": [bare], "tiktok_account_id": self.account}).json()["id"]
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/prepare-media", json=self.on_agent).status_code, 409)
        self.assertEqual(self.readiness(sid)["state"], "NO_VIDEO")

    def test_other_owner_cannot_prepare_or_read(self):
        self.assertEqual(self.client.post(f"/api/live/sessions/{self.sid}/prepare-media", json=self.on_agent, headers=self.B).status_code, 404)
        self.assertEqual(self.client.get(f"/api/live/sessions/{self.sid}/media-readiness", params=self.on_agent, headers=self.B).status_code, 404)
        b_agent = self.activate_agent(self.owner_b, "PC-B")
        b_sid = self.client.post("/api/live/sessions", json={"name": "x", "tiktok_account_id": self.account}, headers=self.B)
        self.assertNotEqual(b_sid.status_code, 200)
        foreign = self.client.post(f"/api/live/sessions/{self.sid}/prepare-media", json={"agent_id": b_agent["agent_id"]})
        self.assertEqual(foreign.status_code, 404, "A cannot send work to B's Agent")

    def test_product_added_during_live_is_downloaded_before_it_plays(self):
        self.heartbeat(self.agent, media_cache=self.cache_report(self.owner_a, self.sid))
        self.assertEqual(self.start().status_code, 202)
        self.run_agent(self.agent)
        self.heartbeat(self.agent, playback={"running": True, "session_id": self.sid, "product_id": 1},
                       media_cache=self.cache_report(self.owner_a, self.sid))
        switched = self.client.post(f"/api/live/sessions/{self.sid}/switch", json={"product_id": 3, "mode": "immediate"})
        self.assertEqual((switched.status_code, switched.headers["X-Sarae-Error"]), (409, "MEDIA_NOT_READY"))
        self.assertEqual([c["kind"] for c in self.run_agent(self.agent)], ["media_prepare"], "the missing video is fetched, nothing plays")
        self.heartbeat(self.agent, playback={"running": True, "session_id": self.sid, "product_id": 1},
                       media_cache=self.cache_report(self.owner_a, self.sid))
        self.assertEqual(self.client.post(f"/api/live/sessions/{self.sid}/switch", json={"product_id": 3, "mode": "immediate"}).status_code, 202)
