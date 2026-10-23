"""Cutting a Media Library video into clips with ffmpeg (needs ffmpeg/ffprobe on PATH, skipped otherwise)."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from app.storage_models import MediaItem, MediaThumbnail
from test_media_library import MediaLibraryTestCase

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")


def sample_video(seconds=6) -> bytes:
    """Portrait H.264/AAC MP4 with a keyframe every second."""
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "sample.mp4")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"testsrc2=size=180x320:rate=30:duration={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                        "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path], check=True)
        with open(path, "rb") as file:
            return file.read()


def probe(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "clip.mp4")
        with open(path, "wb") as file:
            file.write(data)
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg is not installed")
class MediaClipsTest(MediaLibraryTestCase):
    def setUp(self):
        super().setUp()
        self.connect_storage()
        self.data = sample_video()
        self.media_id = self.upload(self.data, filename="long.mp4")

    def wait(self, job, headers=None):
        for _ in range(300):
            job = self.client.get(f"/api/media-library/clip-jobs/{job['id']}", headers=headers or self.A).json()
            if job["status"] in ("DONE", "FAILED"):
                return job
            time.sleep(0.1)
        self.fail("clip job did not finish")

    def cut(self, clips, mode="COPY"):
        response = self.client.post(f"/api/media-library/{self.media_id}/clips", json={"clips": clips, "mode": mode})
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait(response.json())

    def test_copy_mode_keeps_codec_and_size_and_creates_new_videos(self):
        job = self.cut([{"start": 0, "end": 2, "name": "Mở hộp"}, {"start": 3, "end": 5}])
        self.assertEqual(job["status"], "DONE", job)
        self.assertEqual((job["done"], len(job["items"])), (2, 2))
        first, second = sorted(job["items"], key=lambda m: m["filename"])
        self.assertEqual(first["display_name"], "Mở hộp")
        self.assertEqual(second["display_name"], "long - đoạn 2")
        self.assertEqual((first["width"], first["height"]), (180, 320))
        self.assertTrue(first["has_thumbnail"])
        clip = self.client.get(second["stream_url"], headers=self.ANON).content
        info = probe(clip)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["codec_name"], video["width"], video["height"]), ("h264", 180, 320))
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 2, delta=0.3)
        self.assertEqual(self.media(self.media_id).json()["status"], "AVAILABLE")  # the original is kept
        with self.Session() as db:
            self.assertEqual(db.query(MediaItem).count(), 3)
            self.assertEqual(db.query(MediaThumbnail).count(), 2)

    def test_copy_mode_starts_on_the_last_keyframe_before_the_cut(self):
        keys = self.client.get(f"/api/media-library/{self.media_id}/keyframes").json()
        self.assertEqual(keys["keyframes"][:3], [0.0, 1.0, 2.0])
        job = self.cut([{"start": 3.6, "end": 5.0}])
        self.assertEqual(job["status"], "DONE", job)
        self.assertAlmostEqual(job["items"][0]["duration_seconds"], 2.0, delta=0.15)  # from keyframe 3.0 to 5.0

    def test_precise_mode_cuts_on_the_exact_frame(self):
        job = self.cut([{"start": 1.5, "end": 3.0}], mode="PRECISE")
        self.assertEqual(job["status"], "DONE", job)
        self.assertAlmostEqual(job["items"][0]["duration_seconds"], 1.5, delta=0.1)

    def test_invalid_clips_and_other_owners_are_refused(self):
        bad = self.client.post(f"/api/media-library/{self.media_id}/clips", json={"clips": [{"start": 3, "end": 2}]})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.post(f"/api/media-library/{self.media_id}/clips", json={"clips": [{"start": 0, "end": 1}]},
                                          headers=self.B).status_code, 404)
        job = self.client.post(f"/api/media-library/{self.media_id}/clips", json={"clips": [{"start": 0, "end": 1}]}).json()
        self.assertEqual(self.client.get(f"/api/media-library/clip-jobs/{job['id']}", headers=self.B).status_code, 404)
        self.wait(job)
