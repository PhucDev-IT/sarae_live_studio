"""Cloud protocol of backend/app/cloud_agents.py: long-poll, claim tokens, journaled acks, lanes, events, sync."""
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock

import httpx
import pytest

from fake_obs import FakeWs
from sarae_agent.cloud.client import CloudClient, CloudError
from sarae_agent.cloud.commands import CommandRouter
from sarae_agent.cloud.journal import CommandJournal, CommandLock, EventOutbox
from sarae_agent.cloud.worker import CloudWorker
from sarae_agent.errors import AgentError
from sarae_agent.playback.engine import PlaybackError

MID = "c" * 64


class FakeCloud:
    """Agent endpoints of the Cloud; ``__call__`` makes it its own client factory."""

    def __init__(self):
        self.lock = threading.Lock()
        self.queue: list[dict] = []
        self.acks: list[dict] = []
        self.ack_attempts = 0
        self.reports: list[dict] = []
        self.events: list[dict] = []
        self.synced: list[list[dict]] = []
        self.thumbs_missing: list[str] = []
        self.fail_heartbeat: CloudError | None = None
        self.fail_ack_times = 0
        self.reject_acks = False

    def __call__(self, api_url, timeout):
        return self

    def heartbeat(self, key, report):
        if self.fail_heartbeat:
            raise self.fail_heartbeat
        with self.lock:
            self.reports.append(report)
        return {"ok": True, "server_time": "now"}

    def next_command(self, key, agent_id, wait=0):
        with self.lock:
            if self.queue:
                return self.queue.pop(0)
        time.sleep(0.02)  # a real Cloud holds the request up to `wait` seconds
        return None

    def ack(self, key, agent_id, command, success, result):
        with self.lock:
            self.ack_attempts += 1
            if self.reject_acks:
                raise CloudError("ACK_REJECTED", "Claim token không hợp lệ cho lệnh này")
            if self.fail_ack_times:
                self.fail_ack_times -= 1
                raise CloudError("CLOUD_UNREACHABLE", "offline", retryable=True)
            self.acks.append({"command_id": command["command_id"], "claim_token": command.get("claim_token"),
                              "success": success, "result": result})
        return {"ok": True}

    def post_events(self, key, agent_id, events):
        with self.lock:
            self.events.extend(events)
        return {"accepted": len(events)}

    def sync_library(self, key, agent_id, videos):
        with self.lock:
            self.synced.append(videos)
        return {"synced": len(videos), "thumbs_missing": list(self.thumbs_missing)}

    def captcha_templates(self, key, agent_id):
        return [{"id": "a" * 32, "sha256": "f" * 64}]

    def captcha_template_image(self, key, agent_id, template_id):
        return b"png-bytes"

    def close(self):
        pass

    def acks_for(self, command_id):
        with self.lock:
            return [a for a in self.acks if a["command_id"] == command_id]


class FakeLibrary:
    def __init__(self):
        self.dirty = True
        self.thumbs = {"a" * 64: "/9j/AAAA"}

    def probe_pending(self, budget_seconds=20.0):
        return 0

    def cloud_inventory(self):
        return [{"media_id": "a" * 64, "filename": "a.mp4", "size_bytes": 1, "modified_ns": 1, "duration_seconds": 2.5},
                {"media_id": "b" * 64, "filename": "b.mp4", "size_bytes": 1, "modified_ns": 1}]

    def thumb_base64(self, media_id):
        return self.thumbs.get(media_id)

    def scan_blocking(self):
        return {"state": "succeeded", "total": 2}


class CountingRouter(CommandRouter):
    def __init__(self):
        self.executed: list[str] = []
        self.gate = threading.Event()
        self.gate.set()

    def execute(self, kind, payload):
        self.executed.append(kind)
        if kind == "tts_speak":
            self.gate.wait(5)
        if kind == "obs_pause":
            raise PlaybackError(409, "Không có video đang phát để Pause")
        return {"ok": True, "kind": kind}


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def journal(tmp_path):
    return CommandJournal(tmp_path / "journal.json")


def make_worker(cloud, router, events, states, journal, *, library=None, outbox=None, lock=None, report=None):
    worker = CloudWorker(agent_id="agt_1", credential="cred", api_url="https://c/api",
                         timeouts={"request": 5, "heartbeat": 0.3, "poll": 1}, router=router,
                         build_report=report or (lambda: {"agent_id": "agt_1", "playback": {"running": False}}),
                         library=library or FakeLibrary(), outbox=outbox or EventOutbox(), journal=journal,
                         emit=events, on_state=lambda s, e: states.append((s, e)), lock=lock, client_factory=cloud)
    worker.ACK_RETRY_DELAYS = (0.05, 0.05, 0.05)
    return worker


def test_executes_once_and_reacks_redelivery_with_new_token(events, journal):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.queue = [{"command_id": "c1", "kind": "obs_status", "payload": {}, "claim_token": "t1", "attempt": 1},
                   {"command_id": "c2", "kind": "obs_pause", "payload": {}, "claim_token": "t9", "attempt": 1}]
    worker = make_worker(cloud, router, events, states, journal)
    worker.start()
    try:
        assert wait_until(lambda: cloud.acks_for("c1") and cloud.acks_for("c2"))
        failed = cloud.acks_for("c2")[0]
        # The Cloud shows a failure's text to the OWNER as is: plain message, not JSON.
        assert failed == {"command_id": "c2", "claim_token": "t9", "success": False,
                          "result": "Không có video đang phát để Pause"}
        cloud.queue.append({"command_id": "c1", "kind": "obs_status", "payload": {}, "claim_token": "t2",
                            "attempt": 2})  # lease ran out / ack lost: delivered again
        assert wait_until(lambda: len(cloud.acks_for("c1")) == 2)
        first, second = cloud.acks_for("c1")
        assert [first["claim_token"], second["claim_token"]] == ["t1", "t2"]
        assert first["result"] == second["result"] and json.loads(first["result"]) == {"ok": True, "kind": "obs_status"}
        assert router.executed == ["obs_status", "obs_pause"]  # c1 not executed twice
        assert states[0][0] == "running"
        finished = [d for n, d in events.captured if n == "command.finished"]
        assert [d["status"] for d in finished if d["command_id"] == "c2"] == ["failed"]
    finally:
        assert worker.stop(timeout=5)


def test_journal_survives_restart(events, journal, tmp_path):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.queue = [{"command_id": "c3", "kind": "playback_next", "payload": {}, "claim_token": "t1"}]
    worker = make_worker(cloud, router, events, states, journal)
    worker.start()
    assert wait_until(lambda: cloud.acks_for("c3"))
    worker.stop(timeout=5)
    reloaded = CommandJournal(journal.path)
    assert reloaded.get("c3")["success"] is True
    cloud.queue = [{"command_id": "c3", "kind": "playback_next", "payload": {}, "claim_token": "t2"}]
    again = make_worker(cloud, router, events, states, reloaded)
    again.start()
    try:
        assert wait_until(lambda: len(cloud.acks_for("c3")) == 2)
        assert router.executed == ["playback_next"]
    finally:
        again.stop(timeout=5)


def test_ack_retried_on_network_error_then_rejection_stops(events, journal):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.fail_ack_times = 2
    cloud.queue = [{"command_id": "c9", "kind": "obs_status", "payload": {}, "claim_token": "t1"}]
    worker = make_worker(cloud, router, events, states, journal)
    worker.start()
    try:
        assert wait_until(lambda: cloud.acks_for("c9"))
        assert cloud.ack_attempts == 3 and router.executed == ["obs_status"]
        cloud.reject_acks = True
        attempts = cloud.ack_attempts
        cloud.queue = [{"command_id": "c10", "kind": "obs_status", "payload": {}, "claim_token": "old"}]
        assert wait_until(lambda: cloud.ack_attempts == attempts + 1)
        time.sleep(0.3)
        assert cloud.ack_attempts == attempts + 1  # 404/409: the Cloud will not take it, no retry
    finally:
        worker.stop(timeout=5)


def test_background_lane_never_blocks_live_lane(events, journal):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    router.gate.clear()
    cloud.queue = [{"command_id": "tts", "kind": "tts_speak", "payload": {"text": "xin chào"}, "claim_token": "a"},
                   {"command_id": "next", "kind": "playback_next", "payload": {}, "claim_token": "b"}]
    worker = make_worker(cloud, router, events, states, journal)
    worker.start()
    try:
        assert wait_until(lambda: cloud.acks_for("next"))
        assert not cloud.acks_for("tts")  # still speaking
        router.gate.set()
        assert wait_until(lambda: cloud.acks_for("tts"))
    finally:
        worker.stop(timeout=5)


def test_playback_events_and_heartbeat(events, journal):
    cloud, router, states, outbox = FakeCloud(), CountingRouter(), [], EventOutbox()
    play = {"running": True, "queue_ids": [MID], "index": 0}
    worker = make_worker(cloud, router, events, states, journal, outbox=outbox,
                         report=lambda: {"agent_id": "agt_1", "playback": play})
    worker.start()
    try:
        assert wait_until(lambda: cloud.reports)
        outbox.push({"type": "PRODUCT_SWITCHED", "session_id": 7, "product_id": 2, "detail": {"trigger": "video_end"}})
        worker.poke()
        assert wait_until(lambda: cloud.events)
        assert cloud.events[0]["type"] == "PRODUCT_SWITCHED" and cloud.events[0]["seq"] > 0
        assert outbox.pending() == 0
        count = len(cloud.reports)
        assert wait_until(lambda: len(cloud.reports) > count, timeout=3)  # 2 s cadence while playing
        assert cloud.reports[-1]["playback"]["queue_ids"] == [MID]
    finally:
        worker.stop(timeout=5)


def test_media_sync_sends_duration_and_only_missing_thumbnails(events, journal):
    cloud, router, states, library = FakeCloud(), CountingRouter(), [], FakeLibrary()
    worker = make_worker(cloud, router, events, states, journal, library=library)
    worker.MEDIA_SYNC_SECONDS = 0.2
    worker.start()
    try:
        assert wait_until(lambda: len(cloud.synced) >= 2)
        first, second = cloud.synced[0], cloud.synced[1]
        assert first[0]["thumb_jpeg_base64"] == "/9j/AAAA" and first[0]["duration_seconds"] == 2.5
        assert "thumb_jpeg_base64" not in first[1]  # no local thumbnail yet
        assert all("thumb_jpeg_base64" not in v for v in second)  # Cloud has it (thumbs_missing empty)
        assert all("path" not in v for v in first)
        assert worker.rescan() == {"count": 2, "thumbs_sent": 0, "thumbs_pending": 0}
    finally:
        worker.stop(timeout=5)


def test_captcha_templates_are_mirrored_from_cloud(events, journal):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    monitor = mock.Mock()
    monitor.sync_templates.return_value = {"added": 1, "removed": 0, "failed": 0, "total": 1}
    worker = make_worker(cloud, router, events, states, journal)
    worker._monitor = monitor
    worker.start()
    try:
        assert wait_until(lambda: monitor.sync_templates.called)
        remote, fetch = monitor.sync_templates.call_args.args
        assert remote == [{"id": "a" * 32, "sha256": "f" * 64}] and fetch("a" * 32) == b"png-bytes"
    finally:
        worker.stop(timeout=5)


def test_transient_then_fatal(events, journal):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.fail_heartbeat = CloudError("CLOUD_UNREACHABLE", "offline", retryable=True)
    worker = make_worker(cloud, router, events, states, journal)
    with mock.patch("sarae_agent.cloud.worker.Backoff.next", return_value=0.1):
        worker.start()
        assert wait_until(lambda: any(s == "disconnected" for s, _ in states))
        time.sleep(0.3)
        assert worker.is_alive()  # keeps retrying with backoff
        assert worker.status()["state"] == "disconnected"
        cloud.fail_heartbeat = CloudError("LICENSE_EXPIRED", "Agent không có license hợp lệ")
        assert wait_until(lambda: not worker.is_alive(), timeout=10)
    assert states[-1] == ("error", {"code": "LICENSE_EXPIRED", "message": "Agent không có license hợp lệ"})
    assert worker.status()["state"] == "rejected"
    worker.stop(timeout=5)


def test_command_lock_allows_one_process(tmp_path, events, journal):
    holder = CommandLock(tmp_path / "agent_commands.lock")
    assert holder.acquire()
    worker = make_worker(FakeCloud(), CountingRouter(), events, [], journal,
                         lock=CommandLock(tmp_path / "agent_commands.lock"))
    with pytest.raises(AgentError) as exc:
        worker.start()
    assert exc.value.code == "AGENT_ALREADY_RUNNING"
    holder.release()
    worker.start()
    try:
        assert worker.is_alive()
    finally:
        worker.stop(timeout=5)
    assert holder.acquire()  # released by stop()
    holder.release()


def test_outbox_seq_increases_and_keeps_unsent():
    outbox = EventOutbox()
    outbox.push({"type": "PRODUCT_SWITCHED", "session_id": 1, "product_id": 2, "detail": {}})
    outbox.push({"type": "PLAYBACK_ENDED", "session_id": 1, "product_id": 2, "detail": {}})
    assert outbox.ready.is_set()
    first, second = (e["seq"] for e in outbox.items)
    assert second == first + 1 and first > 1_600_000_000_000  # epoch ms: grows across restarts

    def down(batch):
        raise CloudError("CLOUD_UNREACHABLE", "down", retryable=True)

    with pytest.raises(CloudError):
        outbox.flush(down)
    assert outbox.pending() == 2
    sent = []
    assert outbox.flush(sent.extend) == 2
    assert [e["seq"] for e in sent] == [first, second]
    assert outbox.pending() == 0 and not outbox.ready.is_set()


# --- command router: dev's command kinds ------------------------------------------------------
@pytest.fixture()
def router():
    engine = mock.Mock()
    engine.status.return_value = {"running": True, "obs": {"has_password": True}}
    monitor, voice = mock.Mock(), mock.Mock()
    return CommandRouter(playback=engine, monitor=monitor, voice=voice), engine, monitor, voice


def test_router_reaches_engine(router):
    r, engine, monitor, voice = router
    r.execute("product_play", {"session_id": 7, "product_id": 3, "media_ids": [MID], "mode": "after_current"})
    engine.product_play.assert_called_once_with(7, 3, [MID], "after_current")
    r.execute("playback_select", {"index": 2})
    engine.select.assert_called_once_with(2)
    r.execute("playback_seek", {"ms": 900})
    engine.seek.assert_called_once_with(900)
    r.execute("playback_cancel_pending", {})
    engine.cancel_pending.assert_called_once()
    r.execute("local_playlist_start", {"media_ids": [MID], "loop": False})
    engine.start_playlist.assert_called_once_with([MID], loop=False)
    for kind, method in (("obs_pause", "pause"), ("playback_resume", "resume"), ("obs_next", "next"),
                         ("playback_previous", "previous"), ("local_media_stop", "stop")):
        r.execute(kind, {})
        getattr(engine, method).assert_called()
    r.execute("tts_speak", {"text": "Xin chào", "voice": "vi-VN-NamMinhNeural"})
    voice.speak.assert_called_once_with("Xin chào", "vi-VN-NamMinhNeural")
    r.execute("monitor_config", {"interval_seconds": 3, "threshold": 0.9})
    assert monitor.configure.call_args.args[0]["threshold"] == 0.9


def test_router_obs_configure_never_returns_password(router):
    r, engine, _, _ = router
    result = r.execute("obs_configure", {"host": "127.0.0.1", "port": 4455, "password": "s3cret",
                                         "input_name": "Sarae Video", "dual_mode": False, "source_a": "", "source_b": ""})
    assert engine.configure.call_args.kwargs["password"] == "s3cret"
    assert "s3cret" not in json.dumps(result)


@pytest.mark.parametrize("kind,payload,code", [
    ("shell", {}, "UNSUPPORTED_COMMAND"),
    ("product_play", {"session_id": 0, "product_id": 1, "media_ids": [MID]}, "INVALID_COMMAND_PAYLOAD"),
    ("local_playlist_start", {"media_ids": ["../../x"]}, "INVALID_COMMAND_PAYLOAD"),
    ("playback_next", {"unexpected": 1}, "INVALID_COMMAND_PAYLOAD"),
    ("media_rescan", {}, "AGENT_NOT_CONNECTED"),
])
def test_router_rejects(router, kind, payload, code):
    r, _, _, _ = router
    with pytest.raises(AgentError) as exc:
        r.execute(kind, payload)
    assert exc.value.code == code


# --- HTTP client against dev's endpoints ------------------------------------------------------
def http_client(handler):
    return CloudClient("https://cloud.example/api", transport=httpx.MockTransport(handler))


def test_client_captcha_template_endpoints():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/image"):
            return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/png"})
        return httpx.Response(200, json={"templates": [{"id": "a" * 32, "sha256": "f" * 64, "bytes": 4}]})

    client = http_client(handler)
    assert client.captcha_templates("key", "agt_1")[0]["id"] == "a" * 32
    assert client.captcha_template_image("key", "agt_1", "a" * 32) == b"image-bytes"
    assert seen[0].url.path == "/api/captcha-templates/agent" and seen[0].url.params["agent_id"] == "agt_1"
    assert seen[1].url.path == f"/api/captcha-templates/agent/{'a' * 32}/image"
    assert all(r.headers["X-Sarae-Agent-Key"] == "key" for r in seen)


def test_client_long_poll_ack_events_shapes():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/next"):
            return httpx.Response(200, json={"command": {"command_id": "c1", "kind": "playback_next", "payload": {},
                                                         "claim_token": "tok", "attempt": 1}})
        return httpx.Response(200, json={"ok": True})

    client = http_client(handler)
    command = client.next_command("key", "agt_1", wait=25)
    assert seen[0].method == "GET" and seen[0].url.path == "/api/agents/commands/agt_1/next"
    assert seen[0].url.params["wait"] == "25" and seen[0].headers["X-Sarae-Agent-Key"] == "key"
    client.ack("key", "agt_1", command, True, '{"ok": true}')
    assert seen[1].url.path == "/api/agents/commands/ack"
    assert json.loads(seen[1].content) == {"agent_id": "agt_1", "command_id": "c1", "claim_token": "tok",
                                           "success": True, "result": '{"ok": true}'}
    client.post_events("key", "agt_1", [{"seq": 1, "type": "ERROR", "session_id": None, "product_id": None,
                                         "detail": {}}])
    assert seen[2].url.path == "/api/agents/events" and json.loads(seen[2].content)["events"][0]["seq"] == 1


@pytest.mark.parametrize("context,status,code,fatal", [
    ("ack", 409, "ACK_REJECTED", False),
    ("ack", 404, "ACK_REJECTED", False),
    ("heartbeat", 403, "LICENSE_EXPIRED", True),
    ("heartbeat", 401, "AGENT_UNAUTHORIZED", True),
    ("heartbeat", 503, "CLOUD_SERVER_ERROR", False),
])
def test_client_error_codes(context, status, code, fatal):
    client = http_client(lambda request: httpx.Response(status, json={"detail": "x"}))
    call = {"ack": lambda: client.ack("k", "a", {"command_id": "c"}, True, ""),
            "heartbeat": lambda: client.heartbeat("k", {})}[context]
    with pytest.raises(CloudError) as exc:
        call()
    assert exc.value.code == code and exc.value.fatal is fatal


def test_heartbeat_report_matches_cloud_and_has_no_paths(home, events):
    from sarae_agent.runtime import AgentRuntime
    rt = AgentRuntime(events)
    rt.obs._factory = lambda cfg, password: FakeWs()
    rt.activation.require = lambda: SimpleNamespace(agent_id="agt_1")
    view = rt.monitor.heartbeat_view()
    view["state"] = {**view["state"], "window_info": {"exe_name": "TikTok LIVE Studio.exe",
                                                      "exe_path": "C:\\Users\\me\\AppData\\TikTok LIVE Studio.exe"}}
    rt.monitor.heartbeat_view = lambda: view
    report = rt.build_report()
    assert set(report) == {"agent_id", "hostname", "verification", "obs", "local_playback", "playback"}
    assert report["playback"]["connected"] is True and report["playback"]["queue_ids"] == []
    assert report["verification"]["state"]["window_info"] == {"exe_name": "TikTok LIVE Studio.exe"}
    assert "C:\\Users" not in json.dumps(report)
