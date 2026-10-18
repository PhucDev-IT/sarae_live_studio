import threading
import time

import pytest

from sarae_agent.cloud.client import CloudError
from sarae_agent.cloud.commands import CommandRouter
from sarae_agent.cloud.worker import CloudWorker
from sarae_agent.config import ConfigStore
from sarae_agent.errors import AgentError
from sarae_agent.obs.controller import ObsController


class RequestError(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


class FakeWs:
    def __init__(self, inputs=("Sarae Video",), apply=True):
        self.inputs = set(inputs)
        self.settings: dict = {}
        self.apply = apply
        self.broken = False
        self.calls: list[str] = []

    def send(self, request, data=None, raw=False):
        self.calls.append(request)
        if self.broken:
            raise ConnectionResetError("socket closed")
        name = (data or {}).get("inputName")
        if name is not None and name not in self.inputs:
            raise RequestError(600)
        if request == "GetVersion":
            return {"obsVersion": "31.0.0", "obsWebSocketVersion": "5.5.0"}
        if request == "SetInputSettings" and self.apply:
            self.settings[name] = dict(data["inputSettings"])
        if request == "GetInputSettings":
            return {"inputSettings": self.settings.get(name, {})}
        if request == "GetMediaInputStatus":
            return {"mediaState": "OBS_MEDIA_STATE_PLAYING", "mediaDuration": 1000, "mediaCursor": 10}
        return {}

    def disconnect(self):
        pass


def controller(home, events, factory):
    obs = ObsController(ConfigStore(), events, client_factory=factory)
    obs.RECONNECT_MIN_SECONDS = 0
    return obs


def test_obs_connect_play_and_verify(home, events):
    ws = FakeWs()
    obs = controller(home, events, lambda cfg, pw: ws)
    assert obs.connect()["obs_version"] == "31.0.0"
    status = obs.play_file("Sarae Video", "C:\\v\\a.mp4")
    assert status["state"] == "OBS_MEDIA_STATE_PLAYING"
    assert ws.settings["Sarae Video"]["local_file"] == "C:\\v\\a.mp4"
    assert "TriggerMediaInputAction" in ws.calls
    assert any(name == "obs.status_changed" and d["connected"] for name, d in events.captured)


def test_obs_errors_are_typed(home, events):
    obs = controller(home, events, lambda cfg, pw: FakeWs(apply=False))
    obs.connect()
    with pytest.raises(AgentError) as exc:
        obs.play_file("Missing Input", "C:\\a.mp4")
    assert exc.value.code == "OBS_INPUT_MISSING"
    with pytest.raises(AgentError) as exc:
        obs.play_file("Sarae Video", "C:\\a.mp4")
    assert exc.value.code == "OBS_NOT_APPLIED"  # OBS answered but did not take the file


@pytest.mark.parametrize("error,code", [
    (ConnectionRefusedError("[WinError 10061] refused"), "OBS_NOT_RUNNING"),
    (Exception("authentication enabled but no password provided"), "OBS_AUTH_FAILED"),
    (TimeoutError("timed out"), "OBS_TIMEOUT"),
])
def test_obs_connect_failures(home, events, error, code):
    def factory(cfg, pw):
        raise error

    obs = controller(home, events, factory)
    with pytest.raises(AgentError) as exc:
        obs.connect()
    assert exc.value.code == code
    assert obs.status()["connected"] is False


def test_obs_drop_and_reconnect(home, events):
    sockets = [FakeWs(), FakeWs()]
    obs = controller(home, events, lambda cfg, pw: sockets.pop(0))
    obs.connect()
    obs._client.broken = True
    with pytest.raises(AgentError) as exc:
        obs.media_status("Sarae Video")
    assert exc.value.code == "OBS_DISCONNECTED" and not obs.status()["connected"]
    assert obs.media_status("Sarae Video")["state"] == "OBS_MEDIA_STATE_PLAYING"  # reconnected
    assert obs.status()["connected"]


# --- Cloud worker -----------------------------------------------------------------------------
class FakeCloud:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue: list[dict] = []
        self.acks: list[tuple[str, str]] = []
        self.heartbeats = 0
        self.synced: list[int] = []
        self.fail_heartbeat: CloudError | None = None
        self.fail_ack_times = 0
        self.redeliver: dict | None = None

    def __call__(self, api_url, timeout):
        return self

    def heartbeat(self, key, report):
        if self.fail_heartbeat:
            raise self.fail_heartbeat
        with self.lock:
            self.heartbeats += 1
        return {"ok": True, "server_time": "now"}

    def next_command(self, key, agent_id):
        with self.lock:
            if self.redeliver:
                command, self.redeliver = self.redeliver, None
                return command
            return self.queue.pop(0) if self.queue else None

    def ack(self, key, agent_id, command_id, status, result):
        with self.lock:
            if status in ("succeeded", "failed") and self.fail_ack_times:
                self.fail_ack_times -= 1
                raise CloudError("CLOUD_UNREACHABLE", "offline", retryable=True)
            self.acks.append((command_id, status))
        return {"ok": True}

    def sync_library(self, key, agent_id, videos):
        self.synced.append(len(videos))
        return {"synced": len(videos)}

    def close(self):
        pass


class FakeLibrary:
    dirty = True

    def cloud_inventory(self):
        return [{"media_id": "a" * 64, "filename": "a.mp4", "size_bytes": 1, "modified_ns": 1}]


class CountingRouter(CommandRouter):
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, kind, payload):
        self.executed.append(kind)
        if kind == "obs_pause":
            raise AgentError("PLAYBACK_NOT_ACTIVE", "Chưa phát playlist nào")
        return {"ok": True}


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def make_worker(cloud, router, events, states):
    return CloudWorker(agent_id="agt_1", credential="cred", api_url="https://c/api",
                       timeouts={"request": 5, "heartbeat": 0.5, "poll": 0.1}, router=router,
                       build_report=lambda: {"agent_id": "agt_1"}, library=FakeLibrary(), emit=events,
                       on_state=lambda s, e: states.append((s, e)), client_factory=cloud)


def test_worker_executes_once_and_acks_in_order(events):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.queue = [{"command_id": "c1", "kind": "obs_status", "payload": {}},
                   {"command_id": "c2", "kind": "obs_pause", "payload": {}}]
    cloud.redeliver = None
    worker = make_worker(cloud, router, events, states)
    worker.start()
    try:
        assert wait_until(lambda: ("c2", "failed") in cloud.acks)
        cloud.redeliver = {"command_id": "c1", "kind": "obs_status", "payload": {}}  # lost ACK -> resent
        assert wait_until(lambda: cloud.acks.count(("c1", "accepted")) == 2)
        assert router.executed == ["obs_status", "obs_pause"]  # c1 not executed twice
        assert ("c1", "succeeded") in cloud.acks
        assert cloud.acks.index(("c1", "accepted")) < cloud.acks.index(("c1", "succeeded"))
        assert cloud.synced == [1]
        assert states[0][0] == "running"
        finished = [d for n, d in events.captured if n == "command.finished"]
        assert {d["command_id"]: d["status"] for d in finished} == {"c1": "succeeded", "c2": "failed"}
    finally:
        assert worker.stop(timeout=5)


def test_worker_retries_final_ack(events):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.fail_ack_times = 2
    cloud.queue = [{"command_id": "c9", "kind": "obs_status", "payload": {}}]
    worker = make_worker(cloud, router, events, states)
    worker.start()
    try:
        assert wait_until(lambda: ("c9", "succeeded") in cloud.acks, timeout=15)
        assert router.executed == ["obs_status"]
    finally:
        worker.stop(timeout=5)


def test_worker_transient_then_fatal(events):
    cloud, router, states = FakeCloud(), CountingRouter(), []
    cloud.fail_heartbeat = CloudError("CLOUD_UNREACHABLE", "offline", retryable=True)
    worker = make_worker(cloud, router, events, states)
    worker.start()
    assert wait_until(lambda: any(s == "disconnected" for s, _ in states))
    assert worker.is_alive()  # keeps retrying with backoff
    assert worker.status()["state"] == "disconnected"
    cloud.fail_heartbeat = CloudError("AGENT_REVOKED", "Agent đã bị thu hồi")
    worker._wake.set()
    assert wait_until(lambda: not worker.is_alive(), timeout=10)
    assert states[-1] == ("error", {"code": "AGENT_REVOKED", "message": "Agent đã bị thu hồi"})
