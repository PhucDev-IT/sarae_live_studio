"""Actual frames, retryable failures, and publication races for local thumbnails."""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import cv2
import numpy as np
import pytest

from sarae_agent.studio import media_thumbnails as frames, service as service_module
from sarae_agent.studio.service import StudioError, StudioService


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(tmp_path / "studio")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def imported(studio, tmp_path, monkeypatch):
    source = tmp_path / "nguồn.mp4"
    source.write_bytes(b"managed-original-video")
    monkeypatch.setattr(service_module, "probe_video", lambda _: {
        "duration_seconds": 12.0, "width": 1280, "height": 720})
    item = studio.dispatch("studio.media.import", {"source_path": str(source)})["media"]
    return item, source


def thumbnail(studio, item):
    return studio.dispatch("studio.media.thumbnail", {"id": item["id"]})["media"]


def jpeg():
    image = np.zeros((32, 64, 3), dtype=np.uint8)
    image[:, :, 0] = 240
    return cv2.imencode(".jpg", image)[1].tobytes()


def fake_frame(source, output, allowed, duration):
    output.write(jpeg())
    output.flush()


def test_real_frame_and_persistent_cache(studio, tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/FFprobe are required for the real frame test")
    source = tmp_path / "real blue.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=1280x720:r=10:d=0.4", "-c:v", "mpeg4", str(source)],
                   check=True, capture_output=True, timeout=20)
    item = studio.dispatch("studio.media.import", {"source_path": str(source)})["media"]
    assert item["has_thumbnail"] is False and item["thumbnail_url"] is None
    assert not (studio.data_dir / "thumbnails").exists()
    with pytest.raises(StudioError) as caught:
        studio.resolve_media_thumbnail(item["id"])
    assert caught.value.code == "NOT_FOUND"
    assert not (studio.data_dir / "thumbnails").exists()
    original = source.read_bytes()
    result = thumbnail(studio, item)
    path = studio.resolve_media_thumbnail(item["id"])
    assert result == {**item, "has_thumbnail": True}
    assert path.parent == studio.data_dir / "thumbnails" / "media"
    assert path.name == f"{item['id']}-{item['sha256']}.jpg"
    data = path.read_bytes()
    assert 0 < len(data) <= frames.MAX_JPEG_BYTES
    assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (360, 640)
    assert image[180, 320, 0] > 200 and image[180, 320, 2] < 30
    modified = path.stat().st_mtime_ns
    monkeypatch.setattr(frames, "resolve_ffmpeg", lambda: pytest.fail("Cached hits must not need FFmpeg"))
    assert thumbnail(studio, item) == result
    studio.close()
    reopened = StudioService(studio.data_dir)
    try:
        assert thumbnail(reopened, item) == result
        assert reopened.resolve_media_thumbnail(item["id"]).stat().st_mtime_ns == modified
        assert reopened.resolve_media(item["id"]).read_bytes() == source.read_bytes() == original
    finally:
        reopened.close()


def test_list_and_resolver_never_generate_or_decode(studio, imported, monkeypatch):
    item, _ = imported
    monkeypatch.setattr(frames, "generate_frame", fake_frame)
    result = thumbnail(studio, item)
    monkeypatch.setattr(frames, "generate_frame", lambda *a: pytest.fail("Read must not generate frames"))
    monkeypatch.setattr(frames.cv2, "imdecode", lambda *a: pytest.fail("List must not decode JPEGs"))
    assert studio.dispatch("studio.media.list", {})["media"] == [result]
    assert studio.resolve_media_thumbnail(item["id"]).is_file()


@pytest.mark.parametrize("failure", ["missing", "rejected", "timeout"])
def test_generator_failure_preserves_originals_and_allows_retry(studio, imported, monkeypatch, failure):
    item, source = imported
    original = source.read_bytes()

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffmpeg", 20)
        return SimpleNamespace(returncode=1)

    if failure == "missing":
        def unavailable():
            raise StudioError("FFMPEG_UNAVAILABLE", "Missing test generator")
        monkeypatch.setattr(frames, "resolve_ffmpeg", unavailable)
    else:
        monkeypatch.setattr(frames, "resolve_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(frames.subprocess, "run", run)
    with pytest.raises(StudioError) as caught:
        thumbnail(studio, item)
    assert caught.value.code == ("FFMPEG_UNAVAILABLE" if failure == "missing" else "THUMBNAIL_FAILED")
    assert studio.dispatch("studio.media.list", {})["media"] == [item]
    assert source.read_bytes() == studio.resolve_media(item["id"]).read_bytes() == original
    assert list(frames.cache_dir(studio.data_dir).iterdir()) == []
    monkeypatch.setattr(frames, "generate_frame", fake_frame)
    assert thumbnail(studio, item)["has_thumbnail"] is True


def test_ffmpeg_options_and_first_frame_fallback(studio, imported, monkeypatch):
    item, _ = imported
    calls = []
    monkeypatch.setattr(frames, "resolve_ffmpeg", lambda: "ffmpeg")

    def run(args, **kwargs):
        calls.append(args)
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        assert set(args[args.index("-format_whitelist") + 1].split(",")) == service_module.VIDEO_FORMATS[".mp4"][0]
        assert args[args.index("-threads") + 1] == "1"
        assert args[args.index("-hwaccel") + 1] == "none"
        assert args[args.index("-frames:v") + 1] == "1" and "-an" in args
        assert 0 < kwargs["timeout"] <= 20
        assert kwargs["stdin"] == kwargs["stderr"] == subprocess.DEVNULL
        if len(calls) == 2:
            kwargs["stdout"].write(jpeg())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(frames.subprocess, "run", run)
    assert thumbnail(studio, item)["has_thumbnail"] is True
    assert [float(args[args.index("-ss") + 1]) for args in calls] == [1.0, 0.0]


@pytest.mark.parametrize("change", ["controls", "delete", "replace", "record", "close"])
def test_generation_releases_lock_and_revalidates(studio, imported, tmp_path, monkeypatch, change):
    item, source = imported
    managed = studio.resolve_media(item["id"])
    entered, release = threading.Event(), threading.Event()

    def generate(*args):
        entered.set()
        assert release.wait(5)
        fake_frame(*args)

    def control():
        studio.dispatch("studio.status", {})
        if change == "delete":
            studio.dispatch("studio.media.delete", {"id": item["id"]})
        elif change == "replace":
            before = managed.stat()
            replacement = tmp_path / "replacement.mp4"
            replacement.write_bytes(b"x" * before.st_size)
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(replacement, managed)
        elif change == "record":
            with studio._lock:
                studio._storage.connection.execute("UPDATE media SET sha256 = ? WHERE id = ?", ("b" * 64, item["id"]))
        elif change == "close":
            studio.close()
        else:
            studio.dispatch("studio.media.update", {"id": item["id"], "presenter_ids": []})

    monkeypatch.setattr(frames, "generate_frame", generate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        generating = pool.submit(thumbnail, studio, item)
        try:
            assert entered.wait(3)
            pool.submit(control).result(timeout=2)
        finally:
            release.set()
        if change == "controls":
            assert generating.result(timeout=3)["has_thumbnail"] is True
        else:
            with pytest.raises(StudioError) as caught:
                generating.result(timeout=3)
            assert caught.value.code == {"delete": "NOT_FOUND", "replace": "MEDIA_MISSING",
                                         "record": "MEDIA_MISSING", "close": "STORAGE_ERROR"}[change]
            assert list(frames.cache_dir(studio.data_dir).iterdir()) == []
    assert source.read_bytes() == b"managed-original-video"


def test_delete_only_removes_corresponding_managed_cache(studio, imported, monkeypatch):
    item, source = imported
    monkeypatch.setattr(frames, "generate_frame", fake_frame)
    thumbnail(studio, item)
    cached = studio.resolve_media_thumbnail(item["id"])
    product_image = studio.data_dir / "thumbnails" / (item["id"] + ".jpg")
    product_image.write_bytes(jpeg())
    other = cached.with_name(frames.cache_name(str(uuid4()), "a" * 64))
    other.write_bytes(jpeg())
    studio.dispatch("studio.media.delete", {"id": item["id"]})
    assert not cached.exists()
    assert product_image.exists() and other.exists() and source.exists()
    with pytest.raises(StudioError) as caught:
        studio.resolve_media_thumbnail(item["id"])
    assert caught.value.code == "NOT_FOUND"


def test_corrupt_cache_retries_and_paths_are_validated(studio, imported, tmp_path, monkeypatch):
    item, _ = imported
    monkeypatch.setattr(frames, "generate_frame", fake_frame)
    thumbnail(studio, item)
    cached = studio.resolve_media_thumbnail(item["id"])
    cached.write_bytes(b"\xff\xd8invalid\xff\xd9")
    assert studio.dispatch("studio.media.list", {})["media"][0]["has_thumbnail"] is False
    assert thumbnail(studio, item)["has_thumbnail"] is True
    # Unsafe cache entries must never be served or removed. Some Windows
    # sandboxes deny hard links; a directory exercises the same rejection there.
    outside = tmp_path / "user.jpg"
    outside.write_bytes(jpeg())
    cached.unlink()
    try:
        os.link(outside, cached)
    except PermissionError:
        cached.mkdir()
    with pytest.raises(StudioError) as caught:
        studio.resolve_media_thumbnail(item["id"])
    assert caught.value.code == "UNSAFE_PATH"
    studio.dispatch("studio.media.delete", {"id": item["id"]})
    assert outside.read_bytes() == jpeg() and cached.exists()
    for params in (None, {}, {"id": "../user.jpg"}, {"id": item["id"], "path": "bad"}):
        with pytest.raises(StudioError) as caught:
            studio.dispatch("studio.media.thumbnail", params)
        assert caught.value.code == "VALIDATION_ERROR"
    with pytest.raises(StudioError):
        frames.cache_name(str(uuid4()), "../bad")


def test_cache_directory_junction_is_rejected(studio, imported, tmp_path, monkeypatch):
    item, _ = imported
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = studio.data_dir / "thumbnails"
    if os.name == "nt":
        import _winapi
        try:
            _winapi.CreateJunction(str(outside), str(redirected))
        except PermissionError:
            pytest.skip("The Windows sandbox disallows junction creation")
    else:
        redirected.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(frames, "generate_frame", lambda *a: pytest.fail("Unsafe directory must not generate"))
    with pytest.raises(StudioError) as caught:
        thumbnail(studio, item)
    assert caught.value.code == "UNSAFE_PATH"
    assert list(outside.iterdir()) == []


def test_frozen_executable_precedes_override_and_path(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    bundled = bundle / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    bundled.write_bytes(b"test")
    bundled.chmod(0o755)
    monkeypatch.setattr(frames.sys, "frozen", True, raising=False)
    monkeypatch.setattr(frames.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("SARAE_FFMPEG", "ffmpeg && invalid")
    monkeypatch.setattr(frames.shutil, "which", lambda _: None)
    assert frames.resolve_ffmpeg() == str(bundled)
    bundled.unlink()
    monkeypatch.setattr(frames.sys, "executable", str(tmp_path / "sidecar.exe"))
    with pytest.raises(StudioError) as caught:
        frames.resolve_ffmpeg()
    assert caught.value.code == "FFMPEG_UNAVAILABLE"
