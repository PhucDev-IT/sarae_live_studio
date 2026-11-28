"""Cloned voices from the Sarae gateway: catalog, synthesis, TTS_BUSY retry and the Edge fallback."""
from __future__ import annotations

import io
import json
import logging
import shutil
import wave

import httpx
import pytest

from sarae_agent.errors import AgentError
from sarae_agent.studio.remote_voice import FALLBACK_VOICE, RemoteVoiceServer, voice_backend
from sarae_agent.studio.service import StudioError
from sarae_agent.studio.voice_server import LocalVoiceServer
from sarae_agent.voice.providers import ProviderRegistry
from test_studio_replies import comment, desk_for, isolated_environment, studio  # noqa: F401 - fixtures

CREDENTIAL = "cred-super-secret-0123456789"
BASE = "https://api.puns.id.vn"
VOICES = {"online": True, "voices": [{"voice_id": "linh_phuong", "name": "Linh Phương", "rev": "rev1", "ready": True},
                                     {"voice_id": "st_design", "name": "ST DESIGN", "rev": "rev2", "ready": False},
                                     {"voice_id": "../bad", "name": "x"}]}


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * 2400)
    return buffer.getvalue()


class FakeAccount:
    def __init__(self, activated=True):
        self.activated, self.reports = activated, []

    def auth(self):
        if not self.activated:
            return None
        return BASE, {"X-Sarae-Agent": "agt_one", "Authorization": "Bearer " + CREDENTIAL}

    def report(self, code):
        self.reports.append(code)


class FakeEdge:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice, model, settings, output, api_key, first_audio):
        self.calls.append({"text": text, "voice": voice, "model": model, "settings": settings})
        output.write_bytes(b"ID3-edge-audio")
        first_audio()


class Clock:
    def __init__(self):
        self.now, self.sleeps = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def server(handler, *, account=None, edge=None, clock=None, ffmpeg=None):
    clock = clock or Clock()
    return RemoteVoiceServer(account or FakeAccount(), transport=httpx.MockTransport(handler), edge=edge or FakeEdge(),
                             sleep=clock.sleep, clock=clock, ffmpeg=ffmpeg)


def fake_ffmpeg(monkeypatch):
    """FFmpeg's role (WAV in, MP3 out) without the binary."""
    def run(command, input, **kwargs):
        assert input.startswith(b"RIFF") and "libmp3lame" in command
        with open(command[-1], "wb") as file:
            file.write(b"ID3-cloned-audio")
        return type("Done", (), {"returncode": 0})()
    monkeypatch.setattr("sarae_agent.studio.remote_voice.subprocess.run", run)
    return "ffmpeg"


def test_catalog_lists_remote_voices_with_device_auth(caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=VOICES)
    voices = server(handle)
    result = voices.refresh()
    assert str(seen[0].url) == BASE + "/api/studio/v1/voices"
    assert seen[0].headers["X-Sarae-Agent"] == "agt_one" and seen[0].headers["Authorization"] == "Bearer " + CREDENTIAL
    remote = [v for v in result["voice_options"] if v["provider"] == "remote_voice"]
    assert [(v["id"], v["ready"]) for v in remote] == [("remote:linh_phuong", True), ("remote:st_design", False)]
    assert result["voice_server"]["online"] is True and result["voice_server"]["mode"] == "remote"
    assert voices.revision("linh_phuong") == "rev1"
    assert any(v["provider"] == "edge" for v in result["voice_options"])
    assert CREDENTIAL not in json.dumps(result) and CREDENTIAL not in caplog.text


def test_catalog_when_gateway_reports_voice_server_off_or_unreachable():
    off = server(lambda request: httpx.Response(200, json={"online": False, "voices": [{**VOICES["voices"][0], "ready": False}]}))
    result = off.refresh()
    assert result["voice_server"]["online"] is False and "Edge" in result["voice_server"]["error"]
    assert next(v for v in result["voice_options"] if v["id"] == "remote:linh_phuong")["ready"] is False

    def down(request):
        raise httpx.ConnectError("down", request=request)
    assert server(down).refresh()["voice_server"]["online"] is False


def test_catalog_without_activation_makes_no_request():
    calls = []
    result = server(lambda request: calls.append(request), account=FakeAccount(activated=False)).refresh()
    assert calls == [] and result["voice_server"]["online"] is False


def test_catalog_refused_device_is_reported_to_account():
    account = FakeAccount()
    server(lambda request: httpx.Response(403, headers={"X-Sarae-Error": "AGENT_REVOKED"}, json={"detail": "Máy đã bị gỡ."}),
           account=account).refresh()
    assert account.reports == ["AGENT_REVOKED"]


def test_synthesis_success_sends_contract_body(tmp_path, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=wav_bytes(), headers={"Content-Type": "audio/wav", "X-Voice-Revision": "rev1"})
    edge = FakeEdge()
    voices = server(handle, edge=edge, ffmpeg=fake_ffmpeg(monkeypatch))
    output = tmp_path / "speech.mp3"
    first = []
    assert voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, output, None, lambda: first.append(1)) is None
    assert str(requests[0].url) == BASE + "/api/studio/v1/tts/synthesize"
    assert json.loads(requests[0].content) == {"voice_id": "linh_phuong", "text": "Xin chào", "preset": "live_fast"}
    assert requests[0].headers["Authorization"] == "Bearer " + CREDENTIAL
    assert output.read_bytes() == b"ID3-cloned-audio" and first == [1] and edge.calls == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")
def test_synthesis_produces_real_mp3_with_ffmpeg(tmp_path):
    voices = server(lambda request: httpx.Response(200, content=wav_bytes()))
    output = tmp_path / "speech.mp3"
    voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 10}, output, None, lambda: None)
    assert output.stat().st_size > 0 and not output.read_bytes().startswith(b"RIFF")


def test_busy_waits_for_the_previous_line_then_retries(tmp_path, monkeypatch):
    answers = [httpx.Response(429, headers={"X-Sarae-Error": "TTS_BUSY", "Retry-After": "2"}, json={"detail": "bận"}),
               httpx.Response(200, content=wav_bytes())]
    clock, edge = Clock(), FakeEdge()
    voices = server(lambda request: answers.pop(0), clock=clock, edge=edge, ffmpeg=fake_ffmpeg(monkeypatch))
    assert voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None) is None
    assert clock.sleeps == [2.0] and edge.calls == []


def test_busy_too_long_speaks_with_edge(tmp_path):
    clock, edge = Clock(), FakeEdge()
    voices = server(lambda request: httpx.Response(429, headers={"X-Sarae-Error": "TTS_BUSY"}), clock=clock, edge=edge)
    outcome = voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 5}, tmp_path / "a.mp3", None, lambda: None)
    assert outcome == {"cache": False, "fallback": FALLBACK_VOICE}
    assert 15 <= sum(clock.sleeps) <= 20 and edge.calls[0]["voice"] == "vi-VN-HoaiMyNeural"
    assert edge.calls[0]["settings"] == {"rate": 5}


@pytest.mark.parametrize("answer", [
    lambda request: httpx.Response(503, headers={"X-Sarae-Error": "TTS_UNAVAILABLE"}, json={"detail": "tắt"}),
    lambda request: httpx.Response(503, headers={"X-Sarae-Error": "TTS_QUEUE_TIMEOUT"}, json={"detail": "chờ lâu"}),
    lambda request: httpx.Response(502, text="Bad gateway"),
    lambda request: (_ for _ in ()).throw(httpx.ConnectError("down", request=request)),
    lambda request: httpx.Response(200, content=b"not a wav"),
])
def test_unavailable_gateway_speaks_the_line_with_edge(tmp_path, caplog, answer):
    caplog.set_level(logging.DEBUG)
    edge = FakeEdge()
    voices = server(answer, edge=edge)
    output = tmp_path / "a.mp3"
    first = []
    outcome = voices.synthesize("Dạ áo giá 389 nghìn ạ", "linh_phuong", "live_fast", {"rate": 0, "revision": "rev1"},
                                output, None, lambda: first.append(1))
    assert outcome["cache"] is False and output.read_bytes() == b"ID3-edge-audio" and first == [1]
    assert edge.calls[0]["text"] == "Dạ áo giá 389 nghìn ạ" and edge.calls[0]["model"] == "edge-neural"
    assert "Edge" in caplog.text and CREDENTIAL not in caplog.text
    assert voices.catalog()["voice_server"]["fallbacks"] == 1


def test_after_an_outage_next_lines_go_straight_to_edge_for_a_while(tmp_path):
    calls, clock, edge = [], Clock(), FakeEdge()

    def handle(request):
        calls.append(request)
        return httpx.Response(503, headers={"X-Sarae-Error": "TTS_UNAVAILABLE"})
    voices = server(handle, clock=clock, edge=edge)
    for _ in range(3):
        voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None)
    assert len(calls) == 1 and len(edge.calls) == 3 and voices.catalog()["voice_server"]["online"] is False
    clock.now += 31
    voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None)
    assert len(calls) == 2


def test_refused_device_reports_to_account_and_still_speaks(tmp_path):
    account, edge = FakeAccount(), FakeEdge()
    voices = server(lambda request: httpx.Response(403, headers={"X-Sarae-Error": "LICENSE_EXPIRED"}), account=account, edge=edge)
    voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None)
    assert account.reports == ["LICENSE_EXPIRED"] and len(edge.calls) == 1


def test_not_activated_speaks_with_edge_without_request(tmp_path):
    calls, edge = [], FakeEdge()
    voices = server(lambda request: calls.append(request), account=FakeAccount(activated=False), edge=edge)
    voices.synthesize("Xin chào", "linh_phuong", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None)
    assert calls == [] and len(edge.calls) == 1


def test_missing_voice_is_a_configuration_error(tmp_path):
    edge = FakeEdge()
    voices = server(lambda request: httpx.Response(404, headers={"X-Sarae-Error": "VOICE_NOT_FOUND"}), edge=edge)
    with pytest.raises(AgentError) as caught:
        voices.synthesize("Xin chào", "gone_voice", "live_fast", {"rate": 0}, tmp_path / "a.mp3", None, lambda: None)
    assert caught.value.code == "VOICE_NOT_FOUND" and edge.calls == []


def test_fallback_audio_is_not_cached_as_the_cloned_voice(home, monkeypatch):
    from sarae_agent.voice.tts import VoiceEngine
    answers = [httpx.Response(503, headers={"X-Sarae-Error": "TTS_UNAVAILABLE"}), httpx.Response(200, content=wav_bytes())]
    clock = Clock()
    voices = server(lambda request: answers.pop(0), clock=clock, ffmpeg=fake_ffmpeg(monkeypatch))
    registry = ProviderRegistry()
    registry.register("remote_voice", voices)
    engine = VoiceEngine(None, None, registry=registry)
    first = engine._audio("Xin chào", "linh_phuong", 0, "remote_voice", "live_fast", {"revision": "rev1"})
    assert first.name.startswith("once_") and first.read_bytes() == b"ID3-edge-audio"
    clock.now += 31  # the gateway is back
    second = engine._audio("Xin chào", "linh_phuong", 0, "remote_voice", "live_fast", {"revision": "rev1"})
    assert not second.name.startswith("once_") and second.read_bytes() == b"ID3-cloned-audio"
    assert engine._audio("Xin chào", "linh_phuong", 0, "remote_voice", "live_fast", {"revision": "rev1"}) == second
    assert answers == []


def test_reply_desk_speaks_remote_voice_with_revision(studio):
    voices = server(lambda request: httpx.Response(200, json=VOICES))
    voices.refresh()
    desk = desk_for(studio)
    desk.voice_server = voices
    desk.configure({"voice": "remote:linh_phuong", "call_name": False})
    entry = desk.ingest(comment("giá bao nhiêu"), simulated=True)
    desk.answer(desk._entry(desk._queue.popleft()))
    desk.speak({"id": entry["id"]})
    call = desk.voice.calls[0]
    assert call["voice"] == "linh_phuong" and call["provider"] == "remote_voice" and call["settings"]["revision"] == "rev1"
    assert desk.status()["voice_server"]["mode"] == "remote"


def test_local_voice_saved_before_falls_back_to_edge_in_remote_mode(studio):
    desk = desk_for(studio)
    desk.voice_server = server(lambda request: httpx.Response(200, json=VOICES))
    desk.configure({"voice": "local:linh_phuong", "call_name": False})
    entry = desk.ingest(comment("giá bao nhiêu"), simulated=True)
    desk.answer(desk._entry(desk._queue.popleft()))
    desk.speak({"id": entry["id"]})
    call = desk.voice.calls[0]
    assert call["voice"] == "vi-VN-HoaiMyNeural" and "provider" not in call


def test_remote_mode_has_no_local_server_to_start(studio):
    desk = desk_for(studio)
    desk.voice_server = server(lambda request: httpx.Response(200, json=VOICES))
    with pytest.raises(StudioError) as caught:
        desk.voices_start({})
    assert caught.value.code == "VOICE_SERVER_REMOTE"


def test_voice_mode_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("SARAE_VOICE_SERVER_DIR", str(tmp_path / "no-voice-server"))
    assert isinstance(voice_backend(FakeAccount(), {}), RemoteVoiceServer)
    assert isinstance(voice_backend(FakeAccount(), {"SARAE_STUDIO_VOICE_MODE": "local"}), LocalVoiceServer)
