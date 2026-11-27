"""Persisted local video metadata and lock-free, race-safe lazy inspection."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from sarae_agent.studio import service as service_module, storage as storage_module
from sarae_agent.studio.service import StudioError, StudioService, probe_video
from studio_helpers import create_product


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def video(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and FFprobe are required for real MP4 metadata tests")
    path = tmp_path / "nguồn video.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=blue:s=64x32:r=10:d=0.3", "-an", "-c:v", "mpeg4", str(path)],
                   check=True, capture_output=True, timeout=30)
    return path


def import_video(studio, video, **params):
    return studio.dispatch("studio.media.import", {"source_path": str(video), **params})["media"]


def inspect(studio, media_id):
    return studio.dispatch("studio.media.inspect", {"id": media_id})["media"]


def listed(studio):
    return studio.dispatch("studio.media.list", {})["media"]


def make_legacy(studio, media_id):
    studio._storage.connection.execute("""UPDATE media SET duration_seconds = NULL,
        width = NULL, height = NULL, metadata_probed = 0 WHERE id = ?""", (media_id,))


def fake_probe_payload(monkeypatch, stream=None, container=None, *, encoded=None, returncode=0):
    payload = {"streams": [{"codec_type": "video", "width": 640, "height": 360,
                            "duration": "2.5", **(stream or {})}],
               "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "9", **(container or {})}}
    monkeypatch.setattr(service_module.shutil, "which", lambda _: "ffprobe")

    def run(args, **kwargs):
        assert args[args.index("-protocol_whitelist") + 1] == "file"
        assert "mp4" in args[args.index("-format_whitelist") + 1].split(",")
        assert kwargs["timeout"] == 15
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        kwargs["stdout"].write(encoded if encoded is not None else json.dumps(payload).encode())
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(service_module.subprocess, "run", run)


@pytest.mark.parametrize(("stream_duration", "format_duration", "expected"), [
    ("1.25", "9", 1.25), ("N/A", "4.5", 4.5), (None, "4.5", 4.5),
    ("nan", "4.5", 4.5), ("inf", "4.5", 4.5), (0, "4.5", 4.5),
    (-1, "4.5", 4.5), (True, "4.5", 4.5), ("N/A", "N/A", None),
    (None, None, None), (0, 0, None), (-1, -1, None), ("nan", "inf", None),
    ("1e999", "-inf", None), ([], {}, None),
])
def test_probe_uses_finite_positive_stream_duration_then_format(
        monkeypatch, stream_duration, format_duration, expected):
    fake_probe_payload(monkeypatch, {"duration": stream_duration}, {"duration": format_duration})
    metadata = probe_video(Path("fixture.mp4"))
    assert metadata == {"duration_seconds": expected, "width": 640, "height": 360}
    json.dumps(metadata, allow_nan=False)


@pytest.mark.parametrize(("orientation", "swapped"), [
    ({"side_data_list": [{"rotation": -90}]}, True),
    ({"side_data_list": [{"rotation": 270}]}, True),
    ({"side_data_list": [{"rotation": 180}]}, False),
    ({"side_data_list": [{"rotation": 0}], "tags": {"rotate": "90"}}, False),
    ({"tags": {"rotate": "90"}}, True),
    ({"tags": {"rotate": "-270"}}, True),
    ({"tags": {"rotate": "N/A"}}, False),
    ({"side_data_list": [{"displaymatrix": "\n00000000: 0 -65536 0\n"
                                            "00000001: 65536 0 0\n00000002: 0 0 1073741824\n"}]}, True),
])
def test_probe_returns_display_dimensions(monkeypatch, orientation, swapped):
    fake_probe_payload(monkeypatch, orientation)
    metadata = probe_video(Path("fixture.mp4"))
    assert (metadata["width"], metadata["height"]) == ((360, 640) if swapped else (640, 360))


@pytest.mark.parametrize("failure", ["oversized", "bad_json", "rejected", "timeout", "audio_only", "image"])
def test_probe_keeps_validation_and_bounded_output(monkeypatch, failure):
    if failure == "timeout":
        fake_probe_payload(monkeypatch)

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("ffprobe", 15)

        monkeypatch.setattr(service_module.subprocess, "run", timeout)
    else:
        fake_probe_payload(monkeypatch,
                           {"codec_type": "audio"} if failure == "audio_only" else None,
                           {"format_name": "image2"} if failure == "image" else None,
                           encoded=b"x" * 65537 if failure == "oversized" else b"{" if failure == "bad_json" else None,
                           returncode=1 if failure == "rejected" else 0)
    with pytest.raises(StudioError, match=".") as caught:
        probe_video(Path("fixture.mp4"))
    assert caught.value.code == "INVALID_MEDIA"


def test_real_mp4_metadata_persists_and_inspected_import_is_not_reprobed(studio, video, monkeypatch):
    original = video.read_bytes()
    item = import_video(studio, video)
    assert item["duration_seconds"] == pytest.approx(0.3)
    assert (item["width"], item["height"], item["metadata_probed"]) == (64, 32, True)
    monkeypatch.setattr(service_module, "probe_video", lambda _: pytest.fail("Already inspected video was probed"))
    assert inspect(studio, item["id"]) == item
    studio.close()
    reopened = StudioService(data_dir=studio.data_dir)
    try:
        assert listed(reopened) == [item]
        assert inspect(reopened, item["id"]) == item
        assert reopened.resolve_media(item["id"]).read_bytes() == original
        assert video.read_bytes() == original
    finally:
        reopened.close()


def test_real_rotated_mp4_uses_display_orientation(studio, video, tmp_path):
    rotated = tmp_path / "rotated.mp4"
    result = subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                             "-display_rotation:v:0", "90", "-i", str(video), "-c", "copy", str(rotated)],
                            capture_output=True, timeout=30)
    if result.returncode:
        # Older FFmpeg versions expose rotation through an output metadata tag.
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                        "-c", "copy", "-metadata:s:v:0", "rotate=90", str(rotated)],
                       check=True, capture_output=True, timeout=30)
    item = import_video(studio, rotated)
    assert (item["width"], item["height"]) == (32, 64)
    assert item["duration_seconds"] == pytest.approx(0.3)


def test_v5_migration_preserves_files_and_links_without_probing(tmp_path, monkeypatch, video):
    root = tmp_path / "legacy"
    folder = root / "media"
    folder.mkdir(parents=True)
    product_id, presenter_id, media_id = [str(uuid4()) for _ in range(3)]
    managed = folder / (media_id + ".mp4")
    shutil.copyfile(video, managed)
    original = video.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(storage_module, "MIGRATIONS", tuple(m for m in storage_module.MIGRATIONS if m[0] <= 5))
        storage = storage_module.StudioStorage(root)
        try:
            with storage.transaction() as connection:
                connection.execute("INSERT INTO products(id,name,created_at,updated_at) VALUES (?,?,?,?)",
                                   (product_id, "Legacy product", "before", "before"))
                connection.execute("INSERT INTO presenters(id,name,created_at,updated_at) VALUES (?,?,?,?)",
                                   (presenter_id, "Legacy presenter", "before", "before"))
                connection.execute("""INSERT INTO media(id,product_id,original_name,relative_path,size_bytes,
                    sha256,mime_type,created_at) VALUES (?,?,?,?,?,?,?,?)""",
                                   (media_id, product_id, video.name, managed.name, managed.stat().st_size,
                                    "a" * 64, "video/mp4", "before"))
                connection.execute("INSERT INTO media_presenters VALUES (?,?)", (media_id, presenter_id))
        finally:
            storage.close()
    with monkeypatch.context() as patch:
        patch.setattr(service_module, "probe_video", lambda _: pytest.fail("Migration/list must not probe"))
        instance = StudioService(data_dir=root)
        try:
            item = listed(instance)[0]
            assert item["duration_seconds"] is item["width"] is item["height"] is None
            assert item["metadata_probed"] is False
            assert item["product_id"] == product_id and item["presenter_ids"] == [presenter_id]
            assert instance._storage.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == max(version for version, _ in storage_module.MIGRATIONS)
        finally:
            instance.close()
    instance = StudioService(data_dir=root)
    try:
        backfilled = inspect(instance, media_id)
        assert backfilled == {**item, "duration_seconds": pytest.approx(0.3), "width": 64,
                              "height": 32, "metadata_probed": True}
        assert instance.resolve_media(media_id) == managed
        assert managed.read_bytes() == original == video.read_bytes()
    finally:
        instance.close()


@pytest.mark.parametrize("result", [None, {"duration_seconds": None, "width": 64, "height": 32}])
def test_unknown_metadata_is_not_retried(studio, tmp_path, monkeypatch, result):
    source = tmp_path / "mock.mp4"
    source.write_bytes(b"validation-only-fixture")
    probes = []
    monkeypatch.setattr(service_module, "probe_video", lambda path: probes.append(path) or result)
    item = import_video(studio, source)
    assert item["duration_seconds"] is None and item["metadata_probed"] is True
    assert inspect(studio, item["id"]) == item
    assert len(probes) == 1
    make_legacy(studio, item["id"])
    backfilled = inspect(studio, item["id"])
    assert backfilled["metadata_probed"] is True and backfilled["duration_seconds"] is None
    assert inspect(studio, item["id"]) == backfilled
    assert len(probes) == 2


def test_inspect_preserves_known_fields_and_skips_known_dimensions(studio, video, monkeypatch):
    item = import_video(studio, video)
    make_legacy(studio, item["id"])
    studio._storage.connection.execute("UPDATE media SET duration_seconds = 7 WHERE id = ?", (item["id"],))
    result = inspect(studio, item["id"])
    assert result["duration_seconds"] == 7 and (result["width"], result["height"]) == (64, 32)
    studio._storage.connection.execute("UPDATE media SET duration_seconds = NULL, metadata_probed = 0 WHERE id = ?",
                                       (item["id"],))
    monkeypatch.setattr(service_module, "probe_video", lambda _: pytest.fail("Known dimensions must not re-probe"))
    result = inspect(studio, item["id"])
    assert result["duration_seconds"] is None and result["metadata_probed"] is True


def test_failed_inspection_preserves_media_and_allows_retry(studio, video, monkeypatch):
    item = import_video(studio, video)
    managed = studio.resolve_media(item["id"])
    original = managed.read_bytes()
    make_legacy(studio, item["id"])

    def fail(_):
        raise StudioError("INVALID_MEDIA", "Injected probe failure")

    monkeypatch.setattr(service_module, "probe_video", fail)
    with pytest.raises(StudioError) as caught:
        inspect(studio, item["id"])
    assert caught.value.code == "INVALID_MEDIA"
    remaining = listed(studio)[0]
    assert remaining["id"] == item["id"] and remaining["status"] == "ready"
    assert remaining["metadata_probed"] is False and remaining["width"] is None
    monkeypatch.setattr(service_module, "probe_video", lambda _: {"duration_seconds": 0.3, "width": 64, "height": 32})
    retried = inspect(studio, item["id"])
    assert retried["metadata_probed"] is True and retried["width"] == 64
    assert managed.read_bytes() == video.read_bytes() == original


@pytest.mark.parametrize("change", ["none", "delete", "replace", "record", "links", "close"])
def test_inspect_releases_lock_and_revalidates_before_publication(studio, video, tmp_path, monkeypatch, change):
    item = import_video(studio, video)
    make_legacy(studio, item["id"])
    managed = studio.resolve_media(item["id"])
    product = create_product(studio)
    entered, release = threading.Event(), threading.Event()

    def probe(path):
        assert path == managed
        entered.set()
        assert release.wait(5)
        return {"duration_seconds": 0.3, "width": 64, "height": 32}

    def control():
        # Every action here requires the global service lock.
        studio.dispatch("studio.status", {})
        if change == "delete":
            studio.dispatch("studio.media.delete", {"id": item["id"]})
        elif change == "replace":
            previous = managed.stat()
            replacement = tmp_path / "replacement.mp4"
            replacement.write_bytes(b"x" * previous.st_size)
            os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            os.replace(replacement, managed)
        elif change == "record":
            with studio._lock:
                studio._storage.connection.execute("UPDATE media SET sha256 = ? WHERE id = ?", ("b" * 64, item["id"]))
        elif change == "links":
            studio.dispatch("studio.media.update", {"id": item["id"], "product_id": product["id"]})
        elif change == "close":
            studio.close()

    monkeypatch.setattr(service_module, "probe_video", probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        inspecting = pool.submit(inspect, studio, item["id"])
        try:
            assert entered.wait(3)
            pool.submit(control).result(timeout=2)
        finally:
            release.set()
        if change in {"delete", "replace", "record", "close"}:
            with pytest.raises(StudioError) as caught:
                inspecting.result(timeout=3)
            assert caught.value.code == {"delete": "NOT_FOUND", "replace": "MEDIA_MISSING",
                                         "record": "MEDIA_MISSING", "close": "STORAGE_ERROR"}[change]
            if change in {"replace", "record"}:
                assert listed(studio)[0]["metadata_probed"] is False
                assert listed(studio)[0]["width"] is None
        else:
            result = inspecting.result(timeout=3)
            assert result["metadata_probed"] is True and result["width"] == 64
            assert result["product_id"] == (product["id"] if change == "links" else None)


@pytest.mark.parametrize("params", [None, {}, {"id": "../escape.mp4"}, {"id": str(uuid4()), "path": "bad"}])
def test_inspect_validates_parameters(studio, params):
    with pytest.raises(StudioError) as caught:
        studio.dispatch("studio.media.inspect", params)
    assert caught.value.code == "VALIDATION_ERROR"


def test_inspect_missing_file_never_probes(studio, video, monkeypatch):
    item = import_video(studio, video)
    make_legacy(studio, item["id"])
    studio.resolve_media(item["id"]).unlink()
    monkeypatch.setattr(service_module, "probe_video", lambda _: pytest.fail("Missing video must not be probed"))
    with pytest.raises(StudioError) as caught:
        inspect(studio, item["id"])
    assert caught.value.code == "MEDIA_MISSING"
    assert listed(studio)[0]["metadata_probed"] is False
