import io
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))

from sarae_voice.api import create_app  # noqa: E402
from sarae_voice.config import Settings  # noqa: E402
from sarae_voice.engine import FakeEngine  # noqa: E402

TOKEN = "test-orchestrator-token"
ADMIN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_wav(seconds: float = 4.0, sr: int = 48000, freq: float = 220, tail_silence: float = 0.4,
             fmt: str = "WAV") -> bytes:
    t = np.arange(int(sr * seconds)) / sr
    audio = np.concatenate([np.zeros(int(sr * 0.2)), 0.3 * np.sin(2 * np.pi * freq * t), np.zeros(int(sr * tail_silence))])
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format=fmt, subtype="PCM_16")
    return buf.getvalue()


def settings_for(path: Path, **overrides) -> Settings:
    values = {"engine": "fake", "data_dir": path, "orchestrator_tokens": TOKEN, "admin_tokens": ADMIN, **overrides}
    return Settings(_env_file=None, **values)


def wait_ready(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get("/health/ready")
        if r.json()["state"] != "loading":
            return r.json()
        time.sleep(0.01)
    raise AssertionError("engine still loading")


@pytest.fixture
def app_factory(tmp_path):
    clients = []

    def make(engine: FakeEngine | None = None, **overrides):
        engine = engine or FakeEngine()
        client = TestClient(create_app(settings_for(tmp_path / "data", **overrides), engine))
        client.__enter__()
        clients.append(client)
        return client, engine
    yield make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(app_factory):
    c, engine = app_factory()
    wait_ready(c)
    return c, engine


def put_voice(client: TestClient, voice_id: str = "shop_main", wav: bytes | None = None, **form):
    return client.put(f"/v1/voices/{voice_id}", headers=AUTH,
                      files={"wav": ("ref.wav", wav if wav is not None else make_wav(), "audio/wav")}, data=form)
