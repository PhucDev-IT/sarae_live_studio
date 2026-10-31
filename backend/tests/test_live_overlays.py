"""Overlay events of Single Video Timeline versions: building blocks instead of fixed types, normalized canvas,
DRAFT-only editing with the pin markers' lifecycle, owner isolation of products and images, and the snapshot a
renderer syncs."""
import struct
import unittest
from types import SimpleNamespace

from app.live_overlays import busiest, image_size
from support import SaasTestCase


def png(width=200, height=100):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\x00" * 20


def jpeg(width=320, height=240):
    return b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" \
        + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01" + b"\x00" * 10


def webp(width=640, height=360):
    return b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x0a\x00\x00\x00" + b"\x00\x00\x00\x00" \
        + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little") + b"\x00" * 10


class ImageHeaderTests(unittest.TestCase):
    def test_sizes_are_read_from_the_content(self):
        self.assertEqual(image_size("image/png", png(200, 100)), (200, 100))
        self.assertEqual(image_size("image/jpeg", jpeg(320, 240)), (320, 240))
        self.assertEqual(image_size("image/webp", webp(640, 360)), (640, 360))
        self.assertIsNone(image_size("image/png", jpeg()))
        self.assertIsNone(image_size("image/webp", b"RIFF\x00\x00\x00\x00AVI LIST"))

    def test_overlays_ending_where_the_next_starts_do_not_overlap(self):
        e = lambda start, duration: SimpleNamespace(start_ms=start, duration_ms=duration)
        self.assertEqual(busiest([e(0, 1000), e(1000, 1000), e(2000, 1000)]), (1, 0))
        self.assertEqual(busiest([e(0, 5000), e(1000, 1000), e(1500, 3000)]), (3, 1500))


class OverlayApiTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.ao, self.quan = self.tiktok_products(self.owner_a, "Áo", "Quần")
        self.foreign_product, = self.tiktok_products(self.owner_b, "B", seller="6001")
        agent = self.activate_agent(self.owner_a, "PC-A")
        self.account = self.console_sync(agent, product_ids=(1, 2))
        self.main, self.other, self.outside = self.library_videos(self.owner_a, "main.mp4", "other.mp4", "outside.mp4")  # 12.5 s each
        created = self.client.post("/api/live/sessions", json={"name": "Single", "tiktok_account_id": self.account,
                                                               "playback_mode": "SINGLE_VIDEO_TIMELINE",
                                                               "media_ids": [self.main, self.other]})
        self.assertEqual(created.status_code, 201, created.text)
        self.sid = created.json()["id"]
        self.base = f"/api/live/sessions/{self.sid}/timelines"

    # ----- helpers -----
    def new(self, **body):
        created = self.client.post(self.base, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["id"]

    def overlay(self, start=1000, duration=3000, **extra):
        body = {"template": "PRODUCT_INFO", "start_ms": start, "duration_ms": duration, "product_id": self.ao,
                "layout": {"x": 0.05, "y": 0.7, "width": 0.9, "height": 0.15},
                "elements": [{"kind": "shape", "fill": "#000000A0"},
                             {"kind": "product_field", "field": "price", "suffix": "đ",
                              "frame": {"x": 0.05, "y": 0.5, "width": 0.9, "height": 0.4}}]}
        body.update(extra)
        return body

    def put(self, tid, overlays, status=200, headers=None):
        saved = self.client.put(f"{self.base}/{tid}/overlays", json={"overlays": overlays}, headers=headers or self.A)
        self.assertEqual(saved.status_code, status, saved.text)
        return saved.json()

    def upload(self, data, mime="image/png", name="size.png", headers=None):
        return self.client.post("/api/live/overlay-assets", files={"file": (name, data, mime)}, headers=headers or self.A)

    def asset(self, headers=None):
        uploaded = self.upload(png(), headers=headers)
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        return uploaded.json()

    def image_overlay(self, asset_id, **extra):
        return self.overlay(product_id=None, elements=[{"kind": "image", "source": "asset", "asset_id": asset_id}], **extra)

    # ----- CRUD and versions -----
    def test_save_and_read_the_overlays_of_a_draft(self):
        tid = self.new()
        saved = self.put(tid, [self.overlay(), self.overlay(start=5000, media_id=self.other, template="MY_OWN_CARD",
                                                             elements=[{"kind": "text", "text": "Freeship"}])])
        self.assertEqual(saved["canvas"], {"width": 1080, "height": 1920})
        first, second = saved["overlays"]
        self.assertEqual((first["media_id"], first["template"], first["layout"]["width"], first["animation_in"]),
                         (self.main, "PRODUCT_INFO", 0.9, {"name": "fade", "duration_ms": 300}), "media defaults to the first video")
        self.assertEqual((second["media_id"], second["template"], second["elements"][0]["kind"]), (self.other, "MY_OWN_CARD", "text"),
                         "any template label: overlays are made of blocks, not of fixed types")
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays").json()["overlays"], saved["overlays"])
        self.assertEqual(self.client.get(f"{self.base}/{tid}").json()["overlay_count"], 2)

    def test_overlays_and_pin_markers_of_a_version_do_not_touch_each_other(self):
        tid = self.new()
        self.client.put(f"{self.base}/{tid}/markers", json={"markers": [{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao}]})
        self.put(tid, [self.overlay()])
        self.assertEqual(self.client.put(f"{self.base}/{tid}/markers", json={"markers": [
            {"at_ms": 2000, "action": "PIN_PRODUCT", "product_id": self.quan}]}).status_code, 200)
        detail = self.client.get(f"{self.base}/{tid}").json()
        self.assertEqual(([m["product_id"] for m in detail["markers"]], detail["overlay_count"]), ([self.quan], 1))
        self.put(tid, [])
        self.assertEqual(len(self.client.get(f"{self.base}/{tid}").json()["markers"]), 1)

    def test_only_a_draft_is_edited_and_a_new_version_copies_the_overlays(self):
        tid = self.new()
        self.put(tid, [self.overlay()])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        self.put(tid, [], status=409)
        activated = self.client.post(f"{self.base}/{tid}/activate")
        self.assertEqual(activated.status_code, 200, activated.text)
        self.put(tid, [], status=409)
        copy = self.new(copy_from=tid)
        overlays = self.client.get(f"{self.base}/{copy}/overlays").json()
        self.assertEqual((overlays["status"], len(overlays["overlays"]), overlays["overlays"][0]["product_id"]), ("DRAFT", 1, self.ao))
        self.put(copy, [])
        self.assertEqual(len(self.client.get(f"{self.base}/{tid}/overlays").json()["overlays"]), 1, "the active one is unchanged")
        self.assertEqual(self.client.delete(f"{self.base}/{copy}").status_code, 204)

    # ----- validation -----
    def test_times_must_fit_the_video(self):
        tid = self.new()
        self.put(tid, [self.overlay(start=10_000, duration=2_500)])
        self.put(tid, [self.overlay(start=10_000, duration=3_000)], status=422)  # ends after 12.5 s
        self.put(tid, [self.overlay(duration=499)], status=422)
        self.put(tid, [self.overlay(start=-1)], status=422)
        self.put(tid, [self.overlay(duration=1000, animation_in={"name": "fade", "duration_ms": 600},
                                    animation_out={"name": "zoom", "duration_ms": 600})], status=422)
        self.put(tid, [self.overlay(animation_in={"name": "spin", "duration_ms": 300})], status=422)

    def test_boxes_must_stay_inside_the_canvas(self):
        tid = self.new()
        self.put(tid, [self.overlay(layout={"x": 0.5, "y": 0, "width": 0.6, "height": 0.1})], status=422)
        self.put(tid, [self.overlay(layout={"x": 0, "y": 0.95, "width": 1, "height": 0.1})], status=422)
        self.put(tid, [self.overlay(layout={"x": 0, "y": 0, "width": 0, "height": 0.1})], status=422)
        self.put(tid, [self.overlay(layout={"x": 0, "y": 0, "width": 1, "height": 1, "opacity": 1.5})], status=422)
        self.put(tid, [self.overlay(elements=[{"kind": "text", "text": "x", "frame": {"x": 0.5, "y": 0, "width": 0.6, "height": 1}}])], status=422)
        self.put(tid, [self.overlay(elements=[{"kind": "video", "src": "https://evil.example/x.mp4"}])], status=422)
        self.put(tid, [self.overlay(elements=[{"kind": "image", "source": "url", "url": "https://evil.example/x.png"}])], status=422)
        self.put(tid, [self.overlay(layout={"x": 0, "y": 0, "width": 1, "height": 1})])

    def test_overlays_belong_to_a_video_of_the_session(self):
        tid = self.new()
        self.put(tid, [self.overlay(media_id=self.outside)], status=422)

    def test_product_blocks_need_a_product_of_the_owner(self):
        tid = self.new()
        self.put(tid, [self.overlay(product_id=None)], status=422)
        self.put(tid, [self.overlay(product_id=self.foreign_product)], status=422)
        self.put(tid, [self.overlay(product_id=None, elements=[{"kind": "image", "source": "product", "product_id": self.foreign_product}])], status=422)
        self.put(tid, [self.overlay(product_id=None, elements=[{"kind": "image", "source": "product", "product_id": self.quan}])])

    def test_too_many_overlays_at_once_block_approval_but_not_saving(self):
        tid = self.new()
        crowded = self.put(tid, [self.overlay(start=1000 * i, duration=6000) for i in range(5)])
        self.assertIn("TOO_MANY_AT_ONCE", [i["code"] for i in crowded["issues"]])
        self.assertIn("OVERLAY_ERRORS", [i["code"] for i in self.client.get(f"{self.base}/{tid}").json()["issues"]])
        refused = self.client.post(f"{self.base}/{tid}/approve")
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Overlay", refused.json()["detail"])
        disabled = [self.overlay(start=1000 * i, duration=6000, enabled=i < 4) for i in range(5)]
        self.assertEqual(self.put(tid, disabled)["issues"], [], "a disabled overlay does not count")
        other_video = [self.overlay(start=1000 * i, duration=6000, media_id=self.main if i < 3 else self.other) for i in range(5)]
        self.assertEqual(self.put(tid, other_video)["issues"], [], "counted per video")
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)

    # ----- owner isolation -----
    def test_another_owner_sees_nothing(self):
        tid = self.new()
        self.put(tid, [self.overlay()])
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays", headers=self.B).status_code, 404)
        self.put(tid, [], status=404, headers=self.B)
        self.assertEqual(self.client.get(f"/api/live/sessions/{self.sid}/overlay-snapshot", headers=self.B).status_code, 404)
        self.assertEqual(len(self.client.get(f"{self.base}/{tid}/overlays").json()["overlays"]), 1)

    # ----- assets -----
    def test_assets_are_checked_images(self):
        self.assertEqual(self.upload(png(), mime="image/svg+xml", name="x.svg").status_code, 415)
        self.assertEqual(self.upload(b"<html>", mime="text/html").status_code, 415)
        self.assertEqual(self.upload(jpeg(), mime="image/png").status_code, 422, "content must match the declared type")
        self.assertEqual(self.upload(b"\x89PNG\r\n\x1a\nnot an image").status_code, 422)
        self.assertEqual(self.upload(png(5000, 100)).status_code, 422)
        self.assertEqual(self.upload(png() + b"\x00" * (5 * 1024 * 1024)).status_code, 413)
        for data, mime, size in ((png(200, 100), "image/png", (200, 100)), (jpeg(320, 240), "image/jpeg", (320, 240)),
                                 (webp(640, 360), "image/webp", (640, 360))):
            uploaded = self.upload(data, mime=mime)
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertEqual((uploaded.json()["width"], uploaded.json()["height"], uploaded.json()["mime_type"]), (*size, mime))
        self.assertEqual(len(self.client.get("/api/live/overlay-assets").json()), 3)
        self.assertEqual(self.client.get("/api/live/overlay-assets", headers=self.B).json(), [])

    def test_assets_are_served_only_through_a_url_signed_for_their_owner(self):
        asset = self.asset()
        served = self.client.get(asset["url"], headers={})
        self.assertEqual((served.status_code, served.content, served.headers["content-type"], served.headers["x-content-type-options"]),
                         (200, png(), "image/png", "nosniff"))
        self.assertEqual(self.client.get(f"/api/live/overlay-assets/{asset['id']}/file").status_code, 403)
        self.assertEqual(self.client.get(asset["url"].replace("sig=", "sig=x")).status_code, 403)
        theirs = self.asset(headers=self.B)
        forged = asset["url"].replace(asset["id"], theirs["id"])
        self.assertEqual(self.client.get(forged).status_code, 403, "a URL signed for A never opens B's image")

    def test_overlays_only_use_the_owners_assets(self):
        tid = self.new()
        theirs = self.asset(headers=self.B)
        self.put(tid, [self.image_overlay(theirs["id"])], status=422)
        self.put(tid, [self.image_overlay("0" * 32)], status=422)
        self.put(tid, [self.image_overlay(self.asset()["id"])])

    def test_an_asset_in_use_by_an_approved_version_cannot_be_deleted(self):
        asset, spare = self.asset(), self.asset()
        self.assertEqual(self.client.delete(f"/api/live/overlay-assets/{asset['id']}", headers=self.B).status_code, 404)
        tid = self.new()
        self.put(tid, [self.image_overlay(asset["id"])])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/live/overlay-assets/{asset['id']}").status_code, 409)
        draft = self.new()
        self.put(draft, [self.image_overlay(spare["id"])])
        self.assertEqual(self.client.delete(f"/api/live/overlay-assets/{spare['id']}").status_code, 204)
        self.assertEqual(self.client.get(spare["url"]).status_code, 410)
        issues = self.client.get(f"{self.base}/{draft}/overlays").json()["issues"]
        self.assertEqual([i["code"] for i in issues], ["ASSET_MISSING"])
        self.assertEqual(self.client.post(f"{self.base}/{draft}/approve").status_code, 409)

    # ----- snapshot -----
    def test_the_active_snapshot_holds_what_a_renderer_needs(self):
        self.assertIsNone(self.client.get(f"/api/live/sessions/{self.sid}/overlay-snapshot").json()["snapshot"])
        asset = self.asset()
        tid = self.new()
        self.put(tid, [self.overlay(), self.image_overlay(asset["id"], start=5000), self.overlay(start=8000, enabled=False)])
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        self.assertEqual(self.client.post(f"{self.base}/{tid}/activate").status_code, 200)
        snap = self.client.get(f"/api/live/sessions/{self.sid}/overlay-snapshot").json()["snapshot"]
        self.assertEqual((snap["timeline_id"], snap["version"], len(snap["overlays"])), (tid, 1, 2), "disabled overlays are left out")
        self.assertEqual(set(snap["products"]), {str(self.ao)})
        self.assertEqual(snap["assets"][asset["id"]]["sha256"], asset["sha256"])
        self.assertTrue(snap["assets"][asset["id"]]["url"].startswith(f"/api/live/overlay-assets/{asset['id']}/file?exp="))
        again = self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()
        self.assertEqual(again["revision"], snap["revision"], "the revision ignores signed URLs")
        copy = self.new(copy_from=tid)
        self.put(copy, [self.overlay(start=2000)])
        self.assertNotEqual(self.client.get(f"{self.base}/{copy}/overlays/snapshot").json()["revision"], snap["revision"])

    def test_agent_commands_keep_their_shape(self):
        tid = self.new()
        self.client.put(f"{self.base}/{tid}/markers", json={"markers": [{"at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.ao}]})
        self.put(tid, [self.overlay()])
        self.client.post(f"{self.base}/{tid}/approve")
        self.client.post(f"{self.base}/{tid}/activate")
        from app.database import SessionLocal
        from app.live_sessions import LiveSession
        from app.live_timeline import agent_timeline
        with self.Session() as db:
            timeline = agent_timeline(db, db.get(LiveSession, self.sid))
        self.assertEqual(set(timeline), {"version", "expected_user_id", "markers", "products"}, "older Agents refuse unknown fields")

    def test_templates_are_starting_points(self):
        found = self.client.get("/api/live/overlay-templates").json()
        self.assertEqual(found["canvas"], {"width": 1080, "height": 1920})
        self.assertEqual(found["blocks"], ["text", "product_field", "image", "shape"])
        self.assertIn("SIZE_CHART", [t["template"] for t in found["templates"]])


if __name__ == "__main__":
    unittest.main()
