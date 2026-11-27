"""LIVE sessions, Single Video Timeline: videos played through OBS, products pinned at each video's markers.

Fake obs-websocket and real MP4 files; pins run in rehearsal (the Agent's dry-run executor) unless a test says so.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from concurrent.futures import Future
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fake_obs import FakeWs
from sarae_agent.studio.live import StudioLive
from sarae_agent.studio.service import StudioError, StudioService
from studio_helpers import create_product


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
                    "color=c=green:s=32x32:r=5:d=10", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=60)
    return path


class Rig:
    def __init__(self, root, ws=None):
        self.ws = ws or FakeWs()
        self.events = []
        self.service = StudioService(data_dir=root)
        self.live = StudioLive(self.service, emit=lambda name, data: self.events.append(name),
                               obs_client_factory=lambda cfg, password: self.ws)

    def video(self, clip, **params):
        return self.service.dispatch("studio.media.import", {"source_path": str(clip), **params})["media"]

    def markers(self, media_id, *markers):
        return self.service.dispatch("studio.media.markers.set", {"media_id": media_id, "markers": list(markers)})["markers"]

    def close(self):
        self.live.close()
        self.service.close()


@pytest.fixture
def rig(tmp_path):
    instance = Rig(tmp_path / "studio")
    yield instance
    instance.close()


def pin(at_ms, product):
    return {"at_ms": at_ms, "action": "PIN_PRODUCT", "product_id": product["id"]}


def unpin(at_ms):
    return {"at_ms": at_ms, "action": "UNPIN_PRODUCT"}


def at(rig, media_id, position_ms, iteration=0):
    """One engine watchdog report: the video position drives the pins."""
    rig.live._on_position({"session_id": rig.live.engine.session_id, "media_id": media_id,
                           "position_ms": position_ms, "iteration": iteration})


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_engine_files_stay_inside_studio_data(rig, tmp_path):
    rig.live.obs_configure({"host": "127.0.0.1", "port": 4456, "media_input": "Video bán hàng"})
    assert (tmp_path / "studio" / "engine" / "config.json").is_file()
    assert not (tmp_path / "agent-must-not-be-used").exists()
    status = rig.live.obs_status()
    assert status["port"] == 4456 and status["media_input"] == "Video bán hàng" and "password" not in status


def test_obs_status_reports_the_canvas_videos_are_fitted_to(rig):
    status = rig.live.obs_connect()
    assert status["connected"] and status["canvas"] == {"width": 1080, "height": 1920}


def test_markers_belong_to_the_video_and_are_validated(rig, clip):
    shirt, dress = create_product(rig.service, "Áo"), create_product(rig.service, "Váy")
    video = rig.video(clip)
    saved = rig.markers(video["id"], pin(6000, dress), unpin(9000), pin(1000, shirt))
    assert [(m["at_ms"], m["action"], m["product_name"]) for m in saved] == \
        [(1000, "PIN_PRODUCT", "Áo"), (6000, "PIN_PRODUCT", "Váy"), (9000, "UNPIN_PRODUCT", None)]
    listed = rig.service.dispatch("studio.media.list", {})["media"][0]
    assert listed["marker_count"] == 3
    for bad in ([{"at_ms": 99_000, "action": "PIN_PRODUCT", "product_id": shirt["id"]}],
                [{"at_ms": -1, "action": "UNPIN_PRODUCT"}], [{"at_ms": 10, "action": "SWITCH"}],
                [{"at_ms": 10, "action": "UNPIN_PRODUCT", "product_id": shirt["id"]}],
                [{"at_ms": 10, "action": "PIN_PRODUCT", "product_id": str(uuid4())}],
                [{"at_ms": True, "action": "UNPIN_PRODUCT"}]):
        code = error_code(lambda: rig.service.dispatch("studio.media.markers.set",
                                                       {"media_id": video["id"], "markers": bad}))
        assert code in {"VALIDATION_ERROR", "NOT_FOUND"}
    assert len(rig.service.dispatch("studio.media.markers.list", {"media_id": video["id"]})["markers"]) == 3
    rig.service.dispatch("studio.products.delete", {"id": dress["id"]})
    assert [m["product_name"] for m in rig.service.dispatch("studio.media.markers.list",
                                                            {"media_id": video["id"]})["markers"]] == ["Áo", None]


def test_session_is_an_ordered_video_list(rig, clip):
    first, second = rig.video(clip), rig.video(clip)
    session = rig.live.sessions_create({"title": "  Phiên tối ", "video_ids": [second["id"], first["id"]],
                                        "video_order": "shuffle", "video_loop": False})["session"]
    assert (session["title"], session["video_ids"], session["video_order"], session["video_loop"], session["status"]) == \
        ("Phiên tối", [second["id"], first["id"]], "shuffle", False, "draft")
    for bad in ({"title": "x", "video_ids": []}, {"title": "x", "video_ids": [first["id"], first["id"]]},
                {"title": "", "video_ids": [first["id"]]}, {"title": "x", "video_ids": [str(uuid4())]},
                {"title": "x", "video_ids": [first["id"]], "video_order": "chaos"},
                {"title": "x", "video_ids": [first["id"]], "product_ids": []}):
        assert error_code(lambda: rig.live.sessions_create(bad)) in {"VALIDATION_ERROR", "NOT_FOUND"}
    rig.service.dispatch("studio.media.delete", {"id": second["id"]})
    assert rig.service.live_session(session["id"])["video_ids"] == [first["id"]]


def test_start_plays_the_videos_and_pins_at_markers_in_rehearsal(rig, clip):
    shirt, dress = create_product(rig.service, "Áo"), create_product(rig.service, "Váy")
    video = rig.video(clip)
    rig.markers(video["id"], pin(2000, shirt), pin(6000, dress), unpin(9000))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    status = rig.live.start({"session_id": session["id"]})
    assert status["active"] and status["media_id"] == video["id"] and status["session"]["status"] == "live"
    assert rig.ws.settings["Sarae Video"]["local_file"].startswith(str(rig.service.media_dir))
    assert status["pins"]["real"] is False and len(status["markers"]) == 3
    at(rig, video["id"], 1000)
    assert rig.live.status()["pins"]["desired_product_id"] is None and rig.live.current_product_id() is None
    at(rig, video["id"], 2500)
    pins = rig.live.status()["pins"]
    assert pins["desired_product_id"] == shirt["id"] and pins["actual_product_id"] == shirt["id"]
    assert pins["product_names"][shirt["id"]] == "Áo" and rig.live.current_product_id() == shirt["id"]
    at(rig, video["id"], 7000)
    assert rig.live.status()["pins"]["actual_product_id"] == dress["id"]
    at(rig, video["id"], 9500)
    assert rig.live.status()["pins"]["actual_product_id"] is None
    kinds = [event["kind"] for event in rig.live.status()["events"]]
    assert kinds.count("PIN_APPLIED") == 3 and kinds[-1] == "STARTED"


def test_seek_pins_what_is_in_force_without_replaying(rig, clip):
    shirt, dress = create_product(rig.service, "Áo"), create_product(rig.service, "Váy")
    video = rig.video(clip)
    rig.markers(video["id"], pin(1000, shirt), pin(5000, dress))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    at(rig, video["id"], 8000)
    status = rig.live.status()
    assert status["pins"]["actual_product_id"] == dress["id"]
    assert [e["kind"] for e in status["events"]].count("PIN_APPLIED") == 1


def test_real_pins_wait_for_the_tiktok_console(rig, clip):
    shirt = create_product(rig.service, "Áo")
    video = rig.video(clip)
    rig.markers(video["id"], pin(0, shirt))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.configure_pins({"real": True})
    rig.live.start({"session_id": session["id"]})
    at(rig, video["id"], 3000)
    pins = rig.live.status()["pins"]
    assert pins["real"] is True and pins["chrome_connected"] is False
    assert pins["desired_product_id"] == shirt["id"] and pins["actual_product_id"] is None  # fail closed
    rig.live.configure_pins({"real": False})
    at(rig, video["id"], 3500)
    assert rig.live.status()["pins"]["actual_product_id"] == shirt["id"]
    assert error_code(lambda: rig.live.configure_pins({"real": "yes"})) == "VALIDATION_ERROR"


def test_switching_from_rehearsal_never_claims_a_real_pin_was_confirmed(rig, clip):
    shirt = create_product(rig.service, "Áo")
    video = rig.video(clip)
    rig.markers(video["id"], pin(0, shirt))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    at(rig, video["id"], 1000)
    assert rig.live.status()["pins"]["actual_known"] is True
    result = rig.live.configure_pins({"real": True})
    assert result["pins"]["actual_known"] is False
    assert result["pins"]["actual_product_id"] is None
    assert result["console_live"]["state"] == "UNKNOWN"


def test_status_exposes_next_marker_and_separates_tiktok_from_obs_playback(rig, clip):
    shirt = create_product(rig.service, "Áo")
    video = rig.video(clip)
    rig.markers(video["id"], pin(0, shirt), unpin(8000))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    rig.ws.cursor["Sarae Video"] = 3000
    result = rig.live.status()
    assert result["active"] is True and result["broadcast"]["state"] != "live"
    assert result["console_live"]["state"] == "UNKNOWN"
    assert result["next_marker"]["action"] == "UNPIN_PRODUCT"
    assert result["next_marker"]["remaining_ms"] == 5000
    rig.ws.cursor["Sarae Video"] = 9000
    assert rig.live.status()["next_marker"] is None


@pytest.mark.parametrize("console_state,expected_clicks", [("LIVE", 1), ("OFFLINE", 0), ("UNKNOWN", 0)])
def test_real_pin_uses_console_executor_and_requires_console_live(rig, clip, console_state, expected_clicks):
    shirt = create_product(rig.service, "Áo")
    video = rig.video(clip)
    rig.markers(video["id"], pin(0, shirt))
    calls = []
    actual = [None]
    def completed(value):
        future = Future()
        future.set_result(value)
        return future
    def submit_pin(target, current, guard):
        assert current()
        calls.append(target)
        actual[0] = target["tiktok_product_id"]
        return completed({"pinned_product_id": actual[0]})
    browser = SimpleNamespace(status=lambda: {"browser_open": True},
                              submit_live_state=lambda: completed({"state": console_state}),
                              submit_pin_state=lambda: completed({"pinned_product_id": actual[0]}),
                              submit_pin=submit_pin)
    rig.live.tiktok = SimpleNamespace(_browser=browser)
    rig.live.configure_pins({"real": True})
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    rig.live.status()  # read the Console state before the next playback observation
    at(rig, video["id"], 1000)
    at(rig, video["id"], 2000)
    assert len(calls) == expected_clicks
    result = rig.live.status()
    if expected_clicks:
        assert result["pins"]["actual_known"] is True
        assert result["pins"]["actual_product_id"] == shirt["id"]
        assert calls[0]["tiktok_product_id"] == shirt["tiktok_product_id"]
    else:
        assert result["pins"]["actual_known"] is False


def test_marker_edits_reach_the_running_session(rig, clip):
    shirt, dress = create_product(rig.service, "Áo"), create_product(rig.service, "Váy")
    video = rig.video(clip)
    rig.markers(video["id"], pin(1000, shirt))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    at(rig, video["id"], 4000)
    rig.markers(video["id"], pin(1000, shirt), pin(3000, dress))
    rig.live.refresh()
    at(rig, video["id"], 4100)
    assert rig.live.status()["pins"]["actual_product_id"] == dress["id"]


def test_session_edits_while_live(rig, clip):
    first, second = rig.video(clip), rig.video(clip)
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [first["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    assert error_code(lambda: rig.live.sessions_update({"id": session["id"], "video_ids": [second["id"]]})) == "SESSION_LIVE"
    rig.live.sessions_update({"id": session["id"], "video_loop": False, "title": "Đổi tên"})
    assert rig.live.engine.loop is False
    assert error_code(lambda: rig.live.sessions_delete({"id": session["id"]})) == "SESSION_LIVE"
    other = rig.live.sessions_create({"title": "Khác", "video_ids": [second["id"]]})["session"]
    assert error_code(lambda: rig.live.start({"session_id": other["id"]})) == "SESSION_CONFLICT"


def test_pause_seek_stop_and_restart(rig, clip):
    shirt = create_product(rig.service, "Áo")
    video = rig.video(clip)
    rig.markers(video["id"], pin(0, shirt))
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    assert rig.live.pause()["paused"] is True
    assert rig.live.resume()["paused"] is False
    assert rig.live.seek({"position_ms": 4000})["active"] is True
    assert error_code(lambda: rig.live.seek({"position_ms": -5})) == "VALIDATION_ERROR"
    stopped = rig.live.stop()
    assert stopped["active"] is False and stopped["session"] is None
    assert rig.live.scheduler.status()["session_id"] is None  # nothing pinned after a stop
    ended = rig.service.live_session(session["id"])
    assert ended["status"] == "ended" and ended["end_reason"] == "operator"
    assert error_code(rig.live.pause) == "SESSION_NOT_LIVE"
    assert rig.live.start({"session_id": session["id"]})["active"] is True


def test_start_requires_playable_videos(rig, clip):
    video = rig.video(clip)
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.service.resolve_media(video["id"]).unlink()
    assert error_code(lambda: rig.live.start({"session_id": session["id"]})) == "NO_PLAYABLE_VIDEO"
    assert rig.service.live_session(session["id"])["status"] == "draft"


def test_core_restart_never_resumes_a_live_session(tmp_path, clip):
    first = Rig(tmp_path / "studio")
    video = first.video(clip)
    session = first.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    first.live.start({"session_id": session["id"]})
    first.live.engine.stop_monitor()  # a crash: the LIVE layer never stopped the session
    first.service.close()
    second = Rig(tmp_path / "studio")
    try:
        restored = second.service.live_session(session["id"])
        assert restored["status"] == "ended" and restored["end_reason"] == "core_restart"
        assert second.live.status()["active"] is False
    finally:
        second.close()


def test_obs_errors_become_studio_errors(rig, clip):
    rig.ws.broken = True
    video = rig.video(clip)
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    code = error_code(lambda: rig.live.start({"session_id": session["id"]}))
    assert code.startswith("OBS_") or code.startswith("PLAYBACK")
    assert rig.service.live_session(session["id"])["status"] == "draft"
    assert error_code(lambda: rig.live.obs_configure({"port": 99999})) == "VALIDATION_ERROR"


def test_session_listeners_hear_start_and_stop(rig, clip):
    heard = []
    rig.live.session_listeners.append(lambda session, started: heard.append((session["status"], started)))
    video = rig.video(clip)
    session = rig.live.sessions_create({"title": "Phiên", "video_ids": [video["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    rig.live.stop()
    assert heard == [("live", True), ("ended", False)]


def test_old_product_lineups_and_scripts_are_gone(rig):
    connection = sqlite3.connect(rig.service.database_path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()
    assert "media_markers" in tables and "live_session_products" not in tables and "sales_scripts" not in tables
