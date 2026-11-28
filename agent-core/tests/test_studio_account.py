"""Device activation and license (docs/HYBRID_STUDIO_SERVER.md) against a fake gateway (httpx.MockTransport)."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from sarae_agent.studio.account import StudioAccount, validate_server_url
from sarae_agent.studio.service import StudioError, StudioService
from sarae_agent.studio.transport import StudioTransport

CREDENTIAL = "cred-super-secret-0123456789"
HOUR = 3600.0
START = 1_800_000_000.0  # 2027-01-15


def protect(data: bytes) -> bytes:  # reversible stand-in for DPAPI
    return b"P" + data[::-1]


def unprotect(data: bytes) -> bytes:
    assert data.startswith(b"P")
    return data[1:][::-1]


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now


class Gateway:
    """The gateway's contract: activation, then the license with device auth."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.license = {"valid": True, "email": "khach@example.vn", "expires_at": "2027-12-31T00:00:00+00:00",
                        "max_agents": 2, "agent_id": "agt_one", "hostname": "MAY-LIVE", "grace_hours": 72}
        self.license_error: tuple[int, str] | None = None
        self.activate_error: tuple[int, str] | None = None
        self.offline = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/api/identity/activate":
            if self.activate_error:
                status, code = self.activate_error
                return httpx.Response(status, headers={"X-Sarae-Error": code}, json={"detail": "lỗi từ server"})
            return httpx.Response(200, json={"agent_id": "agt_one", "agent_credential": CREDENTIAL,
                                             "owner_id": "own_1", "platform": "WINDOWS"})
        if request.url.path == "/api/studio/v1/license":
            if request.headers.get("X-Sarae-Agent") != "agt_one" or request.headers.get("Authorization") != "Bearer " + CREDENTIAL:
                return httpx.Response(401, headers={"X-Sarae-Error": "AGENT_UNAUTHORIZED"}, json={"detail": "x"})
            if self.license_error:
                status, code = self.license_error
                return httpx.Response(status, headers={"X-Sarae-Error": code} if code else {}, json={"detail": "x"})
            return httpx.Response(200, json={**self.license, "server_time": "2027-01-15T08:00:00+00:00"})
        return httpx.Response(404)


@pytest.fixture
def gateway():
    return Gateway()


@pytest.fixture
def make_account(tmp_path, gateway, monkeypatch):
    monkeypatch.delenv("SARAE_STUDIO_DEV_UNLICENSED", raising=False)
    clock = Clock()
    events = []

    def make(**environ):
        return StudioAccount(tmp_path / "studio", transport=httpx.MockTransport(gateway), protect=protect,
                             unprotect=unprotect, clock=clock, emit=lambda name, data: events.append((name, data)),
                             environ=environ, hostname="MAY-LIVE")

    make.clock, make.events = clock, events
    return make


def activated(make_account):
    account = make_account()
    account.activate({"activation_key": "SARAE-ACT-ABCDEF123"})
    return account


def test_activation_stores_protected_credential_and_checks_license(make_account, gateway, caplog):
    caplog.set_level(logging.DEBUG)
    account = make_account()
    assert account.status()["state"] == "unactivated" and not account.licensed()
    status = account.activate({"activation_key": "  SARAE-ACT-ABCDEF123  "})
    body = json.loads(gateway.requests[0].content)
    assert body == {"activation_key": "SARAE-ACT-ABCDEF123", "hostname": "MAY-LIVE", "platform": "WINDOWS"}
    assert str(gateway.requests[0].url) == "https://api.puns.id.vn/api/identity/activate"
    check = gateway.requests[1]
    assert check.headers["X-Sarae-Agent"] == "agt_one" and check.headers["Authorization"] == "Bearer " + CREDENTIAL
    assert status["licensed"] and status["state"] == "active"
    assert status["email"] == "khach@example.vn" and status["expires_at"].startswith("2027-12-31")
    assert status["device_name"] == "MAY-LIVE" and status["checked_at"]
    stored = account.path.read_text(encoding="utf-8")
    assert CREDENTIAL not in stored and "SARAE-ACT" not in stored
    assert CREDENTIAL not in json.dumps(status) and CREDENTIAL not in caplog.text
    assert make_account.events[-1][0] == "studio.account.changed"


def test_license_survives_restart_without_network(make_account, gateway):
    activated(make_account)
    gateway.offline = True
    make_account.clock.now += 10 * HOUR
    again = make_account()
    assert again.licensed() and again.status()["email"] == "khach@example.vn"


def test_offline_grace_window_then_lock_then_recovery(make_account, gateway):
    account = activated(make_account)
    gateway.offline = True
    make_account.clock.now += 71 * HOUR
    status = account.check()
    assert status["state"] == "offline" and status["licensed"] and status["grace_until"]
    assert status["last_error"]["code"] == "SERVER_UNREACHABLE"
    make_account.clock.now += 2 * HOUR  # 73 h after the last good check
    status = account.status()
    assert status["state"] == "locked" and status["reason"] == "OFFLINE_GRACE_EXPIRED" and not account.licensed()
    gateway.offline = False
    assert account.check()["state"] == "active"


def test_server_error_uses_grace_like_network_error(make_account, gateway):
    account = activated(make_account)
    gateway.license_error = (502, "")
    assert account.check()["state"] == "offline" and account.licensed()


def test_clock_moved_far_back_needs_a_fresh_check(make_account, gateway):
    account = activated(make_account)
    make_account.clock.now -= 2 * HOUR
    assert account.status()["reason"] == "OFFLINE_GRACE_EXPIRED"


def test_expired_license_locks_and_stays_locked_offline(make_account, gateway):
    account = activated(make_account)
    gateway.license_error = (403, "LICENSE_EXPIRED")
    status = account.check()
    assert status["state"] == "locked" and status["reason"] == "LICENSE_EXPIRED" and "hết hạn" in status["message"]
    assert status["activated"]  # the credential is kept: renewing the license unlocks this machine
    gateway.offline = True
    assert make_account().status()["reason"] == "LICENSE_EXPIRED"
    gateway.offline, gateway.license_error = False, None
    assert account.check()["state"] == "active"


def test_license_date_passing_offline_locks(make_account, gateway):
    gateway.license = {**gateway.license, "expires_at": "2027-01-15T10:00:00+00:00"}  # 2 h after server_time
    account = activated(make_account)
    gateway.offline = True
    make_account.clock.now += 1 * HOUR
    assert account.licensed()
    make_account.clock.now += 2 * HOUR
    assert account.status()["reason"] == "LICENSE_EXPIRED"


def test_invalid_payload_locks(make_account, gateway):
    gateway.license = {**gateway.license, "valid": False}
    account = activated(make_account)
    assert account.status()["reason"] == "LICENSE_EXPIRED"


@pytest.mark.parametrize("code,status", [("AGENT_REVOKED", 403), ("AGENT_UNAUTHORIZED", 403), ("AGENT_NOT_ACTIVATED", 401)])
def test_refused_device_forgets_credential(make_account, gateway, code, status):
    account = activated(make_account)
    gateway.license_error = (status, code)
    result = account.check()
    assert result["state"] == "unactivated" and result["reason"] == code and not result["activated"]
    assert not account.path.exists() and account.auth() is None
    if code == "AGENT_REVOKED":
        assert "gỡ" in result["message"]


def test_report_from_voice_call_applies_the_same_rules(make_account):
    account = activated(make_account)
    account.report("LICENSE_EXPIRED")
    assert account.status()["reason"] == "LICENSE_EXPIRED"
    account.report("AGENT_REVOKED")
    assert account.status()["state"] == "unactivated"


@pytest.mark.parametrize("status,code,fragment", [(403, "ACTIVATION_KEY_INVALID", "không đúng"),
                                                  (403, "ACTIVATION_KEY_USED", "đã được dùng"),
                                                  (409, "AGENT_LIMIT_REACHED", "đủ số máy")])
def test_activation_errors_are_reported_per_code(make_account, gateway, status, code, fragment):
    gateway.activate_error = (status, code)
    account = make_account()
    with pytest.raises(StudioError) as caught:
        account.activate({"activation_key": "SARAE-ACT-ABCDEF123"})
    assert caught.value.code == code and fragment in caught.value.message
    assert not account.path.exists()


def test_activation_network_error_and_bad_key(make_account, gateway):
    account = make_account()
    with pytest.raises(StudioError) as caught:
        account.activate({"activation_key": "có dấu cách"})
    assert caught.value.code == "ACTIVATION_KEY_INVALID" and gateway.requests == []
    gateway.offline = True
    with pytest.raises(StudioError) as caught:
        account.activate({"activation_key": "SARAE-ACT-ABCDEF123"})
    assert caught.value.code == "SERVER_UNREACHABLE"


def test_deactivate_forgets_locally(make_account, gateway):
    account = activated(make_account)
    status = account.deactivate()
    assert status["state"] == "unactivated" and status["reason"] is None and not account.path.exists()
    with pytest.raises(StudioError) as caught:
        account.check()
    assert caught.value.code == "AGENT_NOT_ACTIVATED"


def test_server_url_rules():
    assert validate_server_url("https://api.puns.id.vn/") == "https://api.puns.id.vn"
    assert validate_server_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in ("http://api.puns.id.vn", "ftp://x", "https://user:pw@x.vn", "https://x.vn/?a=1", "https://"):
        with pytest.raises(StudioError):
            validate_server_url(bad)


def test_credential_belongs_to_its_server(make_account, gateway):
    activated(make_account)
    other = make_account(SARAE_STUDIO_SERVER_URL="http://localhost:8000")
    assert other.status()["state"] == "unactivated" and other.auth() is None


def test_dev_bypass_is_licensed_and_warns(make_account, caplog):
    caplog.set_level(logging.WARNING)
    account = make_account(SARAE_STUDIO_DEV_UNLICENSED="1")
    assert account.licensed() and account.status()["dev_bypass"]
    assert "SARAE_STUDIO_DEV_UNLICENSED" in caplog.text


def test_background_tick_checks_when_due_and_publishes_changes(make_account, gateway):
    account = make_account()
    account.tick()  # not activated: nothing to ask the server
    assert gateway.requests == []
    account = activated(make_account)
    calls = len(gateway.requests)
    account.tick()
    assert len(gateway.requests) == calls  # checked at activation; next in 6 h
    make_account.clock.now += 6 * HOUR + 1
    account.tick()
    assert len(gateway.requests) == calls + 1
    make_account.events.clear()
    gateway.offline = True
    make_account.clock.now += 80 * HOUR - 1  # due again (retry), unreachable, grace over
    account.tick()
    assert make_account.events and make_account.events[-1][1]["reason"] == "OFFLINE_GRACE_EXPIRED"


# --- transport gate ------------------------------------------------------------------------
class FakeLive:
    def __init__(self, active=False):
        self.engine = SimpleNamespace(active=active)
        self.started = []

    def start(self, params):
        self.started.append(params)
        return {"active": True}

    def status(self, params=None):
        return {"active": self.engine.active}

    def stop(self, params=None):
        self.engine.active = False
        return {"active": False}

    def refresh(self):
        pass


@pytest.fixture
def gated(tmp_path, make_account):
    service = StudioService(tmp_path / "studio")
    transport = StudioTransport(service)
    transport.live = FakeLive()
    account = transport.account = make_account()
    account.live_running = transport.live_running
    yield transport, account
    service.close()


def code_of(operation):
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_dispatch_requires_license_except_account_and_status(gated):
    transport, _ = gated
    assert code_of(lambda: transport.dispatch("studio.presenters.list", {})) == "LICENSE_REQUIRED"
    assert code_of(lambda: transport.dispatch("studio.live.start", {"session_id": "x"})) == "LICENSE_REQUIRED"
    assert transport.dispatch("studio.status", {})["version"]
    assert transport.dispatch("studio.account.status", {})["state"] == "unactivated"
    transport.dispatch("studio.account.activate", {"activation_key": "SARAE-ACT-ABCDEF123"})
    assert transport.dispatch("studio.presenters.list", {}) == {"presenters": []}


def test_running_live_keeps_working_but_no_new_live_starts(gated, gateway):
    transport, account = gated
    transport.dispatch("studio.account.activate", {"activation_key": "SARAE-ACT-ABCDEF123"})
    transport.live.engine.active = True
    gateway.license_error = (403, "LICENSE_EXPIRED")
    status = transport.dispatch("studio.account.check", {})
    assert not status["licensed"] and status["live_running"]
    assert transport.dispatch("studio.presenters.list", {}) == {"presenters": []}
    assert transport.dispatch("studio.live.status", {}) == {"active": True}
    error = pytest.raises(StudioError, transport.dispatch, "studio.live.start", {"session_id": "x"}).value
    assert error.code == "LICENSE_REQUIRED" and "Phiên đang phát" in error.message
    transport.dispatch("studio.live.stop", {})  # the LIVE ends: the lock applies again
    assert code_of(lambda: transport.dispatch("studio.presenters.list", {})) == "LICENSE_REQUIRED"


def test_dev_bypass_opens_dispatch(tmp_path, make_account):
    service = StudioService(tmp_path / "studio-dev")
    try:
        transport = StudioTransport(service)
        transport.account = make_account(SARAE_STUDIO_DEV_UNLICENSED="1")
        assert transport.dispatch("studio.presenters.list", {}) == {"presenters": []}
    finally:
        service.close()
