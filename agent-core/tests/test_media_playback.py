import base64
import hashlib
import json
import time

import pytest

from conftest import windows_only
from fake_obs import FakeWs
from sarae_agent.config import ConfigStore, paths
from sarae_agent.errors import AgentError
from sarae_agent.media import library as library_module
from sarae_agent.media.library import MediaLibrary, media_id_for
from sarae_agent.obs.controller import ObsController, ObsSettingsStore
from sarae_agent.playback.engine import PlaybackEngine
from sarae_agent.playback.service import PlaybackService

ENDED = "OBS_MEDIA_STATE_ENDED"


def make_videos(root, names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(b"\x00" * 16)


@pytest.fixture()
def library(home, tmp_path, events):
    videos = tmp_path / "videos"
    make_videos(videos, ["b.mp4", "a.MP4", "notes.txt"])
    make_videos(videos / "nested", ["c.mp4"])
    store = ConfigStore()
    lib = MediaLibrary(store, events)
    lib.add_folder(str(videos))
    result = lib.scan_blocking()
    assert result["state"] == "succeeded"
    return lib, store, videos


def ids(lib):
    return {item["filename"]: item["media_id"] for item in lib.list_videos()["items"]}


def test_scan_finds_mp4_only_with_stable_legacy_ids(library, events):
    lib, _, videos = library
    found = ids(lib)
    assert set(found) == {"a.MP4", "b.mp4", "c.mp4"}
    expected = hashlib.sha256(str((videos / "b.mp4").resolve()).casefold().encode()).hexdigest()
    assert found["b.mp4"] == expected == media_id_for((videos / "b.mp4").resolve())
    assert lib.scan_blocking()["added"] == 0  # rescan keeps IDs
    assert ids(lib) == found
    inventory = lib.cloud_inventory()
    assert {"media_id", "filename", "size_bytes", "modified_ns"} == set(inventory[0])
    assert all(str(videos) not in str(v) for v in inventory)  # no local path leaves the PC
    assert any(name == "media.scan_finished" for name, _ in events.captured)


def test_folder_validation(library, tmp_path):
    lib, _, videos = library
    with pytest.raises(AgentError) as exc:
        lib.add_folder(str(videos))
    assert exc.value.code == "FOLDER_ALREADY_ADDED"
    with pytest.raises(AgentError) as exc:
        lib.add_folder(str(tmp_path / "missing"))
    assert exc.value.code == "FOLDER_NOT_FOUND"
    with pytest.raises(AgentError) as exc:
        lib.add_folder("C:\\")
    assert exc.value.code == "FOLDER_TOO_BROAD"


def test_resolve_guards(library):
    lib, _, videos = library
    found = ids(lib)
    assert lib.resolve(found["b.mp4"]) == (videos / "b.mp4").resolve()
    with pytest.raises(AgentError) as exc:
        lib.resolve("../../etc/passwd")
    assert exc.value.code == "INVALID_MEDIA_ID"
    with pytest.raises(AgentError) as exc:
        lib.resolve("f" * 64)
    assert exc.value.code == "MEDIA_NOT_INDEXED"
    (videos / "b.mp4").unlink()
    with pytest.raises(AgentError) as exc:
        lib.resolve(found["b.mp4"])
    assert exc.value.code == "MEDIA_FILE_MISSING"
    assert lib.dirty


def test_remove_folder_never_deletes_files(library):
    lib, store, videos = library
    lib.remove_folder(str(videos.resolve()))
    assert (videos / "a.MP4").exists() and (videos / "nested" / "c.mp4").exists()
    assert lib.list_videos()["total"] == 0
    assert store.get().media.folders == []


def test_renamed_file_detected_on_rescan(library):
    lib, _, videos = library
    old = ids(lib)["b.mp4"]
    (videos / "b.mp4").rename(videos / "renamed.mp4")
    result = lib.scan_blocking()
    assert result["added"] == 1 and result["removed"] == 1
    assert old not in {v["media_id"] for v in lib.cloud_inventory()}


def test_background_scan_is_async_and_exclusive(library, events):
    lib, _, _ = library
    job = lib.start_scan()
    assert job["state"] == "accepted"
    deadline = time.monotonic() + 10
    while lib.scan_status() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert any(n == "media.scan_finished" and d["job_id"] == job["job_id"] for n, d in events.captured)


def make_clip(path, seconds=3, fps=10):
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
    rng = np.random.default_rng(1)
    for _ in range(seconds * fps):
        writer.write(rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8))
    writer.release()
    return path


def test_probe_adds_duration_and_small_thumbnail_once(home, tmp_path, events, monkeypatch):
    videos = tmp_path / "clips"
    videos.mkdir()
    make_clip(videos / "clip.mp4")
    lib = MediaLibrary(ConfigStore(), events)
    lib.add_folder(str(videos))
    lib.scan_blocking()
    mid = lib.cloud_inventory()[0]["media_id"]
    assert "duration_seconds" not in lib.cloud_inventory()[0] and lib.thumb_base64(mid) is None
    assert lib.probe_pending() == 0
    video = lib.cloud_inventory()[0]
    assert video["duration_seconds"] == pytest.approx(3.0, abs=0.2)
    raw = base64.b64decode(lib.thumb_base64(mid))
    assert raw.startswith(b"\xff\xd8") and len(raw) <= 40 * 1024
    assert str(videos) not in json.dumps(lib.cloud_inventory())  # never a local path
    monkeypatch.setattr(library_module, "probe", lambda path: (_ for _ in ()).throw(AssertionError("re-probed")))
    assert lib.probe_pending() == 0  # cached per (size, mtime)
    reloaded = MediaLibrary(ConfigStore(), events)
    assert reloaded.thumb_base64(mid) is not None  # cache survives a restart


def test_probe_budget_spreads_work_and_drops_stale_thumbs(home, tmp_path, events):
    videos = tmp_path / "clips"
    videos.mkdir()
    make_clip(videos / "a.mp4", seconds=1)
    lib = MediaLibrary(ConfigStore(), events)
    lib.add_folder(str(videos))
    lib.scan_blocking()
    assert lib.probe_pending(budget_seconds=0) == 1
    assert lib.probe_pending() == 0
    mid = lib.cloud_inventory()[0]["media_id"]
    assert (paths.thumbs / f"{mid}.jpg").exists()
    (videos / "a.mp4").unlink()
    lib.scan_blocking()
    lib.probe_pending()
    assert not (paths.thumbs / f"{mid}.jpg").exists()


# --- playback: ported PlaybackEngine on the Core's OBS connection ------------------------------
@pytest.fixture()
def player(library, events):
    lib, store, videos = library
    ws = FakeWs()
    connections = []

    def factory(cfg, password):
        connections.append((cfg.host, cfg.port, password))
        return ws

    obs = ObsController(store, events, client_factory=factory)
    obs.RECONNECT_MIN_SECONDS = 0
    engine = PlaybackEngine(resolve=lib.resolve, client_factory=obs.requests_for, store=ObsSettingsStore(store, obs))
    service = PlaybackService(engine, lib, events)
    return service, engine, ws, lib, videos, connections


def test_playlist_plays_and_auto_advances_over_one_connection(player):
    service, engine, ws, lib, videos, connections = player
    found = ids(lib)
    status = service.play_playlist([found["a.MP4"], found["b.mp4"]], loop=False)
    assert status["active"] and status["current_filename"] == "a.MP4" and status["count"] == 2
    assert status["queue_ids"] == [found["a.MP4"], found["b.mp4"]]
    assert ws.settings["Sarae Video"]["local_file"] == str((videos / "a.MP4").resolve())
    ws.media_state["Sarae Video"] = ENDED
    engine.tick()
    assert service.status()["current_filename"] == "b.mp4"
    ws.media_state["Sarae Video"] = ENDED
    engine.tick()
    assert service.status()["active"] is False  # last video ended without loop
    assert len(connections) == 1  # engine requests reuse the Core's single obs-websocket


def test_operator_controls_and_loop(player):
    service, engine, ws, lib, _, _ = player
    found = ids(lib)
    service.play_playlist([found["a.MP4"], found["b.mp4"]], loop=True)
    service.next()
    service.next()
    assert service.status()["index"] == 0  # wrapped
    service.previous()
    assert service.status()["index"] == 1
    assert service.pause()["paused"] and ws.media_state["Sarae Video"] == "OBS_MEDIA_STATE_PAUSED"
    assert not service.resume()["paused"]
    assert service.set_loop(False)["loop"] is False
    with pytest.raises(AgentError) as exc:
        service.play(found["c.mp4"])  # another playlist while one runs: stop first
    assert exc.value.code == "PLAYBACK_CONFLICT"
    assert service.stop()["active"] is False


def test_no_fake_success(player):
    service, engine, ws, lib, videos, _ = player
    with pytest.raises(AgentError) as exc:
        service.play("f" * 64)  # not in the library
    assert exc.value.code == "PLAYBACK_CONFLICT" and not service.status()["active"]
    (videos / "a.MP4").unlink()
    with pytest.raises(AgentError):
        service.play_playlist([ids(lib)["a.MP4"], ids(lib)["b.mp4"]])
    ws.broken = True
    with pytest.raises(AgentError) as exc:
        service.play(ids(lib)["b.mp4"])
    assert exc.value.code == "OBS_UNAVAILABLE"
    status = service.status()
    assert status["active"] is False and status["connected"] is False and status["last_error"]


def test_product_switch_event_reaches_callback(player):
    service, engine, ws, lib, _, _ = player
    seen = []
    engine.on_event = seen.append
    found = ids(lib)
    engine.product_play(7, 1, [found["a.MP4"]], "immediate")
    assert engine.product_play(7, 2, [found["b.mp4"]], "after_current") == {"ok": True, "pending_product_id": 2}
    ws.media_state["Sarae Video"] = ENDED
    engine.tick()
    assert seen[-1]["type"] == "PRODUCT_SWITCHED" and seen[-1]["product_id"] == 2 and seen[-1]["session_id"] == 7
    assert service.status()["current_filename"] == "b.mp4"


def test_restart_requires_manual_resume(player, events):
    service, engine, ws, lib, _, _ = player
    service.play(ids(lib)["b.mp4"])
    again = PlaybackService(engine.__class__(resolve=lib.resolve, client_factory=engine.client_factory,
                                             store=engine.store), lib, events)
    assert again.status()["needs_manual_resume"] is True and again.status()["active"] is False


@windows_only
def test_obs_configure_keeps_password_in_dpapi_store(player):
    service, engine, ws, lib, _, connections = player
    status = engine.configure(host="127.0.0.1", port=4456, password="s3cret", input_name="Sarae Video",
                              dual_mode=False)
    assert "s3cret" not in json.dumps(status) and status["obs"]["has_password"] is True
    saved = json.loads(paths.config_file.read_text(encoding="utf-8"))["obs"]
    assert saved["port"] == 4456 and "s3cret" not in json.dumps(saved)
    engine.status()
    assert connections[-1] == ("127.0.0.1", 4456, "s3cret")  # reconnected with the new settings
    engine.configure(host="127.0.0.1", port=4456, password="", input_name="Sarae Video")  # empty keeps it
    assert engine.store.load().password == "s3cret"
    with pytest.raises(AgentError) as exc:
        engine.configure(host="10.0.0.5", port=4455)
    assert exc.value.code == "INVALID_REQUEST"  # OBS must run on the Agent PC
    inputs = engine.list_inputs("127.0.0.1", 4457, "other")  # unsaved values: temporary connection
    assert inputs["inputs"][0]["name"] == "Sarae Video" and connections[-1] == ("127.0.0.1", 4457, "other")
    assert engine.store.load().port == 4456
