"""Local Studio persistence and managed-video import contract.

Only the video fixtures require FFmpeg/FFprobe. All data lives below tmp_path;
these tests never open the user's Studio database or import private media.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sarae_agent.studio import service as studio_module
from sarae_agent.studio.service import StudioError, StudioService
from studio_helpers import create_product


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def video_tools():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and FFprobe are required to generate and validate real video")
    return ffmpeg, ffprobe


@pytest.fixture
def video(tmp_path, video_tools):
    path = tmp_path / "nguồn video tiếng Việt.mp4"
    subprocess.run(
        [video_tools[0], "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=blue:s=32x32:r=10:d=0.3",
         "-an", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=30,
    )
    assert path.stat().st_size > 0
    return path


def call(studio, method, **params):
    result = studio.dispatch(method, params)
    assert isinstance(result, dict)
    return result


def products(studio):
    return call(studio, "studio.products.list")["products"]


def media(studio):
    return call(studio, "studio.media.list")["media"]


def import_video(studio, path, **params):
    return call(studio, "studio.media.import", source_path=str(path), **params)["media"]


def assert_error(code, operation):
    with pytest.raises(StudioError) as caught:
        operation()
    error = caught.value
    assert error.code == code
    assert isinstance(error.message, str) and error.message.strip()
    assert error.details is None or isinstance(error.details, dict)
    return error


def assert_uuid(value):
    assert isinstance(value, str)
    assert str(UUID(value)) == value


def assert_timestamp(value):
    assert isinstance(value, str)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def library_files(tmp_path):
    root = tmp_path / "studio" / "media"
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


def test_fresh_store_is_empty_and_status_is_serializable(studio, tmp_path):
    import json

    status = call(studio, "studio.status")
    json.dumps(status, allow_nan=False)
    assert products(studio) == []
    assert media(studio) == []
    assert (tmp_path / "studio" / "studio.sqlite3").is_file()


@pytest.mark.parametrize("use_override", [False, True])
def test_default_data_directory_and_environment_override(tmp_path, monkeypatch, use_override):
    expected = tmp_path / "local-app-data" / "SaraeLiveStudio"
    if use_override:
        expected = tmp_path / "Studio tùy chọn"
        monkeypatch.setenv("SARAE_STUDIO_DATA_DIR", str(expected))
    instance = StudioService()
    try:
        item = create_product(instance)
        assert (expected / "studio.sqlite3").is_file()
    finally:
        instance.close()
    reopened = StudioService(data_dir=expected)
    try:
        assert products(reopened) == [item]
    finally:
        reopened.close()


def test_explicit_directory_takes_precedence_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SARAE_STUDIO_DATA_DIR", str(tmp_path / "environment"))
    explicit = tmp_path / "explicit"
    instance = StudioService(data_dir=explicit)
    try:
        create_product(instance)
        assert (explicit / "studio.sqlite3").is_file()
        assert not (tmp_path / "environment" / "studio.sqlite3").exists()
    finally:
        instance.close()


@pytest.mark.parametrize("method", ["studio.products.update", "studio.products.delete", "studio.media.delete"])
def test_missing_records_return_not_found(studio, method):
    params = {"id": str(uuid4())}
    if method.endswith("update"):
        params["description"] = "Missing"
    assert_error("NOT_FOUND", lambda: call(studio, method, **params))


def test_unknown_method_returns_structured_error(studio):
    assert_error("METHOD_NOT_FOUND", lambda: call(studio, "studio.does.not.exist"))


def test_real_video_import_metadata_and_managed_copy(studio, video, tmp_path):
    product = create_product(studio)
    original = video.read_bytes()
    item = import_video(studio, video, product_id=product["id"])
    assert_uuid(item["id"])
    assert item["product_id"] == product["id"]
    assert item["original_name"] == video.name
    assert item["size_bytes"] == len(original)
    assert item["sha256"] == hashlib.sha256(original).hexdigest()
    assert item["storage_mode"] == "managed"
    assert item["status"] == "ready"
    assert item["mime_type"] == "video/mp4"
    assert_timestamp(item["created_at"])
    assert media(studio) == [item]

    resolved = studio.resolve_media(item["id"])
    assert isinstance(resolved, Path)
    assert resolved.resolve().is_relative_to((tmp_path / "studio" / "media").resolve())
    assert resolved.resolve() != video.resolve()
    assert resolved.read_bytes() == original
    assert video.read_bytes() == original
    assert len(library_files(tmp_path)) == 1


def test_reopen_preserves_product_media_ids_and_resolver(tmp_path, video):
    root = tmp_path / "studio"
    first = StudioService(data_dir=root)
    try:
        product = create_product(first)
        item = import_video(first, video, product_id=product["id"])
        managed = first.resolve_media(item["id"])
        digest = item["sha256"]
    finally:
        first.close()
    # A managed import must continue working after its external source disappears.
    video.unlink()
    second = StudioService(data_dir=root)
    try:
        assert products(second) == [product]
        assert media(second) == [item]
        assert second.resolve_media(item["id"]) == managed
        assert hashlib.sha256(managed.read_bytes()).hexdigest() == digest
        another = create_product(second)
        assert another["id"] != product["id"]
    finally:
        second.close()


def test_delete_product_unlinks_media_without_removing_files(studio, video):
    product = create_product(studio)
    item = import_video(studio, video, product_id=product["id"])
    managed = studio.resolve_media(item["id"])
    original = video.read_bytes()
    call(studio, "studio.products.delete", id=product["id"])
    assert products(studio) == []
    remaining = media(studio)
    assert len(remaining) == 1
    assert remaining[0]["id"] == item["id"]
    assert remaining[0]["product_id"] is None
    assert remaining[0]["status"] == "ready"
    assert studio.resolve_media(item["id"]) == managed
    assert managed.read_bytes() == original
    assert video.read_bytes() == original


def test_delete_media_removes_managed_copy_only(studio, video, tmp_path):
    product = create_product(studio)
    original = video.read_bytes()
    item = import_video(studio, video, product_id=product["id"])
    managed = studio.resolve_media(item["id"])
    call(studio, "studio.media.delete", id=item["id"])
    assert media(studio) == []
    assert products(studio) == [product]
    assert not managed.exists()
    assert video.read_bytes() == original
    assert library_files(tmp_path) == set()
    assert_error("NOT_FOUND", lambda: studio.resolve_media(item["id"]))


def test_missing_managed_file_is_reported_and_can_be_deleted(studio, video):
    item = import_video(studio, video)
    studio.resolve_media(item["id"]).unlink()
    listed = media(studio)
    assert len(listed) == 1
    assert listed[0]["id"] == item["id"]
    assert listed[0]["status"] == "missing"
    assert_error("MEDIA_MISSING", lambda: studio.resolve_media(item["id"]))
    call(studio, "studio.media.delete", id=item["id"])
    assert media(studio) == []
    assert video.is_file()


def test_import_without_product_creates_unlinked_media(studio, video):
    item = import_video(studio, video)
    assert item["product_id"] is None
    assert item["status"] == "ready"


def test_nonexistent_product_import_rolls_back(studio, video, tmp_path):
    original = video.read_bytes()
    assert_error("NOT_FOUND", lambda: import_video(studio, video, product_id=str(uuid4())))
    assert media(studio) == []
    assert library_files(tmp_path) == set()
    assert video.read_bytes() == original


@pytest.mark.parametrize("kind", ["relative", "missing", "directory", "extension", "corrupt"])
def test_invalid_source_does_not_publish_media(studio, tmp_path, kind, video_tools):
    source = tmp_path / "invalid.mp4"
    code = "INVALID_MEDIA"
    if kind == "relative":
        source = Path("relative.mp4")
        code = "VALIDATION_ERROR"
    elif kind == "directory":
        source.mkdir()
    elif kind == "extension":
        source = tmp_path / "invalid.txt"
        source.write_bytes(b"not a video")
    elif kind == "corrupt":
        source.write_bytes(b"this is not an MP4 video")
    assert_error(code, lambda: import_video(studio, source))
    assert media(studio) == []
    assert library_files(tmp_path) == set()


def test_audio_only_mp4_is_not_a_video(studio, tmp_path, video_tools):
    source = tmp_path / "audio-only.mp4"
    subprocess.run(
        [video_tools[0], "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.2", "-vn", "-c:a", "aac", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    assert_error("INVALID_MEDIA", lambda: import_video(studio, source))
    assert media(studio) == []
    assert library_files(tmp_path) == set()
    assert source.is_file()


@pytest.mark.parametrize("code", ["INVALID_MEDIA", "PROBE_UNAVAILABLE"])
def test_probe_failure_leaves_no_record_or_partial_copy(studio, video, tmp_path, monkeypatch, code):
    original = video.read_bytes()

    def fail_probe(path):
        raise StudioError(code, "Injected probe failure", {"fixture": True})

    monkeypatch.setattr(studio_module, "probe_video", fail_probe)
    assert_error(code, lambda: import_video(studio, video))
    assert media(studio) == []
    assert library_files(tmp_path) == set()
    assert video.read_bytes() == original


@pytest.mark.parametrize("unsafe_id", ["../outside.mp4", "..\\outside.mp4", "/tmp/outside.mp4", "C:\\outside.mp4"])
@pytest.mark.parametrize("operation", ["resolve", "delete"])
def test_media_ids_cannot_be_used_as_paths(studio, tmp_path, unsafe_id, operation):
    sentinel = tmp_path / "outside.mp4"
    sentinel.write_bytes(b"do not touch")
    if operation == "resolve":
        action = lambda: studio.resolve_media(unsafe_id)
    else:
        action = lambda: call(studio, "studio.media.delete", id=unsafe_id)
    assert_error("VALIDATION_ERROR", action)
    assert sentinel.read_bytes() == b"do not touch"
    assert media(studio) == []


@pytest.mark.parametrize("kind", ["renamed_container", "renamed_image"])
def test_extension_cannot_disguise_container_or_image(studio, video, tmp_path, video_tools, kind):
    source = tmp_path / ("disguised.avi" if kind == "renamed_container" else "disguised.mp4")
    if kind == "renamed_container":
        shutil.copyfile(video, source)
    else:
        image = tmp_path / "frame.bmp"
        subprocess.run(
            [video_tools[0], "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
             "-frames:v", "1", str(image)],
            check=True, capture_output=True, timeout=30,
        )
        image.rename(source)
    original = source.read_bytes()
    assert_error("INVALID_MEDIA", lambda: import_video(studio, source))
    assert media(studio) == []
    assert library_files(tmp_path) == set()
    assert source.read_bytes() == original


def test_import_streams_hashes_and_atomically_publishes(studio, tmp_path, monkeypatch):
    chunk_size = 65536
    original = bytes(range(256)) * 769
    source = tmp_path / "streamed.mp4"
    source.write_bytes(original)
    reads, probes, publications = [], [], []
    real_open, real_replace = Path.open, studio_module.os.replace
    monkeypatch.setattr(studio_module, "COPY_CHUNK_BYTES", chunk_size)

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert 0 < size <= chunk_size, "Video reads must have a bounded size"
            result = self.stream.read(size)
            reads.append(len(result))
            return result

    def observed_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return Reader(stream) if path == source and mode == "rb" else stream

    def probe(staged):
        assert staged != source
        assert staged.parent == tmp_path / "studio" / "media"
        assert ".partial" in staged.name
        assert staged.read_bytes() == original
        assert media(studio) == []
        probes.append(staged)

    def publish(staged, target):
        staged, target = Path(staged), Path(target)
        assert probes == [staged]
        assert staged.parent == target.parent
        assert not target.exists()
        assert media(studio) == []
        assert staged.read_bytes() == original
        publications.append(target)
        return real_replace(staged, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", observed_open)
        patch.setattr(studio_module, "probe_video", probe)
        patch.setattr(studio_module.os, "replace", publish)
        item = import_video(studio, source)
    assert len([size for size in reads if size]) > 2
    assert sum(reads) == len(original)
    assert item["sha256"] == hashlib.sha256(original).hexdigest()
    assert item["size_bytes"] == len(original)
    assert publications == [studio.resolve_media(item["id"])]
    assert source.read_bytes() == original
    assert len(library_files(tmp_path)) == 1


def test_failed_atomic_publish_removes_staging_copy(studio, video, tmp_path, monkeypatch):
    original = video.read_bytes()

    def fail_publish(*args, **kwargs):
        raise OSError("Injected disk failure")

    monkeypatch.setattr(studio_module.os, "replace", fail_publish)
    assert_error("STORAGE_ERROR", lambda: import_video(studio, video))
    assert media(studio) == []
    assert library_files(tmp_path) == set()
    assert video.read_bytes() == original


@pytest.mark.parametrize("operation", ["insert", "delete"])
def test_database_failure_rolls_back_media_and_managed_files(studio, video, tmp_path, operation):
    original = video.read_bytes()
    item = import_video(studio, video) if operation == "delete" else None
    before = media(studio)
    files_before = library_files(tmp_path)
    # Private DB access is reserved for deterministic persistence failure injection.
    connection = studio._storage.connection
    connection.execute(f"""CREATE TRIGGER fail_media_write BEFORE {operation.upper()} ON media
        BEGIN SELECT RAISE(ABORT, 'Injected database failure'); END""")
    try:
        action = (lambda: call(studio, "studio.media.delete", id=item["id"])) if item else (
            lambda: import_video(studio, video))
        assert_error("STORAGE_ERROR", action)
        assert media(studio) == before
        assert library_files(tmp_path) == files_before
        if item:
            assert studio.resolve_media(item["id"]).read_bytes() == original
    finally:
        connection.execute("DROP TRIGGER fail_media_write")
    assert video.read_bytes() == original
    # The connection remains usable after rolling back the failed transaction.
    if item:
        call(studio, "studio.media.delete", id=item["id"])
    else:
        assert import_video(studio, video)["status"] == "ready"


def test_corrupt_database_path_is_never_resolved_or_deleted(studio, video, tmp_path):
    item = import_video(studio, video)
    managed = studio.resolve_media(item["id"])
    outside = tmp_path / "studio" / "outside.mp4"
    outside.write_bytes(b"external sentinel")
    studio._storage.connection.execute(
        "UPDATE media SET relative_path = ? WHERE id = ?", ("../outside.mp4", item["id"]))
    assert_error("UNSAFE_PATH", lambda: studio.resolve_media(item["id"]))
    assert_error("UNSAFE_PATH", lambda: call(studio, "studio.media.delete", id=item["id"]))
    assert media(studio)[0]["status"] == "missing"
    assert outside.read_bytes() == b"external sentinel"
    assert managed.is_file()
    assert video.is_file()


def test_failed_migration_rolls_back_ddl_and_version(tmp_path, monkeypatch):
    from sarae_agent.studio import storage as storage_module

    root = tmp_path / "studio"
    first = StudioService(data_dir=root)
    try:
        product = create_product(first)
    finally:
        first.close()
    migrations = storage_module.MIGRATIONS
    version = max(number for number, _ in migrations) + 1
    broken = (version, ("CREATE TABLE migration_test (id INTEGER)",
                        "INSERT INTO missing_migration_test_table VALUES (1)"))
    with monkeypatch.context() as patch:
        patch.setattr(storage_module, "MIGRATIONS", migrations + (broken,))
        assert_error("STORAGE_ERROR", lambda: StudioService(data_dir=root))
    # Inspect on a fresh connection; a leaked startup transaction would lock it.
    connection = sqlite3.connect(root / "studio.sqlite3", timeout=1)
    try:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'migration_test'").fetchall() == []
        assert connection.execute("SELECT version FROM schema_migrations WHERE version = ?", (version,)).fetchall() == []
    finally:
        connection.close()
    reopened = StudioService(data_dir=root)
    try:
        assert products(reopened) == [product]
        assert create_product(reopened)["id"] != product["id"]
    finally:
        reopened.close()


def test_restart_recovers_delete_tombstones_and_import_debris(tmp_path, video):
    root = tmp_path / "studio"
    first = StudioService(data_dir=root)
    try:
        item = import_video(first, video)
        managed = first.resolve_media(item["id"])
    finally:
        first.close()
    original = managed.read_bytes()
    managed.rename(managed.with_name(".delete-" + managed.name))
    folder = root / "media"
    for name in (f".delete-{uuid4()}.mp4", f"{uuid4()}.mp4", ".import-interrupted.partial.mp4"):
        (folder / name).write_bytes(b"interrupted operation")
    unrelated = folder / "user-notes.txt"
    unrelated.write_bytes(b"preserve unrelated file")
    reopened = StudioService(data_dir=root)
    try:
        assert media(reopened) == [item]
        assert reopened.resolve_media(item["id"]).read_bytes() == original
        assert library_files(tmp_path) == {Path(managed.name), Path(unrelated.name)}
        assert unrelated.read_bytes() == b"preserve unrelated file"
        assert video.read_bytes() == original
    finally:
        reopened.close()


def test_library_directory_redirection_is_rejected(studio, video, tmp_path):
    item = import_video(studio, video)
    folder = tmp_path / "studio" / "media"
    saved = folder.with_name("saved-media")
    outside = tmp_path / "outside-library"
    outside.mkdir()
    sentinel = outside / studio.resolve_media(item["id"]).name
    sentinel.write_bytes(b"outside sentinel")
    folder.rename(saved)
    try:
        try:
            folder.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(folder), str(outside)],
                capture_output=True, timeout=10,
            )
            if result.returncode:
                pytest.skip("Directory symlinks/junctions unavailable")
        assert_error("UNSAFE_PATH", lambda: studio.resolve_media(item["id"]))
        assert_error("UNSAFE_PATH", lambda: call(studio, "studio.media.delete", id=item["id"]))
        assert_error("UNSAFE_PATH", lambda: import_video(studio, video))
        assert media(studio)[0]["status"] == "missing"
        assert sentinel.read_bytes() == b"outside sentinel"
        assert list(outside.iterdir()) == [sentinel]
        assert_error("UNSAFE_PATH", lambda: StudioService(data_dir=tmp_path / "studio"))
    finally:
        if folder.is_symlink():
            folder.unlink()
        elif folder.exists():
            folder.rmdir()  # Remove the junction itself, never its contents.
        saved.rename(folder)


def test_close_waits_for_active_import_and_persists_result(tmp_path, monkeypatch):
    source = tmp_path / "concurrent.mp4"
    source.write_bytes(b"injected-probe-video" * 100)
    instance = StudioService(data_dir=tmp_path / "studio")
    probe_entered, release_probe = threading.Event(), threading.Event()
    close_started, close_done = threading.Event(), threading.Event()
    errors, imported = [], []

    def probe(path):
        probe_entered.set()
        assert release_probe.wait(5), "Timed out waiting to release probe"

    def run_import():
        try:
            imported.append(import_video(instance, source))
        except BaseException as exc:
            errors.append(exc)

    def run_close():
        close_started.set()
        try:
            instance.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            close_done.set()

    monkeypatch.setattr(studio_module, "probe_video", probe)
    importer = threading.Thread(target=run_import, daemon=True)
    closer = threading.Thread(target=run_close, daemon=True)
    importer.start()
    try:
        assert probe_entered.wait(3), "Import did not reach probe"
        closer.start()
        assert close_started.wait(1)
        assert not close_done.wait(0.1), "close returned while import was active"
    finally:
        release_probe.set()
        importer.join(timeout=5)
        if closer.ident is not None:
            closer.join(timeout=5)
    assert not importer.is_alive() and not closer.is_alive()
    assert close_done.is_set()
    assert errors == []
    assert len(imported) == 1
    instance.close()  # Idempotence after a concurrent close.
    reopened = StudioService(data_dir=tmp_path / "studio")
    try:
        assert media(reopened) == imported
        assert reopened.resolve_media(imported[0]["id"]).read_bytes() == source.read_bytes()
    finally:
        reopened.close()


def test_restart_removes_only_stale_upload_folders(tmp_path):
    root = tmp_path / "studio"
    uploads = root / "imports"
    stale = uploads / "upload-interrupted"
    stale.mkdir(parents=True)
    original = tmp_path / "original-name.mp4"
    original.write_bytes(b"external original")
    (stale / original.name).write_bytes(b"incomplete upload")
    unrelated = uploads / "user-folder"
    unrelated.mkdir()
    (unrelated / original.name).write_bytes(b"unrelated content")
    nested = uploads / "upload-nested"
    (nested / "child").mkdir(parents=True)
    (nested / "preserve.txt").write_bytes(b"nested folder is excluded")
    instance = StudioService(data_dir=root)
    try:
        assert not stale.exists()
        assert (unrelated / original.name).read_bytes() == b"unrelated content"
        assert (nested / "child").is_dir()
        assert (nested / "preserve.txt").read_bytes() == b"nested folder is excluded"
        assert original.read_bytes() == b"external original"
    finally:
        instance.close()
