"""Phase 2 essentials on the real model (opt-in, same switches as test_gpu_integration.py):
jobs reproduce the Phase 0 audio, result cache, cancel queued/running on the real GPU lane, a long job running
when a LIVE lease starts, many mixed jobs on one lane, memory flat.
"""
import io
import json
import os
import time
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from conftest import AUTH, TOKEN, wait_ready
from sarae_voice.api import create_app
from sarae_voice.config import Settings

pytestmark = [pytest.mark.gpu, pytest.mark.skipif(
    os.environ.get("SARAE_VOICE_GPU_TESTS") != "1" or not os.environ.get("SARAE_VOICE_TEST_REF"),
    reason="set SARAE_VOICE_GPU_TESTS=1 and SARAE_VOICE_TEST_REF to run against the real model")]

ROOT = Path(__file__).resolve().parents[1]
PHASE0_RUN = ROOT / "bench" / "runs" / "p0_20261102_2251"
GOLDEN = {i["id"]: i for i in json.loads((ROOT / "bench" / "golden_set.json").read_text(encoding="utf-8"))["items"]}
BUCKETS = json.loads((ROOT / "bench" / "perf_texts.json").read_text(encoding="utf-8"))["buckets"]


def seed_for(item_id: str, rep: int) -> int:
    return zlib.crc32(f"{item_id}:{rep}".encode())


@pytest.fixture(scope="module")
def gpu(tmp_path_factory):
    data = tmp_path_factory.mktemp("voice-jobs")
    s = Settings(_env_file=None, data_dir=data, orchestrator_tokens=TOKEN, queue_live=40, queue_preview=40,
                 queue_prepare=40)
    client = TestClient(create_app(s))
    client.__enter__()
    assert wait_ready(client, timeout=600)["state"] == "ready"
    wav = Path(os.environ["SARAE_VOICE_TEST_REF"]).read_bytes()
    assert client.put("/v1/voices/shop_main", headers=AUTH, files={"wav": ("ref.wav", wav, "audio/wav")}).status_code == 201
    yield client
    client.__exit__(None, None, None)


def job(c, text, priority="live", **extra):
    r = c.post("/v1/tts/jobs", headers=AUTH, json={"voice_id": "shop_main", "text": text, "priority": priority,
                                                   "reader_agent_id": "agent-1", **extra})
    assert r.status_code in (200, 202), r.text
    return r.json()


def final(c, job_id, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = c.get(f"/v1/tts/jobs/{job_id}", headers=AUTH, params={"wait": 10}).json()
        if body["state"] in ("done", "failed", "cancelled", "expired"):
            return body
    raise AssertionError("job did not finish")


def read(c, body):
    return c.get(f"/v1/tts/jobs/{body['job_id']}/audio", params={"wait": 20},
                 headers={"Authorization": f"Bearer {body['read_token']}", "X-Sarae-Agent-Id": "agent-1"})


@pytest.mark.skipif(not (PHASE0_RUN / "S-ASR").is_dir(), reason="local Phase 0 run not present")
@pytest.mark.parametrize("item", ["s01", "p02", "e01"])
def test_job_audio_equals_phase0(gpu, item):
    body = job(gpu, GOLDEN[item]["text"], seed=seed_for(item, 0))
    r = read(gpu, body)
    assert r.status_code == 200, r.text
    ours, _ = sf.read(io.BytesIO(r.content), dtype="int16")
    base, _ = sf.read(PHASE0_RUN / "S-ASR" / f"{item}_r0.wav", dtype="int16")
    assert ours.shape == base.shape and int(np.max(np.abs(ours.astype(int) - base.astype(int)))) == 0
    again = job(gpu, GOLDEN[item]["text"], seed=seed_for(item, 0))
    assert again["created"] == "result_cache" and read(gpu, again).content == r.content


def test_long_job_running_when_live_starts(gpu):
    long_body = job(gpu, BUCKETS["long_3min"]["texts"][0], priority="long", seed=1)
    deadline = time.monotonic() + 60
    while gpu.get(f"/v1/tts/jobs/{long_body['job_id']}", headers=AUTH).json()["state"] != "running":
        assert time.monotonic() < deadline
        time.sleep(0.1)
    time.sleep(2)
    lease = gpu.put("/v1/admission/live", headers=AUTH, json={"owner_ref": "o", "session_id": "s", "ttl_s": 120}).json()
    running = lease["gpu"]["running"]
    print(f"\nlease while long job runs: ready_for_live={lease['ready_for_live']} reason={lease['reason']} running={running}")
    assert lease["ready_for_live"] is False and lease["reason"] == "LONG_JOB_RUNNING"
    t0 = time.monotonic()
    live = job(gpu, "Dạ có size Mờ nha chị.")
    cancelled = job(gpu, "Câu này sẽ bị hủy trước khi chạy nha.")
    assert gpu.delete(f"/v1/tts/jobs/{cancelled['job_id']}", headers=AUTH).status_code == 200
    live_final = final(gpu, live["job_id"])
    waited = time.monotonic() - t0
    long_final = final(gpu, long_body["job_id"])
    print(f"live job waited {waited:.1f}s behind the long job (estimated remaining was "
          f"{running['estimated_remaining_s']}s); long audio {long_final['audio']['audio_ms'] / 1000:.0f}s in "
          f"{long_final['timings']['inference_ms'] / 1000:.1f}s; live queue {live_final['timings']['queue_ms']} ms")
    assert live_final["state"] == "done" and final(gpu, cancelled["job_id"])["state"] == "cancelled"
    assert gpu.app.state.gate.counts["live"]["cancelled"] >= 1
    gpu.delete("/v1/admission/live", headers=AUTH, params={"owner_ref": "o", "session_id": "s"})


def test_cancel_running_job_discards_real_audio(gpu):
    body = job(gpu, BUCKETS["para_1min"]["texts"][0], priority="long", seed=2)
    while gpu.get(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH).json()["state"] != "running":
        time.sleep(0.1)
    gpu.delete(f"/v1/tts/jobs/{body['job_id']}", headers=AUTH)
    end = final(gpu, body["job_id"])
    assert end["state"] == "cancelled" and end["audio"] is None and read(gpu, body).status_code == 410


def test_mixed_jobs_one_lane_memory_flat(gpu):
    texts = BUCKETS["live_short"]["texts"] + BUCKETS["live_typical"]["texts"]
    before = gpu.get("/v1/status", headers=AUTH).json()["memory"]["allocated_gib"]
    # 30 jobs need ~70 s of GPU: give them a deadline beyond that (with the 60 s defaults the last ones expire, by design).
    late = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    bodies = [job(gpu, texts[i % len(texts)], priority=("live", "preview", "prepare")[i % 3], deadline_at=late)
              for i in range(30)]
    finals = [final(gpu, b["job_id"]) for b in bodies]
    assert all(f["state"] == "done" for f in finals), [(f["priority"], f["state"], f["error"], f["timings"]) for f in finals if f["state"] != "done"]
    assert gpu.app.state.gate.max_active_seen == 1
    after = gpu.get("/v1/status", headers=AUTH).json()
    by = {}
    for f in finals:
        by.setdefault(f["priority"], []).append(f["timings"].get("queue_ms", 0))
    print("\nqueue wait ms by priority (max):", {k: max(v) for k, v in by.items()},
          "memory", after["memory"], "latency", after["latency"]["job_inference"])
    assert after["memory"]["allocated_gib"] - before < 0.05
