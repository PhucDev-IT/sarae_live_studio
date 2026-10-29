"""Single Video Timeline LIVE, step 2: versioned pin timelines of a session (draft → approved → active) and the pin
state a position of the main video leads to."""
import unittest
from types import SimpleNamespace

from app.live_timeline import pin_at
from support import SaasTestCase


def marker(at_ms, action="PIN_PRODUCT", product_id=None, seq=0, review_status="APPROVED", id=None):
    return SimpleNamespace(at_ms=at_ms, action=action, product_id=product_id, seq=seq, review_status=review_status, id=id)


class PinAtTests(unittest.TestCase):
    def test_state_in_force_at_a_position(self):
        markers = [marker(10_000, product_id=1), marker(60_000, product_id=2), marker(90_000, "UNPIN_PRODUCT"),
                   marker(120_000, "NO_ACTION"), marker(150_000, product_id=3)]
        expected = {0: None, 9_999: None, 10_000: 1, 59_999: 1, 60_000: 2, 90_000: None, 130_000: None, 3_600_000: 3}
        for position, product in expected.items():
            self.assertEqual(pin_at(markers, position), product, position)

    def test_seek_backward_and_forward_give_the_state_at_the_new_position_only(self):
        markers = [marker(1_000, product_id=1), marker(2_000, product_id=2), marker(3_000, product_id=3)]
        self.assertEqual(pin_at(markers, 3_500), 3, "a forward jump does not replay 1 and 2")
        self.assertEqual(pin_at(markers, 1_500), 1, "a backward jump goes back to what was pinned there")

    def test_same_position_the_later_marker_wins_and_unreviewed_ones_do_nothing(self):
        same = [marker(5_000, product_id=2, seq=1), marker(5_000, product_id=1, seq=0)]
        self.assertEqual(pin_at(same, 5_000), 2)
        self.assertEqual(pin_at([marker(0, product_id=1), marker(1_000, product_id=2, review_status="NEEDS_REVIEW")], 2_000), 1)


class TimelineApiTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.ao, self.quan, self.vay = self.tiktok_products(self.owner_a, "Áo", "Quần", "Váy")
        self.foreign_product, = self.tiktok_products(self.owner_b, "B", seller="6001")
        agent = self.activate_agent(self.owner_a, "PC-A")
        self.account = self.console_sync(agent, product_ids=(1, 2, 3))
        self.main, self.other = self.library_videos(self.owner_a, "main.mp4", "other.mp4")  # 12.5 s each
        created = self.client.post("/api/live/sessions", json={"name": "Single", "tiktok_account_id": self.account,
                                                               "playback_mode": "SINGLE_VIDEO_TIMELINE", "main_media_id": self.main})
        self.sid = created.json()["id"]
        self.base = f"/api/live/sessions/{self.sid}/timelines"

    def new(self, **body):
        created = self.client.post(self.base, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()

    def save(self, tid, markers, status=200):
        saved = self.client.put(f"{self.base}/{tid}/markers", json={"markers": markers})
        self.assertEqual(saved.status_code, status, saved.text)
        return saved.json()

    def approved_and_active(self, markers):
        tid = self.new()["id"]
        self.save(tid, markers)
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        activated = self.client.post(f"{self.base}/{tid}/activate")
        self.assertEqual(activated.status_code, 200, activated.text)
        return tid

    def session(self):
        return self.client.get(f"/api/live/sessions/{self.sid}").json()

    def test_only_single_video_sessions_with_a_main_video_have_timelines(self):
        legacy = self.client.post("/api/live/sessions", json={"name": "L", "tiktok_account_id": self.account}).json()["id"]
        self.assertEqual(self.client.get(f"/api/live/sessions/{legacy}/timelines").status_code, 409)
        bare = self.client.post("/api/live/sessions", json={"name": "S", "tiktok_account_id": self.account,
                                                            "playback_mode": "SINGLE_VIDEO_TIMELINE"}).json()["id"]
        self.assertEqual(self.client.post(f"/api/live/sessions/{bare}/timelines", json={}).status_code, 409)
        self.assertEqual(self.client.get(self.base, headers=self.B).status_code, 404)

    def test_versions_take_the_main_video_and_its_duration(self):
        first, second = self.new(note="v1"), self.new()
        self.assertEqual((first["version"], second["version"], first["status"], first["duration_ms"], first["media_id"]),
                         (1, 2, "DRAFT", 12_500, self.main))
        self.assertEqual([t["version"] for t in self.client.get(self.base).json()], [2, 1])

    def test_markers_are_validated_and_ordered(self):
        tid = self.new()["id"]
        saved = self.save(tid, [{"at_ms": 9_000, "action": "UNPIN_PRODUCT", "product_id": self.ao},
                                {"at_ms": 3_000, "action": "PIN_PRODUCT", "product_id": self.quan, "label": "Quần"},
                                {"at_ms": 3_000, "action": "PIN_PRODUCT", "product_id": self.ao},
                                {"at_ms": 0, "action": "NO_ACTION"}])
        rows = [(m["at_ms"], m["action"], m["product_id"], m["seq"]) for m in saved["markers"]]
        self.assertEqual(rows, [(0, "NO_ACTION", None, 0), (3_000, "PIN_PRODUCT", self.quan, 0),
                                (3_000, "PIN_PRODUCT", self.ao, 1), (9_000, "UNPIN_PRODUCT", None, 0)])
        self.assertIn("SAME_TIME", [i["code"] for i in saved["issues"]])
        self.save(tid, [{"at_ms": 0, "action": "PIN_PRODUCT"}], 422)
        self.save(tid, [{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.foreign_product}], 422)
        self.save(tid, [{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": 999}], 422)
        self.save(tid, [{"at_ms": 12_501, "action": "PIN_PRODUCT", "product_id": self.ao}], 422)
        self.save(tid, [{"at_ms": 0, "action": "SWITCH_VIDEO"}], 422)
        self.assertEqual(len(self.client.get(f"{self.base}/{tid}").json()["markers"]), 4, "a refused save changes nothing")

    def test_ai_suggestions_must_be_reviewed_before_approval(self):
        tid = self.new()["id"]
        self.save(tid, [{"at_ms": 1_000, "action": "PIN_PRODUCT", "product_id": self.ao, "source": "AI",
                         "review_status": "NEEDS_REVIEW", "confidence": 0.4, "evidence": "{\"transcript\": \"áo thun\"}"}])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 409)
        self.save(tid, [{"at_ms": 1_000, "action": "PIN_PRODUCT", "product_id": self.ao, "source": "AI", "confidence": 0.4}])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        self.assertEqual(self.client.put(f"{self.base}/{tid}/markers", json={"markers": []}).status_code, 409, "approved is frozen")
        self.assertEqual(self.client.delete(f"{self.base}/{tid}").status_code, 409)

    def test_activation_switches_versions_and_derives_the_lineup(self):
        draft = self.new()["id"]
        self.assertEqual(self.client.post(f"{self.base}/{draft}/activate").status_code, 409, "a draft is not activated")
        v2 = self.approved_and_active([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.quan},
                                       {"at_ms": 5_000, "action": "PIN_PRODUCT", "product_id": self.ao},
                                       {"at_ms": 8_000, "action": "PIN_PRODUCT", "product_id": self.quan}])
        self.assertEqual((self.session()["active_timeline_id"], self.session()["product_ids"]), (v2, [self.quan, self.ao]))
        v3 = self.approved_and_active([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.vay}])
        statuses = {t["id"]: t["status"] for t in self.client.get(self.base).json()}
        self.assertEqual((statuses[v2], statuses[v3]), ("APPROVED", "ACTIVE"))
        self.assertEqual(self.session()["product_ids"], [self.vay])
        self.assertEqual(self.client.post(f"{self.base}/{v2}/activate").status_code, 200, "an older version can come back")
        self.assertEqual(self.session()["active_timeline_id"], v2)
        events = [e["event_type"] for e in self.client.get(f"/api/live/sessions/{self.sid}/events").json()]
        self.assertEqual(events.count("TIMELINE_ACTIVATED"), 3)

    def test_copy_of_the_active_version_is_a_new_draft(self):
        active = self.approved_and_active([{"at_ms": 2_000, "action": "PIN_PRODUCT", "product_id": self.ao}])
        copy = self.new(copy_from=active)
        self.assertEqual((copy["status"], [(m["at_ms"], m["product_id"]) for m in copy["markers"]]), ("DRAFT", [(2_000, self.ao)]))
        self.assertEqual(self.client.delete(f"{self.base}/{copy['id']}").status_code, 204)

    def test_a_product_removed_from_the_catalog_blocks_activation(self):
        tid = self.new()["id"]
        self.save(tid, [{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.vay}])
        self.client.post(f"{self.base}/{tid}/approve")
        with self.Session() as db:
            from app.catalog import Product
            db.delete(db.get(Product, self.vay))
            db.commit()
        detail = self.client.get(f"{self.base}/{tid}").json()
        self.assertIn("PRODUCT_MISSING", [i["code"] for i in detail["issues"]])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/activate").status_code, 409)

    def test_changing_the_main_video_deactivates_the_timeline_made_for_the_old_one(self):
        old = self.approved_and_active([{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao}])
        patched = self.client.patch(f"/api/live/sessions/{self.sid}", json={"main_media_id": self.other})
        self.assertEqual(patched.json()["active_timeline_id"], None)
        self.assertEqual(self.client.post(f"{self.base}/{old}/activate").status_code, 409, "made for another video")
        self.assertEqual(self.new()["media_id"], self.other)

    def test_duplicate_session_starts_from_the_timeline_that_ran(self):
        self.approved_and_active([{"at_ms": 4_000, "action": "PIN_PRODUCT", "product_id": self.quan}])
        copy = self.client.post(f"/api/live/sessions/{self.sid}/duplicate").json()
        timelines = self.client.get(f"/api/live/sessions/{copy['id']}/timelines").json()
        self.assertEqual([(t["version"], t["status"], t["marker_count"]) for t in timelines], [(1, "DRAFT", 1)])
        self.assertEqual(copy["active_timeline_id"], None)


if __name__ == "__main__":
    unittest.main()
