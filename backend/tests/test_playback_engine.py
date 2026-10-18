"""PlaybackEngine against a fake obsws ReqClient. Run: python -m unittest discover -s tests -p test_*.py"""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

if importlib.util.find_spec("obsws_python") is None:
    raise unittest.SkipTest("Agent-side engine (agent-core): pip install -r requirements-legacy-local.txt")

from app import playback_engine
from app.playback_engine import PlaybackEngine, PlaybackError

ENDED = "OBS_MEDIA_STATE_ENDED"
PLAYING = "OBS_MEDIA_STATE_PLAYING"


class FakeObs:
    """Shared OBS state; every engine.client() call returns this object."""

    def __init__(self, scene_items=("Sarae Video",)):
        self.scene = "LIVE"
        self.scene_items = {name: i + 1 for i, name in enumerate(scene_items)}
        self.enabled = {}
        self.muted = {}
        self.settings = {}
        self.media_state = {}
        self.cursor = {}
        self.duration = {}
        self.actions = []
        self.cursor_calls = []
        self.fail = False
        self.base_client = SimpleNamespace(ws=SimpleNamespace(close=lambda: None))

    def _check(self):
        if self.fail:
            raise ConnectionError("OBS closed")

    def get_version(self):
        self._check()
        return SimpleNamespace(obs_version="30.1.0")

    def get_current_program_scene(self):
        self._check()
        return SimpleNamespace(current_program_scene_name=self.scene)

    def get_scene_item_id(self, scene, name):
        if name not in self.scene_items:
            raise KeyError(name)
        return SimpleNamespace(scene_item_id=self.scene_items[name])

    def get_scene_item_list(self, scene):
        return SimpleNamespace(scene_items=[{"sourceName": n} for n in self.scene_items])

    def get_input_list(self):
        names = list(self.scene_items) + ["Mic"]
        return SimpleNamespace(inputs=[{"inputName": n, "inputKind": "ffmpeg_source" if n != "Mic" else "wasapi_input_capture"}
                                       for n in names])

    def get_media_input_status(self, name):
        self._check()
        return SimpleNamespace(media_state=self.media_state.get(name, PLAYING),
                               media_cursor=self.cursor.get(name, 0), media_duration=self.duration.get(name, 60000))

    def set_input_settings(self, name, settings, overlay=True):
        self._check()
        self.settings.setdefault(name, {}).update(settings)

    def get_input_settings(self, name):
        return SimpleNamespace(input_settings=dict(self.settings.get(name, {})))

    def trigger_media_input_action(self, name, action):
        self._check()
        self.actions.append((name, action.rsplit("_", 1)[-1]))
        if action.endswith("RESTART"):
            self.media_state[name] = PLAYING
            self.cursor[name] = 0

    def set_input_mute(self, name, muted):
        self.muted[name] = muted

    def set_scene_item_enabled(self, scene, item_id, enabled):
        self.enabled[item_id] = enabled

    def set_media_input_cursor(self, name, ms):
        self.cursor_calls.append((name, ms))


class EngineTestBase(unittest.TestCase):
    dual = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.files = {}
        for name in ("p1a", "p1b", "p1c", "p2a", "p2b", "pl1", "pl2"):
            path = root / f"{name}.mp4"
            path.write_bytes(b"00")
            self.files[name] = hashlib.sha256(str(path).casefold().encode()).hexdigest()
        self.paths = {mid: root / f"{name}.mp4" for name, mid in self.files.items()}
        self.obs = FakeObs(("Sarae Video A", "Sarae Video B") if self.dual else ("Sarae Video",))
        self.events = []
        config_file = root / "obs_config.json"
        if self.dual:
            playback_engine.save_obs_config(playback_engine.ObsConfig(dual_mode=True), config_file)
        self.engine = PlaybackEngine(resolve=self.resolve, on_event=self.events.append,
                                     config_file=config_file, client_factory=lambda cfg: self.obs)
        sleep = mock.patch.object(playback_engine.time, "sleep", lambda s: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, media_id):
        path = self.paths.get(media_id)
        if path is None:
            raise ValueError("Video không tồn tại trên máy Agent")
        return path

    def ids(self, *names):
        return [self.files[n] for n in names]

    def path(self, name):
        return str(self.paths[self.files[name]].resolve())

    def end_current(self):
        self.obs.media_state[self.engine.active_source()] = ENDED
        self.engine.tick()


class SingleSourceTests(EngineTestBase):
    def test_product_play_immediate_starts_and_switches(self):
        result = self.engine.product_play(7, 11, self.ids("p1a", "p1b"), "immediate")
        self.assertEqual(result["index"], 0)
        status = self.engine.status()
        self.assertTrue(status["running"])
        self.assertEqual((status["session_mode"], status["session_id"], status["product_id"]), ("product", 7, 11))
        self.assertEqual(status["queue_ids"], self.ids("p1a", "p1b"))
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1a"))
        self.engine.product_play(7, 12, self.ids("p2a"), "immediate")
        self.assertEqual(self.engine.product_id, 12)
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p2a"))
        self.assertEqual(self.events, [], "an immediate switch is answered by the command result")

    def test_after_current_switches_at_boundary_and_emits_event(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b"), "immediate")
        reply = self.engine.product_play(7, 12, self.ids("p2a", "p2b"), "after_current")
        self.assertEqual(reply, {"ok": True, "pending_product_id": 12})
        self.assertEqual(self.engine.status()["pending_product_id"], 12)
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1a"))
        self.end_current()
        self.assertEqual(self.engine.product_id, 12)
        self.assertIsNone(self.engine.pending_product_id)
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p2a"))
        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        self.assertEqual((event["type"], event["session_id"], event["product_id"]), ("PRODUCT_SWITCHED", 7, 12))
        self.assertEqual(event["detail"]["trigger"], "video_end")
        self.assertEqual(event["detail"]["from_product_id"], 11)

    def test_after_current_same_product_and_cancel(self):
        self.engine.product_play(7, 11, self.ids("p1a"), "immediate")
        self.assertEqual(self.engine.product_play(7, 11, self.ids("p1a", "p1b"), "after_current")["pending_product_id"], None)
        self.engine.product_play(7, 12, self.ids("p2a"), "after_current")
        self.assertEqual(self.engine.cancel_pending(), {"ok": True, "cancelled_product_id": 12})
        self.end_current()
        # Same product continues with its refreshed lineup.
        self.assertEqual(self.engine.product_id, 11)
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1b"))

    def test_product_queue_loops_and_skips_missing_files(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b", "p1c"), "immediate")
        self.paths[self.files["p1b"]].unlink()
        self.end_current()
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1c"))
        self.end_current()
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1a"))
        self.assertTrue(self.engine.active)

    def test_next_applies_pending_switch_with_event(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b"), "immediate")
        self.engine.product_play(7, 12, self.ids("p2a"), "after_current")
        self.engine.next()
        self.assertEqual(self.engine.product_id, 12)
        self.assertEqual(self.events[0]["detail"]["trigger"], "next")

    def test_product_play_rejects_other_session_and_empty_lineup(self):
        self.engine.product_play(7, 11, self.ids("p1a"), "immediate")
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.product_play(8, 12, self.ids("p2a"), "immediate")
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.product_play(7, 13, ["f" * 64], "immediate")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.engine.product_id, 11)

    def test_select_seek_pause_resume_previous_stop(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b", "p1c"), "immediate")
        self.engine.select(2)
        self.assertEqual(self.engine.index, 2)
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("p1c"))
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.select(5)
        self.assertEqual(ctx.exception.status_code, 422)
        self.engine.seek(15000)
        self.assertEqual(self.obs.cursor_calls[-1], ("Sarae Video", 15000))
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.seek(61000)
        self.assertEqual(ctx.exception.status_code, 422)
        self.engine.pause()
        self.assertTrue(self.engine.status()["paused"])
        self.engine.resume()
        self.engine.previous()
        self.assertEqual(self.engine.index, 1)
        self.engine.product_play(7, 12, self.ids("p2a"), "after_current")
        self.engine.stop()
        status = self.engine.status()
        self.assertFalse(status["running"])
        self.assertIsNone(status["pending_product_id"])
        self.assertEqual(self.obs.actions[-1], ("Sarae Video", "STOP"))
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.seek(0)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_playlist_mode_end_emits_playback_ended(self):
        self.engine.start_playlist(self.ids("pl1", "pl2"), loop=False)
        self.assertEqual(self.engine.status()["session_mode"], "playlist")
        with self.assertRaises(PlaybackError):
            self.engine.start_playlist(self.ids("pl1"))
        self.end_current()
        self.assertEqual(self.obs.settings["Sarae Video"]["local_file"], self.path("pl2"))
        self.end_current()
        self.assertFalse(self.engine.active)
        self.assertEqual([e["type"] for e in self.events], ["PLAYBACK_ENDED"])
        self.assertEqual(self.events[0]["detail"]["reason"], "playlist_end")

    def test_obs_errors_retry_then_end(self):
        self.engine.product_play(7, 11, self.ids("p1a"), "immediate")
        self.obs.fail = True
        for _ in range(5):
            self.engine.next_retry_at = 0
            self.engine.tick()
        self.assertFalse(self.engine.active)
        self.assertEqual(self.engine.error_count, 5)
        self.assertEqual(self.events[-1]["detail"]["reason"], "obs_error")

    def test_status_shape_matches_live_status(self):
        self.engine.product_play(7, 11, self.ids("p1a"), "immediate")
        status = self.engine.status()
        for key in ("connected", "obs_version", "media_state", "media_cursor", "media_duration", "running", "paused",
                    "loop", "session_mode", "playlist_id", "product_id", "pending_product_id", "queue_ids", "index",
                    "count", "last_status", "retry_count", "error_count", "transition_count", "recovery_count",
                    "session_seconds", "session_id", "obs"):
            self.assertIn(key, status)
        self.assertEqual(set(status["obs"]), {"host", "port", "input_name", "dual_mode", "dual_sources", "has_password"})
        self.obs.fail = True
        offline = self.engine.status()
        self.assertFalse(offline["connected"])
        self.assertIn("queue_ids", offline)

    def test_configure_keeps_password_and_never_returns_it(self):
        self.engine.configure(host="127.0.0.1", port=4455, password="s3cret", input_name="Sarae Video")
        status = self.engine.configure(host="127.0.0.1", port=4456, password="", input_name="Sarae Video")
        self.assertEqual(self.engine.config.password, "s3cret")
        self.assertTrue(status["obs"]["has_password"])
        self.assertNotIn("s3cret", repr(status))
        self.assertEqual(playback_engine.load_obs_config(self.engine.config_file).port, 4456)
        with self.assertRaises(PlaybackError):
            self.engine.configure(host="10.0.0.5")
        with self.assertRaises(PlaybackError):
            self.engine.configure(dual_mode=True, source_a="X", source_b="X")

    def test_list_inputs(self):
        result = self.engine.list_inputs("127.0.0.1", 4455, "")
        self.assertEqual(result["scene"], "LIVE")
        self.assertEqual(result["inputs"][0], {"name": "Sarae Video", "kind": "ffmpeg_source", "in_scene": True})
        self.assertFalse(result["inputs"][-1]["in_scene"])


class DualModeTests(EngineTestBase):
    dual = True

    def test_dual_sources_stage_next_video_and_swap(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b", "p1c"), "immediate")
        a, b = "Sarae Video A", "Sarae Video B"
        self.assertEqual(self.engine.slots, [a, b])
        self.assertTrue(self.obs.enabled[1])
        self.assertFalse(self.obs.enabled[2])
        self.assertFalse(self.obs.muted[a])
        self.assertTrue(self.obs.muted[b])
        self.assertEqual(self.obs.settings[a]["local_file"], self.path("p1a"))
        # Stand-by source preloads the following video.
        self.assertEqual(self.obs.settings[b]["local_file"], self.path("p1b"))
        self.end_current()
        self.assertEqual(self.engine.active_slot, 1)
        self.assertTrue(self.obs.enabled[2])
        self.assertFalse(self.obs.enabled[1])
        self.assertFalse(self.obs.muted[b])
        self.assertTrue(self.obs.muted[a])
        self.assertEqual(self.obs.settings[a]["local_file"], self.path("p1c"))
        self.assertEqual(self.engine.status()["obs"]["dual_sources"], [a, b])

    def test_dual_after_current_switch_restages(self):
        self.engine.product_play(7, 11, self.ids("p1a", "p1b"), "immediate")
        self.engine.product_play(7, 12, self.ids("p2a", "p2b"), "after_current")
        self.end_current()
        a, b = "Sarae Video A", "Sarae Video B"
        # B held p1b; the switch must re-stage it with the new product's first video.
        self.assertEqual(self.obs.settings[b]["local_file"], self.path("p2a"))
        self.assertEqual(self.obs.settings[a]["local_file"], self.path("p2b"))
        self.assertEqual(self.events[0]["type"], "PRODUCT_SWITCHED")

    def test_dual_requires_both_sources(self):
        del self.obs.scene_items["Sarae Video B"]
        with self.assertRaises(PlaybackError) as ctx:
            self.engine.product_play(7, 11, self.ids("p1a"), "immediate")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertFalse(self.engine.active)


if __name__ == "__main__":
    unittest.main()
