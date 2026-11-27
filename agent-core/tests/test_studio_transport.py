"""Transport integration: same real SQLite service over HTTP and JSON Lines IPC."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest

from sarae_agent.studio import service as service_module
from sarae_agent.studio.__main__ import RuntimeLock
from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.transport import StudioHTTPServer, StudioTransport
from studio_helpers import create_product


@pytest.fixture
def local_http(tmp_path, monkeypatch):
    # Media codec validation has its own storage tests. Here bytes verify streaming.
    monkeypatch.setattr(service_module, "probe_video", lambda _: None)
    service = StudioService(tmp_path / "studio")
    transport = StudioTransport(service)
    server = StudioHTTPServer(transport)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    yield service, transport, call
    transport.stopped.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    service.close()


def auth(transport):
    return {"Authorization": f"Bearer {transport.token}", "Content-Type": "application/json"}


@pytest.mark.parametrize("native", [False, True])
def test_real_video_thumbnail_is_local_and_token_protected(local_http, native):
    service, transport, call = local_http
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for video thumbnail integration")
    source = service.data_dir / "thumbnail-source.mp4"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=blue:s=96x160:d=0.3", "-an", "-c:v", "mpeg4", str(source)],
                   capture_output=True, check=True, timeout=15)
    original = source.read_bytes()
    media = service.dispatch("studio.media.import", {"source_path": str(source)})["media"]
    transport.native = native
    if native:
        result = transport.dispatch("studio.media.thumbnail", {"id": media["id"]})
    else:
        status, body = rpc(call, transport, "studio.media.thumbnail", {"id": media["id"]})
        assert status == 200
        result = body["result"]
    item = result["media"]
    assert item["has_thumbnail"] is True
    thumbnail_url = urlsplit(item["thumbnail_url"])
    path = thumbnail_url.path + "?" + thumbnail_url.query
    assert call("GET", thumbnail_url.path)[0] == 401
    assert call("GET", path, headers={"Origin": "https://evil.example"})[0] == 403
    status, headers, image = call("GET", path)
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    assert image.startswith(b"\xff\xd8") and image.endswith(b"\xff\xd9")
    assert call("HEAD", path)[2] == b""
    assert source.read_bytes() == original
    assert transport.dispatch("studio.media.list", {})["media"][0]["thumbnail_url"] == item["thumbnail_url"]
    assert call("GET", "/studio/media-thumbnails/..%2Fprivate?token=" + transport.token)[0] == 404


def test_metadata_inspection_uses_shared_service_and_emits_update(local_http, monkeypatch):
    service, transport, call = local_http
    source = service.data_dir / "old-video.mp4"
    source.write_bytes(b"probe-injected-fixture")
    media = service.dispatch("studio.media.import", {"source_path": str(source)})["media"]
    with service._storage.transaction() as connection:
        connection.execute("UPDATE media SET metadata_probed = 0 WHERE id = ?", (media["id"],))
    monkeypatch.setattr(service_module, "probe_video", lambda _: {"duration_seconds": 12.5, "width": 1080, "height": 1920})
    listener = transport.add_listener()
    try:
        status, body = rpc(call, transport, "studio.media.inspect", {"id": media["id"]})
        assert status == 200
        inspected = body["result"]["media"]
        assert inspected["id"] == media["id"] and inspected["metadata_probed"] is True
        assert (inspected["duration_seconds"], inspected["width"], inspected["height"]) == (12.5, 1080, 1920)
        assert listener.get_nowait()["data"]["method"] == "studio.media.inspect"
        assert service.dispatch("studio.media.list", {})["media"] == [inspected]
    finally:
        transport.remove_listener(listener)


def rpc(call, transport, method, params=None):
    status, _, body = call("POST", "/studio/rpc", json.dumps({"method": method, "params": params or {}}), auth(transport))
    return status, json.loads(body)


def test_loopback_session_and_mutations_require_token_and_allowed_origin(local_http):
    service, transport, call = local_http
    assert call("GET", "/studio/session")[0] == 403
    assert call("GET", "/studio/session", headers={"X-Studio-Client": "1", "Origin": "https://evil.example"})[0] == 403
    assert call("GET", "/studio/session", headers={"X-Studio-Client": "1", "Host": "evil.example"})[0] == 403
    status, headers, body = call("GET", "/studio/session", headers={"X-Studio-Client": "1", "Origin": "http://127.0.0.1:1420"})
    assert status == 200
    assert json.loads(body)["token"] == transport.token
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:1420"
    assert call("POST", "/studio/rpc", "{}")[0] == 401
    assert call("POST", "/studio/rpc", "{}", {**auth(transport), "Origin": "https://evil.example"})[0] == 403
    assert service.dispatch("studio.products.list", {}) == {"products": []}


def test_http_and_service_share_persistence_and_validation(local_http):
    service, transport, call = local_http
    product = create_product(service, "Áo mùa đông")
    status, body = rpc(call, transport, "studio.products.list")
    assert status == 200 and [item["id"] for item in body["result"]["products"]] == [product["id"]]
    assert body["result"]["products"][0]["thumbnail_url"] is None
    status, body = rpc(call, transport, "studio.products.update", {"id": product["id"], "description": "Ấm áp"})
    assert status == 200 and body["result"]["product"]["description"] == "Ấm áp"
    assert rpc(call, transport, "studio.products.update", {"id": product["id"], "name": "Đổi tên"})[0] == 422
    assert rpc(call, transport, "studio.products.create", {"name": "Tạo tay"})[1]["error"]["code"] == "METHOD_NOT_FOUND"
    assert rpc(call, transport, "studio.media.import", {"source_path": "C:/private.mp4"})[1]["error"]["code"] == "NATIVE_ONLY"
    assert rpc(call, transport, "activation.activate", {})[1]["error"]["code"] == "METHOD_NOT_FOUND"


def test_streamed_upload_media_id_range_and_missing_file(local_http):
    service, transport, call = local_http
    contents = bytes(range(256)) * 400
    name = "áo mùa đông.mp4"
    status, _, body = call("POST", "/studio/upload?" + urlencode({"filename": name}), contents, auth(transport))
    assert status == 200
    media = json.loads(body)["result"]["media"]
    assert media["original_name"] == name
    assert media["size_bytes"] == len(contents)
    assert list((service.data_dir / "imports").iterdir()) == []
    path = "/studio/media/" + media["id"] + "?token=" + transport.token
    assert call("GET", "/studio/media/" + media["id"])[0] == 401
    status, headers, data = call("GET", path, headers={"Range": "bytes=100-199"})
    assert status == 206 and data == contents[100:200]
    assert headers["Content-Range"] == f"bytes 100-199/{len(contents)}"
    assert call("GET", path, headers={"Range": "bytes=-7"})[2] == contents[-7:]
    assert call("GET", path, headers={"Range": "bytes=0-1,3-4"})[0] == 416
    assert call("GET", path, headers={"Range": "bytes=999999-"})[0] == 416
    assert call("HEAD", path)[2] == b""
    assert call("GET", "/studio/media/../../private?token=" + transport.token)[0] == 404
    service.resolve_media(media["id"]).unlink()
    assert call("GET", path)[0] == 404


def test_upload_links_presenters_and_rpc_updates_links(local_http):
    service, transport, call = local_http
    lan = rpc(call, transport, "studio.presenters.create", {"name": "Chị Lan"})[1]["result"]["presenter"]
    minh = rpc(call, transport, "studio.presenters.create", {"name": "Anh Minh"})[1]["result"]["presenter"]
    query = urlencode([("filename", "clip.mp4"), ("presenter_id", lan["id"]), ("presenter_id", minh["id"])])
    status, _, body = call("POST", "/studio/upload?" + query, b"video-bytes", auth(transport))
    assert status == 200
    media = json.loads(body)["result"]["media"]
    assert media["presenter_ids"] == sorted([lan["id"], minh["id"]])
    status, body = rpc(call, transport, "studio.media.update", {"id": media["id"], "presenter_ids": [lan["id"]]})
    assert status == 200 and body["result"]["media"]["presenter_ids"] == [lan["id"]]
    assert service.dispatch("studio.media.list", {"presenter_id": minh["id"]}) == {"media": []}


def test_failed_upload_leaves_no_staging_files_or_database_rows(local_http):
    service, transport, call = local_http
    response = call("POST", "/studio/upload?filename=..%5Cprivate.mp4", b"video", auth(transport))
    assert response[0] == 422
    assert call("POST", "/studio/upload?filename=invalid.txt", b"video", auth(transport))[0] == 422
    assert service.dispatch("studio.media.list", {}) == {"media": []}
    assert list((service.data_dir / "imports").iterdir()) == []


def test_native_http_exposes_only_token_protected_media(local_http):
    service, transport, call = local_http
    transport.native = True
    assert call("GET", "/studio/session", headers={"X-Studio-Client": "1"})[0] == 404
    assert call("POST", "/studio/rpc", "{}", auth(transport))[0] == 404
    assert call("GET", "/studio/events?token=" + transport.token)[0] == 404
    # WebView2's native origin may read an imported video using its scoped URL.
    source = service.data_dir / "test.mp4"
    source.write_bytes(b"native-media")
    media = service.dispatch("studio.media.import", {"source_path": str(source)})["media"]
    path = "/studio/media/" + media["id"] + "?token=" + transport.token
    assert call("GET", path, headers={"Origin": "http://tauri.localhost"})[0] == 200
    assert call("GET", path, headers={"Origin": "https://evil.example"})[0] == 403


def test_runtime_lock_released_on_close(tmp_path):
    lock = RuntimeLock(tmp_path)
    try:
        with pytest.raises(StudioError) as error:
            RuntimeLock(tmp_path)
        assert error.value.code == "CORE_ALREADY_RUNNING"
    finally:
        lock.close()
    RuntimeLock(tmp_path).close()


def test_studio_ipc_persists_and_exits_on_shutdown(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    data_dir = tmp_path / "dữ liệu"
    commands = [
        {"v": 1, "type": "request", "request_id": "create", "method": "studio.presenters.create", "params": {"name": "Người dẫn local"}},
        {"v": 1, "type": "request", "request_id": "stop", "method": "system.shutdown", "params": {}},
    ]
    process = subprocess.run([sys.executable, "-m", "sarae_agent", "--studio", "--ipc", "--data-dir", str(data_dir)],
                             input="".join(json.dumps(command) + "\n" for command in commands),
                             capture_output=True, text=True, env=environment, timeout=15)
    assert process.returncode == 0, process.stderr
    frames = [json.loads(line) for line in process.stdout.splitlines()]
    assert frames[0]["event"] == "core.ready"
    assert "activation.activate" not in frames[0]["data"]["methods"]
    assert any(frame.get("request_id") == "create" and frame["ok"] for frame in frames)
    assert any(frame.get("event") == "core.stopped" for frame in frames)
    service = StudioService(data_dir)
    try:
        assert service.dispatch("studio.presenters.list", {})["presenters"][0]["name"] == "Người dẫn local"
    finally:
        service.close()


def test_controller_rejects_second_runtime_and_exits_when_parent_disappears(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    command = [sys.executable, "-m", "sarae_agent", "--studio", "--http", "--port", "0", "--data-dir", str(tmp_path)]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        ready = json.loads(first.stdout.readline())
        assert ready["event"] == "studio.ready"
        second = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=10)
        assert second.returncode == 3
        assert first.poll() is None
    finally:
        first.terminate()
        first.communicate(timeout=5)
    # The OS releases the controller lock on hard exit. A nonexistent parent
    # makes the new runtime shut itself down without leaving a sidecar orphan.
    watched = subprocess.run(command + ["--parent-pid", "2147483647"], capture_output=True,
                             text=True, env=environment, timeout=10)
    assert watched.returncode == 0, watched.stderr
    assert json.loads(watched.stdout.splitlines()[0])["event"] == "studio.ready"


def test_shutdown_delivers_active_import_result_before_stopped(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    source = tmp_path / "slow-copy.mp4"
    source.write_bytes(b"injected-video-probe")
    # A delayed probe reliably keeps the import active when shutdown is read.
    program = (
        "import time; from sarae_agent.studio import service; "
        "service.probe_video = lambda path: time.sleep(0.25); "
        "from sarae_agent.studio.__main__ import main; raise SystemExit(main())"
    )
    commands = [
        {"v": 1, "type": "request", "request_id": "import", "method": "studio.media.import", "params": {"source_path": str(source)}},
        {"v": 1, "type": "request", "request_id": "stop", "method": "system.shutdown", "params": {}},
    ]
    process = subprocess.run([sys.executable, "-c", program, "--ipc", "--data-dir", str(tmp_path / "studio")],
                             input="".join(json.dumps(command) + "\n" for command in commands),
                             capture_output=True, text=True, env=environment, timeout=15)
    assert process.returncode == 0, process.stderr
    frames = [json.loads(line) for line in process.stdout.splitlines()]
    imported = next(index for index, frame in enumerate(frames) if frame.get("request_id") == "import" and frame["ok"])
    stopped = next(index for index, frame in enumerate(frames) if frame.get("event") == "core.stopped")
    assert imported < stopped
