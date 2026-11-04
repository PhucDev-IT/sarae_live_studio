"""Overlay events of Single Video Timeline versions: building blocks instead of fixed types, normalized canvas,
DRAFT-only editing with the pin markers' lifecycle, owner isolation of products and images, and the snapshot a
renderer syncs."""
import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.live_overlays import busiest, image_size
from app.live_timeline import pin_spans
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


class PinSpanTests(unittest.TestCase):
    def test_pin_spans_match_the_shared_vectors(self):
        """Same vectors as web/src/overlay/overlaySchedule.ts pinSpans and the Agent's port."""
        path = Path(__file__).resolve().parents[2] / "agent-core" / "tests" / "fixtures" / "overlay_schedule_vectors.json"
        vectors = json.loads(path.read_text(encoding="utf-8"))["pins"]
        self.assertTrue(vectors)
        for case in vectors:
            with self.subTest(case["name"]):
                self.assertEqual(pin_spans(case["markers"], case["duration_ms"]), case["spans"])
                rows = [SimpleNamespace(**{"seq": 0, "id": None, **m}) for m in case["markers"]]  # database rows
                self.assertEqual(pin_spans(rows, case["duration_ms"]), case["spans"])


class OverlayApiTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.ao, self.quan = self.tiktok_products(self.owner_a, "Áo", "Quần")
        self.foreign_product, = self.tiktok_products(self.owner_b, "B", seller="6001")
        agent = self.agent = self.activate_agent(self.owner_a, "PC-A")
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

    def test_the_images_of_a_version_have_a_total_size_limit(self):
        from unittest.mock import patch
        tid = self.new()
        first, second = self.asset(), self.asset()
        self.put(tid, [self.image_overlay(first["id"]), self.image_overlay(second["id"], start=5000)])
        with patch("app.live_overlays.MAX_VERSION_ASSET_BYTES", len(png()) + 1):
            issues = self.client.get(f"{self.base}/{tid}/overlays").json()["issues"]
            self.assertIn("ASSETS_TOO_LARGE", [i["code"] for i in issues])
            self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 409)
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)

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

    def test_the_agent_fetches_the_active_snapshot_of_its_own_sessions_only(self):
        url, params = "/api/agent-media/overlay-snapshot", {"agent_id": self.agent["agent_id"], "session_id": self.sid}
        key = {"X-Sarae-Agent-Key": self.agent["agent_credential"]}
        self.assertIsNone(self.client.get(url, params=params, headers=key).json()["snapshot"])
        asset = self.asset()
        tid = self.new()
        self.put(tid, [self.image_overlay(asset["id"])])
        self.client.post(f"{self.base}/{tid}/approve")
        self.client.post(f"{self.base}/{tid}/activate")
        body = self.client.get(url, params=params, headers=key).json()
        snap = body["snapshot"]
        self.assertEqual((body["session_id"], body["agent_id"], snap["version"], len(snap["overlays"])),
                         (self.sid, self.agent["agent_id"], 1, 1))
        self.assertEqual(self.client.get(snap["assets"][asset["id"]]["url"], headers={}).content[:8], png()[:8],
                         "the signed URL alone downloads the image")
        self.assertEqual(snap["revision"], self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()["revision"])
        other = self.activate_agent(self.owner_b, "PC-B")
        foreign = self.client.get(url, params={"agent_id": other["agent_id"], "session_id": self.sid},
                                  headers={"X-Sarae-Agent-Key": other["agent_credential"]})
        self.assertEqual(foreign.status_code, 404)
        self.assertIn(self.client.get(url, params=params, headers={"X-Sarae-Agent-Key": "wrong"}).status_code, (401, 403))

    def test_templates_are_starting_points(self):
        found = self.client.get("/api/live/overlay-templates").json()
        self.assertEqual(found["canvas"], {"width": 1080, "height": 1920})
        self.assertEqual(found["blocks"], ["text", "product_field", "image", "shape"])
        self.assertEqual({t["template"] for t in found["templates"]} >= {"SIZE_CHART", "PRODUCT_INFO", "COLOR_OPTIONS",
                                                                        "DETAIL_IMAGE", "COMPARE_LOOKS", "PRODUCT_SPOTLIGHT"}, True)
        self.assertEqual(found["owner_templates"], [])

    def test_readiness_combines_the_version_and_what_the_agent_reported(self):
        url = f"/api/live/sessions/{self.sid}/overlay-readiness"
        report = self.client.get(url).json()
        codes = {c["code"]: c["ok"] for c in report["checks"]}
        self.assertEqual((report["ok"], codes["ACTIVE_VERSION"], codes["AGENT_ASSIGNED"]), (False, False, False))
        self.assertEqual(self.client.post(f"/api/live/sessions/{self.sid}/overlay-check").status_code, 409, "no Agent yet")
        self.assertEqual(self.client.get(url, headers=self.B).status_code, 404)
        tid = self.new()
        self.put(tid, [self.overlay()])
        self.client.post(f"{self.base}/{tid}/approve")
        self.client.post(f"{self.base}/{tid}/activate")
        url += f"?agent_id={self.agent['agent_id']}"  # before LIVE: the Agent that will play it
        revision = self.client.get(f"/api/live/sessions/{self.sid}/overlay-snapshot").json()["snapshot"]["revision"]
        self.heartbeat(self.agent, playback={"overlay": {"session_id": self.sid, "revision": revision, "state": "hidden", "error": None}})
        report = self.client.get(url).json()
        codes = {c["code"]: c["ok"] for c in report["checks"]}
        self.assertTrue(report["ok"], report)
        self.assertEqual((codes["ACTIVE_VERSION"], codes["OVERLAYS_VALID"], codes["AGENT_ONLINE"], codes["AGENT_SYNCED"]), (True,) * 4)
        self.assertEqual((report["active_version"], report["snapshot_revision"]), (1, revision))
        self.heartbeat(self.agent, playback={"overlay": {"session_id": self.sid, "revision": "old", "state": "error",
                                                         "error": {"code": "OVERLAY_CANVAS_NOT_9_16", "message": "Canvas OBS 1920×1080"}}})
        report = self.client.get(url).json()
        self.assertFalse(report["ok"])
        self.assertIn("Canvas OBS", " ".join(c["message"] for c in report["checks"]))

    def test_overlay_check_is_sent_to_the_session_agent(self):
        other = self.activate_agent(self.owner_b, "PC-B")
        self.assertEqual(self.client.post(f"/api/live/sessions/{self.sid}/overlay-check", json={"agent_id": other["agent_id"]}).status_code, 404,
                         "another owner's Agent")
        sent = self.client.post(f"/api/live/sessions/{self.sid}/overlay-check", json={"agent_id": self.agent["agent_id"]})
        self.assertEqual(sent.status_code, 202, sent.text)
        command = self.client.get(f"/api/agents/{sent.json()['agent_id']}/commands/{sent.json()['command_id']}").json()
        self.assertEqual((command["kind"], command["status"]), ("overlay_check", "PENDING"))

    def test_a_draft_can_be_previewed_on_obs_without_activating(self):
        tid = self.new()
        self.put(tid, [self.overlay()])
        url = f"{self.base}/{tid}/overlay-preview"
        self.assertEqual(self.client.post(url, json={"position_ms": 1500}).status_code, 409, "no Agent chosen")
        self.assertEqual(self.client.post(url, json={"agent_id": self.agent["agent_id"], "media_id": self.outside}).status_code, 422)
        sent = self.client.post(url, json={"agent_id": self.agent["agent_id"], "media_id": self.other, "position_ms": 1500, "playing": True})
        self.assertEqual(sent.status_code, 202, sent.text)
        command = self.client.get(f"/api/agents/{sent.json()['agent_id']}/commands/{sent.json()['command_id']}").json()
        self.assertEqual(command["kind"], "overlay_preview")
        stop = self.client.post(url, json={"agent_id": self.agent["agent_id"], "stop": True})
        self.assertEqual(stop.status_code, 202, stop.text)
        self.assertEqual(self.client.post(url, json={"agent_id": self.agent["agent_id"]}, headers=self.B).status_code, 404)
        # The Agent reads exactly that version, a draft included; never one of another session.
        key = {"X-Sarae-Agent-Key": self.agent["agent_credential"]}
        params = {"agent_id": self.agent["agent_id"], "session_id": self.sid}
        snap = self.client.get("/api/agent-media/overlay-snapshot", params={**params, "timeline_id": tid}, headers=key).json()["snapshot"]
        self.assertEqual((snap["timeline_id"], len(snap["overlays"])), (tid, 1))
        self.assertIsNone(self.client.get("/api/agent-media/overlay-snapshot", params=params, headers=key).json()["snapshot"], "nothing ACTIVE")
        other = self.client.post("/api/live/sessions", json={"name": "Other", "tiktok_account_id": self.account, "playback_mode": "SINGLE_VIDEO_TIMELINE",
                                                             "media_ids": [self.main]}).json()["id"]
        foreign = self.client.get("/api/agent-media/overlay-snapshot", params={"agent_id": self.agent["agent_id"], "session_id": other, "timeline_id": tid}, headers=key)
        self.assertEqual(foreign.status_code, 404)

    # ----- window / random schedules -----
    def put_schedule(self, tid, overlays, status=200, **schedule):
        saved = self.client.put(f"{self.base}/{tid}/overlays", json={"overlays": overlays, "schedule": schedule})
        self.assertEqual(saved.status_code, status, saved.text)
        return saved.json()

    def codes(self, view):
        return [i["code"] for i in view["issues"]]

    def test_the_schedule_defaults_to_fixed_is_saved_and_kept(self):
        tid = self.new()
        default = {"mode": "fixed", "every_ms": 120_000, "min_gap_ms": 20_000}
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays").json()["schedule"], default)
        self.assertEqual(self.put(tid, [self.overlay()])["overlays"][0]["show_ms"], 6000)
        saved = self.put_schedule(tid, [self.overlay(start=0, duration=12_000, show_ms=4000)], mode="window", every_ms=10_000, min_gap_ms=0)
        random = {"mode": "window", "every_ms": 10_000, "min_gap_ms": 0}
        self.assertEqual((saved["schedule"], saved["overlays"][0]["show_ms"]), (random, 4000))
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays").json()["schedule"], random)
        self.assertEqual(self.put(tid, [self.overlay(start=0, duration=12_000)])["schedule"], random,
                         "no schedule in the body keeps the stored one")
        for bad in ({"mode": "shuffle"}, {"every_ms": 9_999}, {"every_ms": 3_600_001}, {"min_gap_ms": -1},
                    {"min_gap_ms": 600_001}, {"extra": 1}):
            self.put_schedule(tid, [], status=422, **bad)
        self.put(tid, [self.overlay(show_ms=999)], status=422)
        self.put(tid, [self.overlay(show_ms=600_001)], status=422)
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200)
        self.put_schedule(tid, [self.overlay()], status=409, mode="fixed")
        copy = self.new(copy_from=tid)
        copied = self.client.get(f"{self.base}/{copy}/overlays").json()
        self.assertEqual((copied["schedule"], copied["overlays"][0]["show_ms"]), (random, 6000), "a new draft copies the schedule")
        self.put_schedule(copy, [], mode="fixed")
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays").json()["schedule"], random, "the source is unchanged")

    def test_deleting_versions_and_sessions_leaves_no_schedule(self):
        from app.live_overlays import OverlaySchedule
        tid = self.new()
        self.put_schedule(tid, [], mode="window")
        self.assertEqual(self.client.delete(f"{self.base}/{tid}").status_code, 204)
        other = self.new()
        self.put_schedule(other, [], mode="window")
        self.assertEqual(self.client.delete(f"/api/live/sessions/{self.sid}").status_code, 204)
        with self.Session() as db:
            self.assertEqual(db.query(OverlaySchedule).count(), 0)

    def test_the_snapshot_carries_the_schedule(self):
        tid = self.new()
        self.put(tid, [self.overlay(show_ms=2000)])
        snap = self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()
        self.assertEqual((snap["schedule"]["mode"], snap["overlays"][0]["show_ms"]), ("fixed", 2000))
        self.put_schedule(tid, [self.overlay(show_ms=2000)], mode="window", every_ms=10_000, min_gap_ms=0)
        changed = self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()
        self.assertEqual(changed["schedule"], {"mode": "window", "every_ms": 10_000, "min_gap_ms": 0})
        self.assertNotEqual(changed["revision"], snap["revision"], "the schedule is part of the revision")

    def test_window_issues(self):
        tid = self.new()
        window = dict(start=0, duration=12_000)
        fine = self.put_schedule(tid, [self.overlay(**window, show_ms=2000)], mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertEqual(fine["issues"], [])
        crowded = [self.overlay(start=1000 * i, duration=6000, show_ms=2000) for i in range(5)]
        self.assertNotIn("TOO_MANY_AT_ONCE", self.codes(self.put_schedule(tid, crowded, mode="window", every_ms=10_000, min_gap_ms=0)),
                         "one overlay at a time in window mode")
        found = self.put_schedule(tid, [self.overlay(start=0, duration=3000, show_ms=4000)], mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertIn("SHOW_LONGER_THAN_WINDOW", self.codes(found))
        found = self.put_schedule(tid, [self.overlay(**window, show_ms=11_000)], mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertIn("SHOW_LONGER_THAN_SLOT", self.codes(found))
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 409, "errors block approval")
        slow = self.overlay(**window, show_ms=1000, animation_in={"name": "fade", "duration_ms": 600},
                            animation_out={"name": "fade", "duration_ms": 600})
        self.assertIn("ANIMATION_LONGER_THAN_SHOW", self.codes(self.put_schedule(tid, [slow], mode="window", every_ms=10_000, min_gap_ms=0)))
        dense = self.put_schedule(tid, [self.overlay(**window, show_ms=2000)], mode="window", every_ms=10_000, min_gap_ms=9000)
        self.assertEqual([(i["code"], i["level"]) for i in dense["issues"]], [("DENSITY_TOO_HIGH", "warning")])
        # Slots of 10 s: 0–10 s and 10–20 s (up to the last window end of the video).
        first = self.overlay(start=0, duration=5000, show_ms=2000)
        fits = self.put_schedule(tid, [first, self.overlay(start=11_000, duration=1500, show_ms=1000)],
                                 mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertEqual(fits["issues"], [])
        disabled = self.put_schedule(tid, [first, self.overlay(start=9_000, duration=2500, show_ms=2000, enabled=False)],
                                     mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertEqual(disabled["issues"], [], "a disabled overlay does not extend the slots")
        holes = self.put_schedule(tid, [first, self.overlay(start=9_000, duration=2500, show_ms=2000)],
                                  mode="window", every_ms=10_000, min_gap_ms=0)
        self.assertEqual([(i["code"], i["media_id"], i["slots"], i["level"]) for i in holes["issues"]],
                         [("NO_OVERLAY_IN_SLOTS", self.main, 1, "warning")],
                         "9–11.5 s straddles the slot edge: neither 9–10 s nor 10–11.5 s holds a 2 s appearance")
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200, "warnings do not block approval")

    def put_markers(self, tid, markers):
        saved = self.client.put(f"{self.base}/{tid}/markers", json={"markers": markers})
        self.assertEqual(saved.status_code, 200, saved.text)

    def pin_markers(self, unpin_at=8000):
        """Main video: Áo pinned 2–`unpin_at` ms; other video: Quần pinned from 0 to its end."""
        return [{"at_ms": 2000, "action": "PIN_PRODUCT", "product_id": self.ao}, {"at_ms": unpin_at, "action": "UNPIN_PRODUCT"},
                {"media_id": self.other, "at_ms": 0, "action": "PIN_PRODUCT", "product_id": self.quan}]

    def generic(self, **extra):
        return self.overlay(product_id=None, elements=[{"kind": "text", "text": "Freeship"}], **extra)

    def test_the_three_modes_are_saved_and_read_back(self):
        tid = self.new()
        for mode in ("fixed", "window", "random", "fixed"):
            saved = self.put_schedule(tid, [self.overlay()], mode=mode, every_ms=10_000, min_gap_ms=0)
            self.assertEqual((saved["schedule"]["mode"], self.client.get(f"{self.base}/{tid}/overlays").json()["schedule"]["mode"]),
                             (mode, mode))
        self.put_schedule(tid, [], status=422, mode="shuffle")

    def test_the_snapshot_carries_pin_spans_and_durations(self):
        tid = self.new()
        self.put_markers(tid, self.pin_markers() + [{"media_id": self.other, "at_ms": 5000, "action": "PIN_PRODUCT",
                                                     "product_id": self.ao, "review_status": "NEEDS_REVIEW"}])
        self.put_schedule(tid, [self.overlay()], mode="random", every_ms=10_000, min_gap_ms=0)
        snap = self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()
        pins = {self.main: [{"start_ms": 2000, "end_ms": 8000, "product_id": self.ao}],
                self.other: [{"start_ms": 0, "end_ms": 12_500, "product_id": self.quan}]}
        self.assertEqual((snap["pins"], snap["durations"]), (pins, {self.main: 12_500, self.other: 12_500}),
                         "approved markers only; a pin lasts to the end of the video")
        view = self.client.get(f"{self.base}/{tid}/overlays").json()
        self.assertEqual((view["pins"], view["durations"]), (snap["pins"], snap["durations"]), "the editor view says the same")
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()["revision"], snap["revision"])
        self.put_markers(tid, self.pin_markers(unpin_at=9000))
        moved = self.client.get(f"{self.base}/{tid}/overlays/snapshot").json()
        self.assertEqual(moved["pins"][self.main][0]["end_ms"], 9000)
        self.assertNotEqual(moved["revision"], snap["revision"], "a moved marker changes what a random plan shows")

    def test_random_issues(self):
        tid = self.new()
        self.put_markers(tid, self.pin_markers())  # Áo pinned 2–8 s of the 12.5 s main video
        plan = dict(mode="random", every_ms=10_000, min_gap_ms=0)
        self.assertEqual(self.put_schedule(tid, [self.overlay(start=0, duration=1000, show_ms=2000)], **plan)["issues"], [],
                         "times are ignored: the product's pin span is the window")
        self.assertEqual(self.put_schedule(tid, [self.generic(show_ms=2000)], **plan)["issues"], [],
                         "an overlay without a product covers the whole video (slots 0–10 s and 10–12.5 s)")
        late = self.put_schedule(tid, [self.generic(show_ms=3000)], **plan)
        self.assertEqual([(i["code"], i["media_id"], i["slots"]) for i in late["issues"]], [("NO_OVERLAY_IN_SLOTS", self.main, 1)],
                         "10–12.5 s cannot hold 3 s")
        never = self.put_schedule(tid, [self.overlay(product_id=self.quan, show_ms=2000)], **plan)
        self.assertEqual([(i["code"], i["level"], i["overlay"]) for i in never["issues"]], [("PRODUCT_NEVER_PINNED", "warning", 0)],
                         "Quần is pinned in the other video only")
        short = self.put_schedule(tid, [self.overlay(show_ms=7000)], **plan)
        self.assertEqual([(i["code"], i["level"]) for i in short["issues"]],
                         [("PIN_TOO_SHORT", "warning"), ("NO_OVERLAY_IN_SLOTS", "warning")])
        self.assertIn("SHOW_LONGER_THAN_SLOT", self.codes(self.put_schedule(tid, [self.generic(show_ms=11_000)], **plan)))
        slow = self.overlay(show_ms=1000, animation_in={"name": "fade", "duration_ms": 600}, animation_out={"name": "fade", "duration_ms": 600})
        self.assertIn("ANIMATION_LONGER_THAN_SHOW", self.codes(self.put_schedule(tid, [slow], **plan)))
        dense = self.put_schedule(tid, [self.overlay(show_ms=2000)], mode="random", every_ms=10_000, min_gap_ms=9000)
        self.assertEqual(self.codes(dense), ["DENSITY_TOO_HIGH"])
        crowded = [self.overlay(start=1000 * i, duration=6000, show_ms=2000) for i in range(5)]
        self.assertEqual(self.put_schedule(tid, crowded, **plan)["issues"], [], "one overlay at a time: no TOO_MANY_AT_ONCE")
        self.assertEqual(self.client.post(f"{self.base}/{tid}/approve").status_code, 200, "warnings do not block approval")

    def test_random_mode_ignores_the_stored_times(self):
        from app.live_overlays import OverlayEvent, OverlaySchedule
        tid = self.new()
        self.put_markers(tid, self.pin_markers())
        self.put_schedule(tid, [self.overlay(start=10_000, duration=2_500, show_ms=2000)], mode="random", every_ms=10_000, min_gap_ms=0)
        self.put(tid, [self.overlay(start=10_000, duration=3_000, show_ms=2000)], status=422)  # still refused when saved
        with self.Session() as db:  # a time past the end (e.g. the video was replaced by a shorter one)
            db.query(OverlayEvent).update({"duration_ms": 3000})
            db.commit()
        self.assertEqual(self.client.get(f"{self.base}/{tid}/overlays").json()["issues"], [])
        with self.Session() as db:
            db.query(OverlaySchedule).update({"mode": "fixed"})
            db.commit()
        self.assertEqual(self.codes(self.client.get(f"{self.base}/{tid}/overlays").json()), ["AFTER_END"])

    def test_ensure_schema_turns_stored_random_schedules_into_window_once(self):
        from sqlalchemy import create_engine, inspect, text
        from app.live_overlays import ensure_schema
        from app.live_timeline import ensure_schema as timeline_schema
        engine = create_engine("sqlite://")
        timeline_schema(engine)
        with engine.begin() as conn:  # a database of 20261104_0022: "random" meant the window mode
            conn.execute(text("CREATE TABLE live_overlay_schedules (timeline_id INTEGER PRIMARY KEY, owner_id VARCHAR(64), "
                              "mode VARCHAR(10), every_ms INTEGER, min_gap_ms INTEGER, updated_at DATETIME)"))
            conn.execute(text("INSERT INTO live_overlay_schedules VALUES (1, 'o', 'random', 10000, 0, '2026-11-04'), "
                              "(2, 'o', 'fixed', 10000, 0, '2026-11-04')"))
        ensure_schema(engine)

        def modes():
            with engine.connect() as conn:
                return dict(conn.execute(text("SELECT timeline_id, mode FROM live_overlay_schedules")).all())
        self.assertEqual(modes(), {1: "window", 2: "fixed"})
        self.assertIn("mode_version", {c["name"] for c in inspect(engine).get_columns("live_overlay_schedules")})
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO live_overlay_schedules (timeline_id, owner_id, mode, every_ms, min_gap_ms, updated_at) "
                              "VALUES (3, 'o', 'random', 10000, 0, '2026-11-05')"))
        ensure_schema(engine)
        self.assertEqual(modes(), {1: "window", 2: "fixed", 3: "random"}, "the new random mode is never converted")
        engine.dispose()

    def test_the_preview_sends_the_iteration(self):
        tid = self.new()
        self.put(tid, [self.overlay()])
        url = f"{self.base}/{tid}/overlay-preview"
        self.assertEqual(self.client.post(url, json={"agent_id": self.agent["agent_id"], "iteration": -1}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"agent_id": self.agent["agent_id"], "iteration": 100_001}).status_code, 422)
        from unittest.mock import patch
        from app import cloud_agents
        for body, expected in (({}, 0), ({"iteration": 7}, 7)):
            with patch("app.cloud_agents.create_command", wraps=cloud_agents.create_command) as create:
                sent = self.client.post(url, json={"agent_id": self.agent["agent_id"], **body})
            self.assertEqual(sent.status_code, 202, sent.text)
            kind, payload = create.call_args.args[2:4]
            self.assertEqual((kind, payload["iteration"]), ("overlay_preview", expected))
            self.assertEqual(cloud_agents.validate_payload(kind, payload)["iteration"], expected, "the Agent channel accepts it")

    def test_ensure_schema_adds_show_ms_to_an_old_table(self):
        from sqlalchemy import create_engine, inspect, text
        from app.live_overlays import ensure_schema
        from app.live_timeline import ensure_schema as timeline_schema
        engine = create_engine("sqlite://")
        timeline_schema(engine)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE live_overlay_events (id INTEGER PRIMARY KEY, timeline_id INTEGER, media_id VARCHAR(32))"))
            conn.execute(text("INSERT INTO live_overlay_events (id, timeline_id, media_id) VALUES (1, 1, 'x')"))
        ensure_schema(engine)
        ensure_schema(engine)  # idempotent
        self.assertIn("show_ms", {c["name"] for c in inspect(engine).get_columns("live_overlay_events")})
        self.assertIn("live_overlay_schedules", inspect(engine).get_table_names())
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT show_ms FROM live_overlay_events")).scalar(), 6000)
        engine.dispose()

    def template_body(self, **extra):
        overlay = self.overlay()
        body = {"name": "Giá vàng", "template": "MY_PRICE", "layout": overlay["layout"], "elements": overlay["elements"],
                "animation_in": {"name": "slide_up", "duration_ms": 400}, "duration_ms": 6000}
        body.update(extra)
        return body

    def test_owner_templates_are_saved_reused_and_isolated(self):
        created = self.client.post("/api/live/overlay-templates", json=self.template_body())
        self.assertEqual(created.status_code, 201, created.text)
        mine = created.json()
        self.assertEqual((mine["name"], mine["template"], mine["duration_ms"], mine["animation_in"]["name"]), ("Giá vàng", "MY_PRICE", 6000, "slide_up"))
        listed = self.client.get("/api/live/overlay-templates").json()["owner_templates"]
        self.assertEqual([t["id"] for t in listed], [mine["id"]])
        self.assertEqual(self.client.get("/api/live/overlay-templates", headers=self.B).json()["owner_templates"], [],
                         "another owner never sees it")
        for method, path in (("put", f"/api/live/overlay-templates/{mine['id']}"), ("delete", f"/api/live/overlay-templates/{mine['id']}"),
                             ("post", f"/api/live/overlay-templates/{mine['id']}/duplicate")):
            kwargs = {"json": self.template_body()} if method == "put" else {}
            self.assertEqual(getattr(self.client, method)(path, headers=self.B, **kwargs).status_code, 404)
        renamed = self.client.put(f"/api/live/overlay-templates/{mine['id']}", json=self.template_body(name="Giá nổi bật"))
        self.assertEqual(renamed.json()["name"], "Giá nổi bật")
        copy = self.client.post(f"/api/live/overlay-templates/{mine['id']}/duplicate")
        self.assertEqual((copy.status_code, copy.json()["name"]), (201, "Giá nổi bật (bản sao)"))
        self.assertEqual(self.client.delete(f"/api/live/overlay-templates/{mine['id']}").status_code, 204)
        self.assertEqual([t["id"] for t in self.client.get("/api/live/overlay-templates").json()["owner_templates"]], [copy.json()["id"]])

    def test_templates_are_not_bound_to_a_product_and_only_use_the_owners_images(self):
        bound = self.template_body(elements=[{"kind": "image", "source": "product", "product_id": self.ao}])
        saved = self.client.post("/api/live/overlay-templates", json=bound).json()
        self.assertIsNone(saved["elements"][0]["product_id"], "product blocks read the overlay's product")
        theirs = self.asset(headers=self.B)
        foreign = self.template_body(elements=[{"kind": "image", "source": "asset", "asset_id": theirs["id"]}])
        self.assertEqual(self.client.post("/api/live/overlay-templates", json=foreign).status_code, 422)
        mine = self.asset()
        with_image = self.client.post("/api/live/overlay-templates", json=self.template_body(
            elements=[{"kind": "image", "source": "asset", "asset_id": mine["id"]}])).json()
        self.client.delete(f"/api/live/overlay-assets/{mine['id']}")
        listed = {t["id"]: t for t in self.client.get("/api/live/overlay-templates").json()["owner_templates"]}
        self.assertEqual(listed[with_image["id"]]["missing_assets"], [mine["id"]])
        self.assertEqual(self.client.post("/api/live/overlay-templates", json=self.template_body(
            duration_ms=500, animation_in={"name": "fade", "duration_ms": 400}, animation_out={"name": "fade", "duration_ms": 400})).status_code, 422)


if __name__ == "__main__":
    unittest.main()
