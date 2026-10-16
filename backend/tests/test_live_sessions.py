import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.catalog import Product
from app.live import state
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class LiveSessionTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.db_patch = patch("app.live_sessions.SessionLocal", TestingSession)
        self.db_patch.start()
        with TestingSession() as db:
            db.add_all([Product(name="Áo", sku="A1"), Product(name="Quần", sku="Q1")])
            db.commit()
        state.active = False
        state.session_id = None
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.db_patch.stop()

    def test_draft_can_start_empty_but_cannot_start(self):
        created = self.client.post("/api/live/sessions", json={"name": "Tối nay"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["product_ids"], [])
        started = self.client.post(f"/api/live/sessions/{created.json()['id']}/start", json={})
        self.assertEqual(started.status_code, 409)

    def test_rename_duplicate_and_delete(self):
        sid = self.client.post("/api/live/sessions", json={"name": "A", "product_ids": [2, 1]}).json()["id"]
        renamed = self.client.patch(f"/api/live/sessions/{sid}", json={"name": "  B  "})
        self.assertEqual(renamed.json()["name"], "B")
        copy = self.client.post(f"/api/live/sessions/{sid}/duplicate")
        self.assertEqual(copy.status_code, 201)
        self.assertEqual(copy.json()["product_ids"], [2, 1])
        self.assertEqual(copy.json()["status"], "DRAFT")
        self.assertEqual(self.client.delete(f"/api/live/sessions/{sid}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").status_code, 404)

    def test_stale_running_session_can_be_closed_without_obs(self):
        from app.live_sessions import LiveSession
        with TestingSession() as db:
            db.add(LiveSession(name="Treo", status="RUNNING"))
            db.commit()
        self.assertEqual(self.client.delete("/api/live/sessions/1").status_code, 409)
        closed = self.client.post("/api/live/sessions/1/finish")
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(closed.json()["status"], "ENDED")
        events = self.client.get("/api/live/sessions/1/events").json()
        self.assertEqual(events[-1]["detail"], "stale=true")

    def test_switch_to_product_outside_lineup_adds_it(self):
        from app.live_sessions import LiveSession
        sid = self.client.post("/api/live/sessions", json={"name": "S", "product_ids": [1]}).json()["id"]
        with TestingSession() as db:
            db.get(LiveSession, sid).status = "RUNNING"
            db.commit()
        state.active, state.session_mode, state.session_id, state.product_id = True, "product", sid, 1
        try:
            with patch.object(state, "product_queue", return_value=[10]), patch.object(state, "switch_product", return_value={"ok": True}):
                switched = self.client.post(f"/api/live/sessions/{sid}/switch", json={"product_id": 2, "mode": "immediate"})
            self.assertEqual(switched.status_code, 200)
            self.assertEqual(self.client.get(f"/api/live/sessions/{sid}").json()["product_ids"], [1, 2])
        finally:
            state.active, state.session_id = False, None

    def test_resume_session_interrupted_by_restart(self):
        from app.live_sessions import LiveSession
        sid = self.client.post("/api/live/sessions", json={"name": "S", "product_ids": [1, 2]}).json()["id"]
        with TestingSession() as db:
            item = db.get(LiveSession, sid)
            item.status, item.current_product_id = "RUNNING", 2
            db.commit()

        def play(_index):
            state.active = True
            return {"ok": True}
        try:
            with patch.object(state, "product_queue", return_value=[10]), patch.object(state, "play", side_effect=play):
                resumed = self.client.post(f"/api/live/sessions/{sid}/resume", json={})
            self.assertEqual(resumed.status_code, 200)
            self.assertEqual(state.product_id, 2)
            self.assertEqual(state.session_id, sid)
            self.assertEqual(self.client.get(f"/api/live/sessions/{sid}/events").json()[-1]["event_type"], "RESUMED")
        finally:
            state.active, state.session_id = False, None

    def test_ended_session_is_read_only(self):
        from app.live_sessions import LiveSession
        with TestingSession() as db:
            db.add(LiveSession(name="Xong", status="ENDED"))
            db.commit()
        self.assertEqual(self.client.patch("/api/live/sessions/1", json={"name": "X"}).status_code, 409)
        self.assertEqual(self.client.put("/api/live/sessions/1/products", json={"product_ids": [1]}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
