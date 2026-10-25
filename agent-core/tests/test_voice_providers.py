"""Provider contract and queue configuration tests; all HTTP calls are mocked."""
import httpx
import pytest

from sarae_agent.errors import AgentError
from sarae_agent.voice.providers import ElevenLabsProvider, ProviderRegistry
from sarae_agent.voice.tts import VoiceService


def test_elevenlabs_streams_to_file_with_selected_model_and_settings(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"ID3audio")
    provider = ElevenLabsProvider(httpx.MockTransport(handler))
    first = []
    target = tmp_path / "voice.mp3"
    provider.synthesize("Xin chào", "voice123", "eleven_flash_v2_5",
                        {"speed": 1.1, "stability": 0.6}, target, "test-secret", lambda: first.append(True))
    assert target.read_bytes() == b"ID3audio"
    assert len(first) == 1
    assert requests[0].headers["xi-api-key"] == "test-secret"
    assert requests[0].read().decode().find('"model_id":"eleven_flash_v2_5"') >= 0
    assert requests[0].url.path.endswith("/voice123/stream")


@pytest.mark.parametrize("status,code", [(401, "TTS_AUTH"), (402, "TTS_QUOTA"), (404, "TTS_API_ERROR")])
def test_elevenlabs_non_retryable_errors(tmp_path, status, code):
    provider = ElevenLabsProvider(httpx.MockTransport(lambda _: httpx.Response(status, json={"secret": "not echoed"})))
    with pytest.raises(AgentError) as error:
        provider.synthesize("Xin chào", "voice123", "eleven_flash_v2_5", {},
                            tmp_path / "voice.mp3", "test-secret", lambda: None)
    assert error.value.code == code
    assert "not echoed" not in error.value.message


def test_elevenlabs_retries_rate_limit(tmp_path, monkeypatch):
    calls = []
    def handler(_):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, content=b"ID3audio")
    monkeypatch.setattr("sarae_agent.voice.providers.time.sleep", lambda _: None)
    provider = ElevenLabsProvider(httpx.MockTransport(handler))
    provider.synthesize("Xin chào", "voice123", "eleven_flash_v2_5", {},
                        tmp_path / "voice.mp3", "test-secret", lambda: None)
    assert len(calls) == 2


def test_elevenlabs_timeout_is_bounded_and_retryable(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("test timeout")
    monkeypatch.setattr("sarae_agent.voice.providers.time.sleep", lambda _: None)
    provider = ElevenLabsProvider(httpx.MockTransport(handler))
    with pytest.raises(AgentError) as error:
        provider.synthesize("Xin chào", "voice123", "eleven_flash_v2_5", {},
                            tmp_path / "voice.mp3", "test-secret", lambda: None)
    assert error.value.code == "TTS_TIMEOUT"
    assert error.value.retryable
    assert len(calls) == provider.RETRIES


def test_cache_key_isolates_provider_model_voice_and_settings():
    base = VoiceService._key("Xin chào", "voice123", 0, "elevenlabs", "eleven_flash_v2_5", {"speed": 1})
    assert base != VoiceService._key("Xin chào", "voice123", 0, "elevenlabs", "eleven_multilingual_v2", {"speed": 1})
    assert base != VoiceService._key("Xin chào", "other", 0, "elevenlabs", "eleven_flash_v2_5", {"speed": 1})
    assert base != VoiceService._key("Xin chào", "voice123", 0, "elevenlabs", "eleven_flash_v2_5", {"speed": 1.1})
    assert VoiceService._key("Xin chào", "vi-VN-HoaiMyNeural", 0) != base


def test_registry_does_not_fallback():
    with pytest.raises(AgentError) as error:
        ProviderRegistry().get("missing")
    assert error.value.code == "TTS_PROVIDER"
