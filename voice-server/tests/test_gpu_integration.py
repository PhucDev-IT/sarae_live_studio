"""Real G-OmniVoice on CUDA through the API. Opt-in (loads the model):

    $env:SARAE_VOICE_GPU_TESTS = "1"; $env:SARAE_VOICE_TEST_REF = "C:\\path\\voice_ref_pause_7s_test.wav"
    .venv\\Scripts\\python.exe -m pytest tests\\test_gpu_integration.py -v -s

Compares with the Phase 0 baseline: transcript in bench/results/phase0 (in Git) and, when the local Phase 0 run
folder still exists, the prompt tokens and the seeded library WAVs sample for sample.
Run with no other CUDA process on the GPU (docs/TTS_SERVER.md 14.1).
"""
import asyncio
import gc
import io
import json
import os
import time
from pathlib import Path

import httpx
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
PHASE0 = ROOT / "bench" / "results" / "phase0" / "quality"
PHASE0_RUN = ROOT / "bench" / "runs" / "p0_20261102_2251"          # local only (audio is never committed)
GOLDEN = {i["id"]: i for i in json.loads((ROOT / "bench" / "golden_set.json").read_text(encoding="utf-8"))["items"]}
PARA = json.loads((ROOT / "bench" / "perf_texts.json").read_text(encoding="utf-8"))["buckets"]["para_1min"]["texts"][0]


def seed_for(item_id: str, rep: int) -> int:
    import zlib
    return zlib.crc32(f"{item_id}:{rep}".encode())


@pytest.fixture(scope="module")
def gpu(tmp_path_factory):
    data = tmp_path_factory.mktemp("voice-data")
    holder = {"data": data, "settings": Settings(_env_file=None, data_dir=data, orchestrator_tokens=TOKEN,
                                                 max_text_chars=3000)}
    client = TestClient(create_app(holder["settings"]))
    client.__enter__()
    started = time.perf_counter()
    assert wait_ready(client, timeout=600)["state"] == "ready", client.get("/health/ready").json()
    holder.update(client=client, ready_s=time.perf_counter() - started, outputs={})
    yield holder
    if holder.get("client"):
        holder["client"].__exit__(None, None, None)


def synth(client, text, seed=None, voice_id="shop_main"):
    return client.post("/v1/tts/synthesize", headers=AUTH,
                       json={"voice_id": voice_id, "text": text, "seed": seed})


def test_01_status_reports_pinned_baseline(gpu):
    s = gpu["client"].get("/v1/status", headers=AUTH).json()
    e = s["engine"]
    print(f"\nready after {gpu['ready_s']:.1f}s, load timings {e['load_timings']}, memory {s['memory']}")
    assert e["checkpoint_revision"] == "f2b73211e57db389d81ab817987f90c02e0898ba"
    assert e["versions"]["omnivoice"] == "0.2.1" and e["sample_rate"] == 24000 and e["dtype"] == "float16"


def test_02_register_voice_with_whisper_matches_phase0(gpu):
    wav = Path(os.environ["SARAE_VOICE_TEST_REF"]).read_bytes()
    r = gpu["client"].put("/v1/voices/shop_main", headers=AUTH, files={"wav": ("ref.wav", wav, "audio/wav")})
    assert r.status_code == 201, r.text
    p = r.json()
    print(f"\nprompt {p['prompt']}, qc warnings {p['qc']['warnings']}")
    baseline = json.loads((PHASE0 / "prompts" / "S-ASR.json").read_text(encoding="utf-8"))
    assert p["transcript"]["mode"] == "asr" and p["prompt"]["asr_used"] is True
    assert p["transcript"]["ref_text_used"] == baseline["ref_text_used"]
    assert p["prompt"]["tokens"] == baseline["tokens_shape"][-1]
    assert (gpu["data"] / "voices" / "shop_main" / "ref.wav").read_bytes() == wav


@pytest.mark.skipif(not (PHASE0_RUN / "prompts" / "S-ASR.pt").is_file(), reason="local Phase 0 run not present")
def test_03_prompt_file_equals_phase0_prompt(gpu):
    import torch
    from omnivoice import VoiceClonePrompt
    profile = gpu["client"].get("/v1/voices/shop_main", headers=AUTH).json()
    ours = VoiceClonePrompt.load(str(gpu["data"] / "voices" / "shop_main" / "prompts" / f"{profile['prompt']['compat']}.pt"))
    base = VoiceClonePrompt.load(str(PHASE0_RUN / "prompts" / "S-ASR.pt"))
    assert torch.equal(ours.ref_audio_tokens, base.ref_audio_tokens) and ours.ref_text == base.ref_text


@pytest.mark.skipif(not (PHASE0_RUN / "S-ASR").is_dir(), reason="local Phase 0 run not present")
@pytest.mark.parametrize("item", ["s01", "s03", "p01", "r02", "c01", "e03"])
def test_04_api_output_equals_phase0_library_output(gpu, item):
    r = synth(gpu["client"], GOLDEN[item]["text"], seed=seed_for(item, 0))
    assert r.status_code == 200, r.text
    ours, sr = sf.read(io.BytesIO(r.content), dtype="int16")
    base, base_sr = sf.read(PHASE0_RUN / "S-ASR" / f"{item}_r0.wav", dtype="int16")
    gpu["outputs"][item] = r.content
    assert sr == base_sr == 24000 and ours.shape == base.shape
    diff = int(np.max(np.abs(ours.astype(int) - base.astype(int))))
    print(f"\n{item}: {len(ours) / sr:.2f}s, max |diff| vs Phase 0 = {diff}, inference {r.headers['X-Inference-Ms']} ms")
    assert diff == 0


def test_05_long_text(gpu):
    r = synth(gpu["client"], PARA, seed=1)
    assert r.status_code == 200, r.text
    audio, sr = sf.read(io.BytesIO(r.content), dtype="int16")
    print(f"\npara {len(PARA)} chars -> {len(audio) / sr:.1f}s audio in {r.headers['X-Inference-Ms']} ms")
    assert 40 < len(audio) / sr < 70 and int(np.max(np.abs(audio))) < 32767


def test_06_consecutive_requests_keep_memory_flat(gpu):
    c = gpu["client"]
    texts = ["Dạ có size Mờ nha chị.", "Cả nhà thả tim giúp shop một cái ạ.",
             "Chị cao một mét sáu nặng năm mươi hai ký thì lấy size Mờ là vừa đẹp nha chị."]
    allocated = []
    for i in range(30):
        assert synth(c, texts[i % 3]).status_code == 200
        allocated.append(c.get("/v1/status", headers=AUTH).json()["memory"]["allocated_gib"])
    print(f"\nallocated GiB first {allocated[0]} last {allocated[-1]} max {max(allocated)}")
    assert max(allocated) - allocated[0] < 0.05


def test_07_concurrent_requests_use_one_gpu_lane(gpu):
    c = gpu["client"]

    async def run():
        transport = httpx.ASGITransport(app=c.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=120) as ac:
            return await asyncio.gather(*[ac.post("/v1/tts/synthesize", headers=AUTH, json={
                "voice_id": "shop_main", "text": "Dạ mẫu này còn màu đen nha chị."}) for _ in range(3)])
    responses = asyncio.run(run())
    assert [r.status_code for r in responses] == [200, 200, 200]
    assert c.app.state.gate.max_active_seen == 1
    print("\nqueue ms:", sorted(float(r.headers["X-Queue-Ms"]) for r in responses))


def test_08_restart_reuses_pt_and_reproduces_audio(gpu):
    import torch
    gpu["client"].__exit__(None, None, None)
    gpu["client"] = None
    gc.collect()
    torch.cuda.empty_cache()
    client = TestClient(create_app(gpu["settings"]))
    client.__enter__()
    gpu["client"] = client
    assert wait_ready(client, timeout=600)["state"] == "ready"
    w = client.post("/v1/voices/shop_main/warmup", headers=AUTH).json()
    print(f"\nafter restart: {w}")
    assert w["cache"] == "disk"
    assert client.get("/v1/status", headers=AUTH).json()["prompt_cache"]["encoded"] == 0
    if "s01" in gpu["outputs"]:
        r = synth(client, GOLDEN["s01"]["text"], seed=seed_for("s01", 0))
        assert r.headers["X-Prompt-Cache"] == "ram" and r.content == gpu["outputs"]["s01"]
