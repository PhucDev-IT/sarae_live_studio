"""Agent-side pieces: media scan metadata, command dispatch/ack and the event outbox.

Run: python -m unittest discover -s tests -p test_*.py
"""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import local_library
from agent import main as agent_main
from app.playback_engine import PlaybackError

MID = "c" * 64


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.videos = root / "videos"
        self.videos.mkdir()
        patches = {"INDEX_FILE": root / "index.json", "FOLDERS_FILE": root / "folders.json",
                   "META_FILE": root / "meta.json", "THUMB_DIR": root / "thumbs"}
        for name, value in patches.items():
            patcher = mock.patch.object(local_library, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        (root / "folders.json").write_text(json.dumps([str(self.videos)]), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def make_video(self, name, seconds=3, fps=10):
        import cv2
        import numpy as np
        path = self.videos / name
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1280, 720))
        rng = np.random.default_rng(1)
        for _ in range(seconds * fps):
            writer.write(rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8))
        writer.release()
        return path

    def test_scan_adds_duration_and_small_thumbnail_once(self):
        path = self.make_video("clip.mp4")
        items = local_library.scan()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertAlmostEqual(item["duration_seconds"], 3.0, delta=0.2)
        data = local_library.thumb_base64(item["media_id"])
        raw = base64.b64decode(data)
        self.assertTrue(raw.startswith(b"\xff\xd8"))
        self.assertLessEqual(len(raw), 40 * 1024)
        import cv2
        import numpy as np
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(image.shape[1], 320)
        self.assertEqual(local_library.resolve(item["media_id"]), path.resolve())
        with mock.patch.object(local_library, "probe", side_effect=AssertionError("re-probed")):
            self.assertEqual(local_library.scan()[0]["duration_seconds"], item["duration_seconds"])

    def test_unprobed_files_wait_for_next_scan(self):
        self.make_video("a.mp4", seconds=1)
        items = local_library.scan(budget_seconds=0)
        self.assertIsNone(items[0]["duration_seconds"])
        self.assertIsNone(local_library.thumb_base64(items[0]["media_id"]))
        self.assertIsNotNone(local_library.scan()[0]["duration_seconds"])


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = mock.Mock()
        self.engine.status.return_value = {"running": True, "obs": {"has_password": True}}
        for patcher in (mock.patch.object(agent_main, "engine", self.engine),
                        # Vietnamese log lines; a cp1252 test console cannot print them.
                        mock.patch("builtins.print")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_new_kinds_reach_engine(self):
        agent_main.execute({"kind": "product_play", "payload": {
            "session_id": 7, "product_id": 3, "media_ids": [MID], "mode": "after_current"}})
        self.engine.product_play.assert_called_once_with(7, 3, [MID], "after_current")
        agent_main.execute({"kind": "playback_select", "payload": {"index": 2}})
        self.engine.select.assert_called_once_with(2)
        agent_main.execute({"kind": "playback_seek", "payload": {"ms": 900}})
        self.engine.seek.assert_called_once_with(900)
        agent_main.execute({"kind": "playback_cancel_pending", "payload": {}})
        self.engine.cancel_pending.assert_called_once()
        agent_main.execute({"kind": "local_playlist_start", "payload": {"media_ids": [MID], "loop": False}})
        self.engine.start_playlist.assert_called_once_with([MID], loop=False)
        for kind, method in (("obs_pause", "pause"), ("playback_resume", "resume"), ("obs_next", "next"),
                             ("playback_previous", "previous"), ("local_media_stop", "stop")):
            agent_main.execute({"kind": kind, "payload": {}})
            getattr(self.engine, method).assert_called()

    def test_obs_configure_returns_status_without_password(self):
        result = agent_main.execute({"kind": "obs_configure", "payload": {
            "host": "127.0.0.1", "port": 4455, "password": "s3cret", "input_name": "Sarae Video",
            "dual_mode": False, "source_a": "", "source_b": ""}})
        self.engine.configure.assert_called_once()
        self.assertEqual(self.engine.configure.call_args.kwargs["password"], "s3cret")
        self.assertNotIn("s3cret", json.dumps(result))

    def test_handle_acks_engine_error_message(self):
        self.engine.pause.side_effect = PlaybackError(409, "Không có video đang phát để Pause")
        with mock.patch.object(agent_main, "request_json") as request:
            agent_main.handle({"command_id": "c1", "kind": "playback_pause", "payload": {}},
                              ("https://vps/api", "key", "agent_1"))
        url, method, key, body = request.call_args.args
        self.assertTrue(url.endswith("/agents/commands/ack"))
        self.assertEqual(body, {"agent_id": "agent_1", "command_id": "c1", "success": False,
                                "result": "Không có video đang phát để Pause"})

    def test_handle_acks_json_result(self):
        self.engine.select.return_value = {"ok": True, "index": 1}
        with mock.patch.object(agent_main, "request_json") as request:
            agent_main.handle({"command_id": "c2", "kind": "playback_select", "payload": {"index": 1}},
                              ("https://vps/api", "key", "agent_1"))
        body = request.call_args.args[3]
        self.assertTrue(body["success"])
        self.assertEqual(json.loads(body["result"]), {"ok": True, "index": 1})


class OutboxTests(unittest.TestCase):
    def test_seq_increases_and_flush_retries_until_sent(self):
        outbox = agent_main.EventOutbox()
        outbox.push({"type": "PRODUCT_SWITCHED", "session_id": 1, "product_id": 2, "detail": {}})
        outbox.push({"type": "PLAYBACK_ENDED", "session_id": 1, "product_id": 2, "detail": {}})
        self.assertTrue(outbox.ready.is_set())
        first, second = (e["seq"] for e in outbox.items)
        self.assertEqual(second, first + 1)
        with mock.patch.object(agent_main, "request_json", side_effect=OSError("down")):
            with self.assertRaises(OSError):
                outbox.flush("https://vps/api", "key", "agent_1")
        self.assertEqual(len(outbox.items), 2)
        with mock.patch.object(agent_main, "request_json") as request:
            self.assertEqual(outbox.flush("https://vps/api", "key", "agent_1"), 2)
        self.assertTrue(request.call_args.args[0].endswith("/agents/events"))
        self.assertEqual([e["seq"] for e in request.call_args.args[3]["events"]], [first, second])
        self.assertEqual(outbox.items, [])
        self.assertFalse(outbox.ready.is_set())


if __name__ == "__main__":
    unittest.main()
