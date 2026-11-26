"""LIVE sessions: product lineup played through OBS by the Agent's engine (fake obs-websocket, real MP4 files)."""
from __future__ import annotations

import shutil
import subprocess
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
                    "color=c=green:s=32x32:r=10:d=0.5", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=30)
    return path


class Rig:
    def __init__(self, root, ws=None):
        self.ws = ws or FakeWs()
        self.events = []
        self.service = StudioService(data_dir=root)
        self.live = StudioLive(self.service, emit=lambda name, data: self.events.append(name),
                               obs_client_factory=lambda cfg, password: self.ws)

    def close(self):
        self.live.close()
        self.service.close()


@pytest.fixture
def rig(tmp_path):
    instance = Rig(tmp_path / "studio")
    yield instance
    instance.close()


def product_with_videos(rig, clip, name, count=1):
    product = create_product(rig.service, name)
    for _ in range(count):
        rig.service.dispatch("studio.media.import", {"source_path": str(clip), "product_id": product["id"]})
    return product


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


def test_session_validation_and_default_policy(rig, clip):
    a = create_product(rig.service, "Áo")
    session = rig.live.sessions_create({"title": "  Phiên tối  ", "product_ids": [a["id"]]})["session"]
    assert session["title"] == "Phiên tối" and session["status"] == "draft" and session["product_ids"] == [a["id"]]
    assert session["policy"]["video_order"] == "sequential" and session["policy"]["product_rotation"] == "off"
    for bad in ({"title": "x", "product_ids": []}, {"title": "x", "product_ids": [a["id"], a["id"]]},
                {"title": "", "product_ids": [a["id"]]}, {"title": "x", "product_ids": [str(uuid4())]},
                {"title": "x", "product_ids": [a["id"]], "policy": {"video_order": "chaos"}},
                {"title": "x", "product_ids": [a["id"]], "policy": {"product_rotation": "random",
                                                                     "rotate_after_videos": None}}):
        assert error_code(lambda: rig.live.sessions_create(bad)) in {"VALIDATION_ERROR", "NOT_FOUND"}
    assert len(rig.live.sessions_list()["sessions"]) == 1


def test_start_requires_a_playable_video(rig):
    a = create_product(rig.service, "Không video")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [a["id"]]})["session"]
    assert error_code(lambda: rig.live.start({"session_id": session["id"]})) == "NO_PLAYABLE_VIDEO"
    assert rig.live.status()["active"] is False
    assert rig.service.live_session(session["id"])["status"] == "draft"


def test_start_plays_first_product_with_videos_in_obs(rig, clip):
    empty = create_product(rig.service, "Chưa có video")
    shirt = product_with_videos(rig, clip, "Áo linen", 2)
    session = rig.live.sessions_create({"title": "Phiên sáng", "product_ids": [empty["id"], shirt["id"]]})["session"]
    status = rig.live.start({"session_id": session["id"]})
    assert status["active"] is True and status["product_id"] == shirt["id"] and len(status["queue"]) == 2
    assert status["session"]["status"] == "live" and status["obs_connected"] is True
    played = rig.ws.settings["Sarae Video"]["local_file"]
    assert played.startswith(str(rig.service.media_dir))
    assert [event["kind"] for event in status["events"]] == ["STARTED"]
    assert error_code(lambda: rig.live.sessions_delete({"id": session["id"]})) == "SESSION_LIVE"
    other = rig.live.sessions_create({"title": "Khác", "product_ids": [shirt["id"]]})["session"]
    assert error_code(lambda: rig.live.start({"session_id": other["id"]})) == "SESSION_CONFLICT"


def test_switch_after_current_immediate_and_cancel(rig, clip):
    shirt = product_with_videos(rig, clip, "Áo")
    dress = product_with_videos(rig, clip, "Váy")
    outsider = product_with_videos(rig, clip, "Ngoài phiên")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"], dress["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    queued = rig.live.switch({"product_id": dress["id"], "mode": "after_current"})
    assert queued["product_id"] == shirt["id"] and queued["pending_product_id"] == dress["id"]
    assert rig.live.cancel_pending()["pending_product_id"] is None
    now = rig.live.switch({"product_id": dress["id"], "mode": "immediate"})
    assert now["product_id"] == dress["id"]
    assert error_code(lambda: rig.live.switch({"product_id": outsider["id"]})) == "VALIDATION_ERROR"
    assert error_code(lambda: rig.live.switch({"product_id": dress["id"], "mode": "later"})) == "VALIDATION_ERROR"


def test_pause_resume_stop_and_restart_from_ended(rig, clip):
    shirt = product_with_videos(rig, clip, "Áo")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    assert rig.live.pause()["paused"] is True
    assert rig.live.resume()["paused"] is False
    stopped = rig.live.stop()
    assert stopped["active"] is False and stopped["session"] is None
    ended = rig.service.live_session(session["id"])
    assert ended["status"] == "ended" and ended["end_reason"] == "operator"
    assert error_code(rig.live.pause) == "SESSION_NOT_LIVE"
    assert rig.live.start({"session_id": session["id"]})["active"] is True


def test_new_video_reaches_the_running_engine(rig, clip):
    shirt = product_with_videos(rig, clip, "Áo")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    rig.service.dispatch("studio.media.import", {"source_path": str(clip), "product_id": shirt["id"]})
    rig.live.refresh()
    assert len(rig.live.engine.product_media[shirt["id"]]) == 2


def test_policy_update_while_live_is_pushed_to_engine(rig, clip):
    shirt = product_with_videos(rig, clip, "Áo")
    dress = product_with_videos(rig, clip, "Váy")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"], dress["id"]]})["session"]
    rig.live.start({"session_id": session["id"]})
    rig.live.sessions_update({"id": session["id"], "policy": {"product_rotation": "sequential",
                                                              "rotate_after_videos": 2, "video_order": "shuffle"}})
    assert rig.live.engine.policy.product_rotation == "sequential"
    assert rig.live.engine.product_order == [shirt["id"], dress["id"]]


def test_core_restart_never_resumes_a_live_session(tmp_path, clip):
    first = Rig(tmp_path / "studio")
    shirt = product_with_videos(first, clip, "Áo")
    session = first.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"]]})["session"]
    first.live.start({"session_id": session["id"]})
    # Simulate a crash: the service closes without the LIVE layer stopping the session.
    first.live.engine.stop_monitor()
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
    shirt = product_with_videos(rig, clip, "Áo")
    session = rig.live.sessions_create({"title": "Phiên", "product_ids": [shirt["id"]]})["session"]
    code = error_code(lambda: rig.live.start({"session_id": session["id"]}))
    assert code.startswith("OBS_") or code.startswith("PLAYBACK")
    assert rig.service.live_session(session["id"])["status"] == "draft"
    assert error_code(lambda: rig.live.obs_configure({"port": 99999})) == "VALIDATION_ERROR"
