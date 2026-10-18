import hashlib
import time

import pytest

from sarae_agent.config import ConfigStore
from sarae_agent.errors import AgentError
from sarae_agent.media.library import MediaLibrary, media_id_for
from sarae_agent.playlist.engine import PlaybackEngine


class FakeObs:
    """Records what OBS was told; media state is driven by the test."""

    def __init__(self):
        self.loaded: list[str] = []
        self.actions: list[str] = []
        self.state = "OBS_MEDIA_STATE_PLAYING"
        self.fail: AgentError | None = None

    def play_file(self, input_name, path, unmute=False):
        if self.fail:
            raise self.fail
        self.loaded.append(path)
        self.state = "OBS_MEDIA_STATE_PLAYING"
        return {"state": self.state}

    def media_action(self, input_name, action):
        if self.fail:
            raise self.fail
        self.actions.append(action)
        self.state = {"pause": "OBS_MEDIA_STATE_PAUSED", "play": "OBS_MEDIA_STATE_PLAYING",
                      "stop": "OBS_MEDIA_STATE_STOPPED"}.get(action, self.state)
        return {"state": self.state}

    def media_status(self, input_name):
        if self.fail:
            raise self.fail
        return {"state": self.state}


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


# --- playback ------------------------------------------------------------------------------
@pytest.fixture()
def player(library, events):
    lib, store, videos = library
    obs = FakeObs()
    engine = PlaybackEngine(store, lib, obs, events)
    engine.SETTLE_SECONDS = 0
    yield engine, obs, lib, videos
    engine.shutdown()


def test_playlist_plays_and_auto_advances(player):
    engine, obs, lib, videos = player
    found = ids(lib)
    order = [found["a.MP4"], found["b.mp4"], found["c.mp4"]]
    status = engine.play_playlist(order, loop=False)
    assert status["active"] and status["current_filename"] == "a.MP4"
    assert obs.loaded[-1].endswith("a.MP4")
    obs.state = "OBS_MEDIA_STATE_ENDED"
    engine.tick()
    assert engine.status()["current_filename"] == "b.mp4"
    obs.state = "OBS_MEDIA_STATE_ENDED"
    engine.tick()
    obs.state = "OBS_MEDIA_STATE_ENDED"
    engine.tick()  # last item ended without loop -> stop
    assert not engine.status()["active"]
    assert obs.actions[-1] == "stop"


def test_loop_previous_pause_resume(player):
    engine, obs, lib, _ = player
    found = ids(lib)
    engine.play_playlist([found["a.MP4"], found["b.mp4"]], loop=True)
    engine.next()
    engine.next()
    assert engine.status()["index"] == 0  # wrapped
    engine.previous()
    assert engine.status()["index"] == 1
    assert engine.pause()["paused"] and obs.actions[-1] == "pause"
    obs.state = "OBS_MEDIA_STATE_ENDED"
    engine.tick()
    assert engine.status()["index"] == 1  # paused playlists never auto-advance
    assert not engine.resume()["paused"] and obs.actions[-1] == "play"
    engine.set_loop(False)
    assert engine.status()["loop"] is False


def test_missing_file_is_skipped_and_reported(player):
    engine, obs, lib, videos = player
    found = ids(lib)
    (videos / "a.MP4").unlink()
    status = engine.play_playlist([found["a.MP4"], found["b.mp4"]])
    assert status["current_filename"] == "b.mp4"
    assert status["skipped"] == [found["a.MP4"]]


def test_no_fake_success(player):
    engine, obs, lib, videos = player
    with pytest.raises(AgentError) as exc:
        engine.play_playlist([])
    assert exc.value.code == "PLAYLIST_EMPTY"
    with pytest.raises(AgentError) as exc:
        engine.play_playlist(["f" * 64])
    assert exc.value.code == "NO_PLAYABLE_MEDIA"
    assert not engine.status()["active"]
    with pytest.raises(AgentError) as exc:
        engine.next()
    assert exc.value.code == "PLAYBACK_NOT_ACTIVE"
    obs.fail = AgentError("OBS_NOT_RUNNING", "OBS chưa chạy")
    with pytest.raises(AgentError) as exc:
        engine.play(ids(lib)["b.mp4"])
    assert exc.value.code == "OBS_NOT_RUNNING"
    assert not engine.status()["active"] and engine.status()["last_error"]["code"] == "OBS_NOT_RUNNING"


def test_obs_drop_during_play_is_reported_then_recovers(player):
    engine, obs, lib, _ = player
    engine.play(ids(lib)["b.mp4"])
    obs.fail = AgentError("OBS_DISCONNECTED", "Mất kết nối OBS")
    engine.tick()
    assert engine.status()["last_error"]["code"] == "OBS_DISCONNECTED"
    assert engine.status()["active"]
    obs.fail = None
    engine.tick()
    assert engine.status()["last_error"] is None


def test_restart_requires_manual_resume(player, events):
    engine, obs, lib, _ = player
    engine.play(ids(lib)["b.mp4"])
    again = PlaybackEngine(engine._config, lib, obs, events)
    assert again.status()["needs_manual_resume"] is True
    assert again.status()["active"] is False
