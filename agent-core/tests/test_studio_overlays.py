"""LIVE overlays kept on library videos: validation, images, snapshot for the Agent's OverlayService."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import http.client

import pytest

from fake_obs import FakeWs
from sarae_agent.studio.live import StudioLive
from sarae_agent.studio.overlays import image_info, templates
from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.transport import StudioHTTPServer, StudioTransport
from studio_helpers import create_product

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (320).to_bytes(4, "big") + (180).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"rest"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("SARAE_AGENT_HOME", str(tmp_path / "agent-must-not-be-used"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


@pytest.fixture
def clip(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required to generate and validate real video")
    path = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=32x32:r=5:d=10", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=60)
    return path


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    yield instance
    instance.close()


def video(studio, clip):
    return studio.dispatch("studio.media.import", {"source_path": str(clip)})["media"]


def overlay(start_ms=1000, duration_ms=3000, product=None, **changes):
    item = {"template": "PRODUCT_INFO", "label": "Giá", "start_ms": start_ms, "duration_ms": duration_ms,
            "product_id": product["id"] if product else None,
            "layout": {"x": 0.05, "y": 0.7, "width": 0.9, "height": 0.12, "z_index": 1, "opacity": 1},
            "elements": [{"kind": "shape", "fill": "#000000A0", "radius": 0.2},
                         ({"kind": "product_field", "field": "price", "suffix": "đ"} if product
                          else {"kind": "text", "text": "Freeship"})]}
    item.update(changes)
    return item


def save(studio, media_id, *items):
    return studio.dispatch("studio.media.overlays.set", {"media_id": media_id, "overlays": list(items)})


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_templates_are_the_dev_presets():
    names = [t["template"] for t in templates()["templates"]]
    assert {"PRODUCT_INFO", "PRICE_TAG", "SIZE_CHART", "CAPTION"} <= set(names)
    assert templates()["canvas"] == {"width": 1080, "height": 1920}


def test_overlays_are_saved_per_video_with_their_scene(studio, clip):
    product = create_product(studio, "Áo linen")
    media = video(studio, clip)
    result = save(studio, media["id"], overlay(product=product), overlay(5000, 2000, label="Freeship"))
    assert [o["label"] for o in result["overlays"]] == ["Giá", "Freeship"]
    assert result["issues"] == []
    scene = result["scene"]
    assert scene["products"][product["id"]]["name"] == "Áo linen" and len(scene["overlays"]) == 2
    assert all(o["media_id"] == media["id"] for o in scene["overlays"])
    listed = studio.dispatch("studio.media.overlays.list", {"media_id": media["id"]})
    assert listed["overlays"] == result["overlays"]


def test_custom_display_times_survive_saving_and_reopening_studio(studio, clip):
    media = video(studio, clip)
    save(studio, media["id"], overlay(start_ms=1200, duration_ms=6300, show_ms=2300))
    root = studio.data_dir
    studio.close()
    reopened = StudioService(data_dir=root)
    try:
        result = reopened.dispatch("studio.media.overlays.list", {"media_id": media["id"]})
        saved = result["overlays"][0]
        assert (saved["start_ms"], saved["duration_ms"], saved["show_ms"]) == (1200, 6300, 2300)
        rendered = result["scene"]["overlays"][0]
        assert (rendered["start_ms"], rendered["duration_ms"], rendered["show_ms"]) == (1200, 6300, 2300)
    finally:
        reopened.close()


@pytest.mark.parametrize("duration_seconds", [3601, 10_800.125, 108_000])
def test_overlay_can_cover_an_entire_long_video(studio, clip, duration_seconds):
    media = video(studio, clip)
    # Exercise long-video metadata without generating hours of video for the test.
    with studio._storage.transaction() as connection:
        connection.execute("UPDATE media SET duration_seconds = ? WHERE id = ?", (duration_seconds, media["id"]))
    duration_ms = int(duration_seconds * 1000)
    result = save(studio, media["id"], overlay(start_ms=0, duration_ms=duration_ms))
    assert result["overlays"][0]["duration_ms"] == duration_ms
    assert result["issues"] == []
    listed = studio.dispatch("studio.media.overlays.list", {"media_id": media["id"]})
    assert listed["overlays"][0]["duration_ms"] == duration_ms
    assert studio.overlay_snapshot([media["id"]])["overlays"][0]["duration_ms"] == duration_ms
    # A late start is valid too, including starts after the old 24-hour boundary.
    start_ms = max(0, duration_ms - 900_000)
    save(studio, media["id"], overlay(start_ms=start_ms, duration_ms=duration_ms - start_ms))
    assert error_code(lambda: save(studio, media["id"], overlay(start_ms=0, duration_ms=duration_ms + 1000))) == "VALIDATION_ERROR"


@pytest.mark.parametrize("bad", [
    {"start_ms": 9000, "duration_ms": 3000},                        # after the end of the 10 s video
    {"duration_ms": 100},                                           # shorter than 0.5 s
    {"layout": {"x": 0.5, "y": 0, "width": 0.8, "height": 0.1}},    # outside the canvas
    {"elements": []},
    {"elements": [{"kind": "product_field", "field": "price"}]},     # product block without a product
    {"elements": [{"kind": "image", "source": "asset", "asset_id": "f" * 32}]},  # unknown image
    {"animation_in": {"name": "spin", "duration_ms": 300}},
    {"script": "alert(1)"},
])
def test_invalid_overlays_are_rejected(studio, clip, bad):
    media = video(studio, clip)
    code = error_code(lambda: save(studio, media["id"], overlay(**bad)))
    assert code in {"VALIDATION_ERROR", "NOT_FOUND"}
    assert studio.dispatch("studio.media.overlays.list", {"media_id": media["id"]})["overlays"] == []


def test_issues_report_crowding_and_deleted_products(studio, clip):
    product = create_product(studio, "Áo")
    media = video(studio, clip)
    crowded = [overlay(1000, 3000, label=str(i)) for i in range(5)]
    assert [i["code"] for i in save(studio, media["id"], *crowded)["issues"]] == ["TOO_MANY_AT_ONCE"]
    save(studio, media["id"], overlay(product=product))
    studio.dispatch("studio.products.delete", {"id": product["id"]})
    issues = studio.dispatch("studio.media.overlays.list", {"media_id": media["id"]})["issues"]
    assert [i["code"] for i in issues] == ["PRODUCT_MISSING"]


def test_overlay_images_are_checked_stored_and_protected(studio, clip):
    assert image_info(PNG) == ("image/png", 320, 180)
    asset = studio.add_overlay_asset(PNG, "bang-size.png")
    assert (asset["mime_type"], asset["width"], asset["height"]) == ("image/png", 320, 180)
    for data in (b"GIF89a....", b"<svg/>", PNG[:16] + (5000).to_bytes(4, "big") + PNG[20:]):
        assert error_code(lambda: studio.add_overlay_asset(data, "x.png")) == "VALIDATION_ERROR"
    media = video(studio, clip)
    image = overlay(elements=[{"kind": "image", "source": "asset", "asset_id": asset["id"]}])
    scene = save(studio, media["id"], image)["scene"]
    assert scene["assets"][asset["id"]]["url"] == f"/api/studio-assets/{asset['id']}"
    assert studio.overlay_image(scene["assets"][asset["id"]]["url"]) == PNG
    assert error_code(lambda: studio.dispatch("studio.overlays.assets.delete", {"id": asset["id"]})) == "ASSET_IN_USE"
    save(studio, media["id"])
    studio.dispatch("studio.overlays.assets.delete", {"id": asset["id"]})
    assert studio.dispatch("studio.overlays.assets.list", {})["assets"] == []
    assert error_code(lambda: studio.overlay_image("/api/../studio.sqlite3")) == "NOT_FOUND"


def test_snapshot_holds_enabled_overlays_and_changes_revision(studio, clip):
    first, second = video(studio, clip), video(studio, clip)
    save(studio, first["id"], overlay(), overlay(4000, 2000, enabled=False))
    snapshot = studio.overlay_snapshot([first["id"], second["id"]])
    assert len(snapshot["overlays"]) == 1 and snapshot["version"] == 1
    save(studio, second["id"], overlay(2000, 2000))
    changed = studio.overlay_snapshot([first["id"], second["id"]])
    assert changed["revision"] != snapshot["revision"] and len(changed["overlays"]) == 2
    assert studio.overlay_snapshot([str(second["id"])]) is not None
    save(studio, first["id"])
    save(studio, second["id"])
    assert studio.overlay_snapshot([first["id"], second["id"]]) is None


def test_live_feeds_the_agent_overlay_service(tmp_path, clip):
    service = StudioService(data_dir=tmp_path / "studio")
    live = StudioLive(service, obs_client_factory=lambda cfg, password: FakeWs())
    try:
        overlay_config = live.config.get().overlay
        assert (overlay_config.port, overlay_config.source_name) == (17893, "Sarae Studio Overlay")
        media = video(service, clip)
        save(service, media["id"], overlay())
        session = live.sessions_create({"title": "Phiên", "video_ids": [media["id"]]})["session"]
        live.start({"session_id": session["id"]})
        snapshot = live._overlay_snapshot(session["id"])
        assert snapshot["overlays"][0]["media_id"] == media["id"]
        live._on_position({"session_id": session["id"], "media_id": media["id"], "position_ms": 1500,
                           "iteration": 0, "media_state": "OBS_MEDIA_STATE_PLAYING"})
        assert live.overlay.clock["position_ms"] == 1500
        assert live.status()["overlay"]["source_name"] == "Sarae Studio Overlay"
        live.stop()
        assert live.overlay.clock is None
    finally:
        live.close()
        service.close()


def test_editor_scene_and_images_are_served_with_the_session_token(tmp_path, clip):
    service = StudioService(data_dir=tmp_path / "studio")
    transport = StudioTransport(service)
    server = StudioHTTPServer(transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asset = service.add_overlay_asset(PNG, "a.png")
        media = video(service, clip)
        result = transport.dispatch("studio.media.overlays.set", {"media_id": media["id"], "overlays": [
            overlay(elements=[{"kind": "image", "source": "asset", "asset_id": asset["id"]}])]})
        url = result["scene"]["assets"][asset["id"]]["url"]
        assert url.endswith(f"?token={transport.token}") and result["scene"]["api_base"] == transport.base_url

        def get(path):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", path)
            response = connection.getresponse()
            data = response.status, response.read()
            connection.close()
            return data
        assert get(url) == (200, PNG)
        assert get(url.split("?")[0])[0] == 401
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/studio/overlay-assets?filename=b.png", PNG,
                           {"Authorization": f"Bearer {transport.token}", "Content-Type": "application/octet-stream"})
        uploaded = json.loads(connection.getresponse().read())
        connection.close()
        assert uploaded["ok"] and uploaded["result"]["asset"]["width"] == 320
    finally:
        server.shutdown()
        server.server_close()
        service.close()
