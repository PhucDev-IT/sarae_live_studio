"""Single Video Timeline LIVE, step 1: the session's playback mode and main video, kept apart from the legacy product
queue (which older clients still get when they send no mode), plus the additive schema and its rollback."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from support import SaasTestCase

BACKEND = Path(__file__).resolve().parents[1]


class PlaybackModeTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.tiktok_products(self.owner_a, "Áo", "Quần")
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.account = self.console_sync(self.agent, product_ids=(1, 2))
        self.main, self.other = self.library_videos(self.owner_a, "main.mp4", "other.mp4")
        self.foreign, = self.library_videos(self.owner_b, "b.mp4")

    def create(self, **body):
        return self.client.post("/api/live/sessions", json={"name": "Tối nay", "tiktok_account_id": self.account, **body})

    def single(self, media_id=None):
        created = self.create(playback_mode="SINGLE_VIDEO_TIMELINE", main_media_id=media_id or self.main)
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()

    def test_older_clients_without_a_mode_keep_the_product_queue(self):
        created = self.create(product_ids=[1, 2])
        self.assertEqual(created.status_code, 201, created.text)
        body = created.json()
        self.assertEqual((body["playback_mode"], body["main_media_id"], body["video_loop"], body["active_timeline_id"]),
                         ("ADVANCED_LEGACY", None, True, None))
        self.assertEqual(self.client.put(f"/api/live/sessions/{body['id']}/products", json={"product_ids": [2]}).status_code, 200)
        self.assertEqual(self.create(main_media_id=self.main).status_code, 422, "a main video only fits the Single Video mode")

    def test_single_video_session_takes_one_owned_video(self):
        body = self.single()
        self.assertEqual((body["playback_mode"], body["main_media_id"], body["product_ids"]),
                         ("SINGLE_VIDEO_TIMELINE", self.main, []))
        self.assertEqual(self.create(playback_mode="SINGLE_VIDEO_TIMELINE", main_media_id=self.foreign).status_code, 422,
                         "another owner's video")
        self.assertEqual(self.create(playback_mode="SINGLE_VIDEO_TIMELINE", main_media_id="0" * 32).status_code, 422)
        self.assertEqual(self.create(playback_mode="SINGLE_VIDEO_TIMELINE", product_ids=[1]).status_code, 422,
                         "products come from the timeline")
        self.assertEqual(self.create(playback_mode="QUEUE").status_code, 422)
        with self.Session() as db:
            from app.storage_models import MediaItem
            db.get(MediaItem, self.other).status = "DELETED"
            db.commit()
        self.assertEqual(self.create(playback_mode="SINGLE_VIDEO_TIMELINE", main_media_id=self.other).status_code, 422,
                         "a deleted video cannot be the main video")
        self.assertEqual(self.create(playback_mode="SINGLE_VIDEO_TIMELINE").status_code, 201, "a DRAFT may pick it later")

    def test_draft_changes_mode_main_video_and_loop(self):
        sid = self.create().json()["id"]
        patched = self.client.patch(f"/api/live/sessions/{sid}", json={"playback_mode": "SINGLE_VIDEO_TIMELINE",
                                                                       "main_media_id": self.other, "video_loop": False})
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertEqual((patched.json()["playback_mode"], patched.json()["main_media_id"], patched.json()["video_loop"]),
                         ("SINGLE_VIDEO_TIMELINE", self.other, False))
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"main_media_id": self.foreign}).status_code, 422)
        events = [e["event_type"] for e in self.client.get(f"/api/live/sessions/{sid}/events").json()]
        self.assertEqual(events, ["CREATED", "MODE_CHANGED", "VIDEOS_CHANGED"])
        legacy = self.create().json()["id"]
        self.assertEqual(self.client.patch(f"/api/live/sessions/{legacy}", json={"main_media_id": self.main}).status_code, 422)

    def test_mode_and_main_video_are_fixed_once_started(self):
        sid = self.single()["id"]
        with self.Session() as db:
            from app.live_sessions import LiveSession
            db.get(LiveSession, sid).status = "RUNNING"
            db.commit()
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"playback_mode": "ADVANCED_LEGACY"}).status_code, 409)
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"main_media_id": self.other}).status_code, 409)
        looped = self.client.patch(f"/api/live/sessions/{sid}", json={"video_loop": False})
        self.assertEqual((looped.status_code, looped.json()["video_loop"]), (200, False), "loop can change during LIVE")

    def test_product_queue_actions_are_refused_on_a_single_video_session(self):
        sid = self.single()["id"]
        refused = {
            "lineup": self.client.put(f"/api/live/sessions/{sid}/products", json={"product_ids": [1]}),
            "policy": self.client.put(f"/api/live/sessions/{sid}/playback-policy", json={}),
            "policy resume": self.client.post(f"/api/live/sessions/{sid}/playback-policy/resume"),
            "start": self.client.post(f"/api/live/sessions/{sid}/start", json={"agent_id": self.agent["agent_id"]}),
        }
        for name, response in refused.items():
            self.assertEqual(response.status_code, 409, f"{name}: {response.text}")
        with self.Session() as db:
            from app.live_sessions import LiveSession
            db.get(LiveSession, sid).status = "RUNNING"
            db.commit()
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 1}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/live/sessions/{sid}/resume", json={"product_id": 1}).status_code, 409)

    def test_policy_is_never_pushed_for_a_single_video_session(self):
        from app.live_sessions import LiveSession
        from app.playback_policy import push
        sid = self.single()["id"]
        with self.Session() as db:
            item = db.get(LiveSession, sid)
            item.status, item.agent_id = "RUNNING", self.agent["agent_id"]
            db.commit()
        self.assertEqual(push(self.owner_a["id"], sid, always=True), (False, None))

    def test_duplicate_keeps_mode_and_delete_removes_timelines(self):
        source = self.single()
        copy = self.client.post(f"/api/live/sessions/{source['id']}/duplicate").json()
        self.assertEqual((copy["playback_mode"], copy["main_media_id"]), ("SINGLE_VIDEO_TIMELINE", self.main))
        from app.live_timeline import LiveTimeline, TimelineMarker
        with self.Session() as db:
            timeline = LiveTimeline(session_id=source["id"], owner_id=self.owner_a["id"], version=1, media_id=self.main)
            db.add(timeline)
            db.flush()
            db.add(TimelineMarker(timeline_id=timeline.id, at_ms=0, action="PIN_PRODUCT", product_id=1))
            db.commit()
        self.assertEqual(self.client.delete(f"/api/live/sessions/{source['id']}").status_code, 204)
        with self.Session() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM live_timelines")).scalar(), 0)
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM live_timeline_markers")).scalar(), 0)

    def test_session_of_another_owner_stays_hidden(self):
        sid = self.single()["id"]
        self.assertEqual(self.client.patch(f"/api/live/sessions/{sid}", json={"video_loop": False}, headers=self.B).status_code, 404)


class SchemaUpgradeTests(unittest.TestCase):
    def test_startup_upgrade_adds_columns_to_an_existing_database(self):
        """An installation from before this mode: the startup schema adds the columns with legacy values."""
        from app.shop_schema import upgrade
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp, 'old.db').as_posix()}")
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, owner_id VARCHAR(64), sku VARCHAR(64), "
                                  "name VARCHAR(200), description TEXT, price FLOAT, image_url TEXT, status VARCHAR(20), created_at DATETIME)"))
                conn.execute(text("CREATE TABLE live_sessions (id INTEGER PRIMARY KEY, owner_id VARCHAR(64), agent_id VARCHAR(80), "
                                  "name VARCHAR(200), status VARCHAR(20), current_product_id INTEGER, created_at DATETIME, "
                                  "started_at DATETIME, ended_at DATETIME)"))
                conn.execute(text("INSERT INTO live_sessions(owner_id,name,status) VALUES('o','old','ENDED')"))
            upgrade(engine)
            upgrade(engine)  # idempotent
            with engine.connect() as conn:
                row = conn.execute(text("SELECT playback_mode, video_loop, main_media_id, active_timeline_id FROM live_sessions")).one()
            self.assertEqual(tuple(row), ("ADVANCED_LEGACY", 1, None, None))
            self.assertTrue({"live_timelines", "live_timeline_markers"} <= set(inspect(engine).get_table_names()))
            engine.dispose()

    def test_alembic_upgrade_downgrade_keeps_existing_sessions(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = {**os.environ, "SARAE_DATA_DIR": tmp}

            def alembic(*args):
                done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                                      capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])

            def query(sql):
                db = sqlite3.connect(Path(tmp, "sarae_live.db"))
                try:
                    return db.execute(sql).fetchall()
                finally:
                    db.close()

            alembic("upgrade", "20261023_0015")
            db = sqlite3.connect(Path(tmp, "sarae_live.db"))
            db.execute("INSERT INTO live_sessions(owner_id,name,status,created_at) VALUES('o','old','ENDED','2026-10-01')")
            db.commit()
            db.close()
            alembic("upgrade", "head")
            self.assertEqual(query("SELECT name, playback_mode, video_loop FROM live_sessions"), [("old", "ADVANCED_LEGACY", 1)])
            alembic("downgrade", "20261023_0015")
            columns = {row[1] for row in query("PRAGMA table_info(live_sessions)")}
            self.assertFalse({"playback_mode", "main_media_id", "video_loop", "active_timeline_id"} & columns)
            self.assertEqual(query("SELECT name FROM live_sessions"), [("old",)])
            self.assertEqual(query("SELECT name FROM sqlite_master WHERE name LIKE 'live_timeline%'"), [])
            alembic("upgrade", "head")


if __name__ == "__main__":
    unittest.main()
