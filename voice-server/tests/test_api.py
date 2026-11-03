"""Service behaviour with FakeEngine (no GPU): auth, readiness, voices and cache, synthesis, GPU lane limits."""
import asyncio
import io
import json
import time

import httpx
import pytest
import soundfile as sf

from conftest import ADMIN, AUTH, make_wav, put_voice, settings_for, wait_ready
from sarae_voice.api import create_app
from sarae_voice.engine import FakeEngine


def synth(client, text="Dạ có size Mờ nha chị.", voice_id="shop_main", **extra):
    return client.post("/v1/tts/synthesize", headers=AUTH, json={"voice_id": voice_id, "text": text, **extra})


# --- auth and health -------------------------------------------------------------------------------
def test_health_needs_no_token_but_api_does(client):
    c, _ = client
    assert c.get("/health/live").json() == {"status": "alive"}
    assert c.get("/health/ready").status_code == 200
    r = c.get("/v1/status")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TTS_AUTH"
    assert c.get("/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/v1/status", headers=AUTH).status_code == 200
    assert c.get("/v1/status", headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 200


def test_service_refuses_to_start_without_tokens(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(settings_for(tmp_path, orchestrator_tokens="", admin_tokens=""), FakeEngine())


def test_ready_reports_loading_then_ready(app_factory):
    c, _ = app_factory(FakeEngine(delay_s=0.3))
    first = c.get("/health/ready")
    assert first.status_code == 503 and first.json()["state"] == "loading"
    assert c.get("/health/live").status_code == 200          # liveness answers while the model loads
    assert synth(c).json()["error"]["code"] == "TTS_NOT_READY"
    assert wait_ready(c)["state"] == "ready"


def test_gpu_unavailable_is_reported_and_blocks_work(app_factory):
    c, _ = app_factory(FakeEngine(load_error="TTS_GPU_UNAVAILABLE"))
    body = wait_ready(c)
    assert body["state"] == "error" and body["error"]["code"] == "TTS_GPU_UNAVAILABLE"
    assert c.get("/health/ready").status_code == 503
    r = put_voice(c)
    assert r.status_code == 503 and r.json()["error"]["code"] == "TTS_GPU_UNAVAILABLE"
    assert synth(c).status_code == 503


# --- voices ------------------------------------------------------------------------------------------
def test_upload_without_transcript_uses_asr_once_and_keeps_wav(client, tmp_path):
    c, engine = client
    wav = make_wav()
    r = put_voice(c, wav=wav, name="Giọng shop")
    assert r.status_code == 201
    p = r.json()
    assert p["transcript"]["mode"] == "asr" and p["transcript"]["asr_text"] == "Đây là giọng nói mẫu của shop."
    assert p["transcript"]["ref_text_used"] == p["transcript"]["asr_text"]
    assert p["prompt"]["asr_used"] is True and engine.calls["asr"] == 1
    assert (tmp_path / "data" / "voices" / "shop_main" / "ref.wav").read_bytes() == wav   # byte for byte
    assert "end_may_be_cut" not in p["qc"]["warnings"]
    # Same WAV again: idempotent, nothing re-encoded.
    again = put_voice(c, wav=wav)
    assert again.status_code == 200 and again.json()["rev"] == p["rev"]
    assert engine.calls["create_prompt"] == 1


def test_manual_transcript_changes_rev_and_invalidates_prompt(client, tmp_path):
    c, engine = client
    first = put_voice(c).json()
    second = put_voice(c, transcript="Xin chào cả nhà, đây là shop.").json()
    assert second["rev"] != first["rev"] and second["transcript"]["mode"] == "manual"
    assert second["prompt"]["compat"] != first["prompt"]["compat"]
    prompts = list((tmp_path / "data" / "voices" / "shop_main" / "prompts").glob("*.pt"))
    assert [p.stem for p in prompts] == [second["prompt"]["compat"]]          # old prompt file removed
    assert engine.prompt_texts == [None, "Xin chào cả nhà, đây là shop."]
    # Back to Whisper: the stored ASR text is reused, Whisper does not run again.
    back = c.put("/v1/voices/shop_main/transcript", headers=AUTH, json={"transcript": None}).json()
    assert back["transcript"]["mode"] == "asr" and back["rev"] == first["rev"]
    assert engine.calls["asr"] == 1 and engine.prompt_texts[-1] == first["transcript"]["asr_text"]


def test_new_wav_invalidates_and_reruns_asr(client):
    c, engine = client
    first = put_voice(c).json()
    second = put_voice(c, wav=make_wav(freq=330)).json()
    assert second["wav"]["sha256"] != first["wav"]["sha256"] and second["rev"] != first["rev"]
    assert engine.calls["asr"] == 2


@pytest.mark.parametrize("wav,code,status", [
    (b"not audio", "INVALID_AUDIO", 422),
    (make_wav(seconds=0.3, tail_silence=0), "INVALID_AUDIO", 422),
    (make_wav(seconds=25), "INVALID_AUDIO", 422),
    (make_wav(fmt="FLAC"), "INVALID_AUDIO", 422),
    (b"", "INVALID_AUDIO", 422),
])
def test_bad_reference_is_rejected_and_not_kept(client, tmp_path, wav, code, status):
    c, _ = client
    r = put_voice(c, wav=wav)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert not (tmp_path / "data" / "voices" / "shop_main").exists()


def test_reference_too_large(app_factory):
    c, _ = app_factory(max_wav_bytes=10_000)
    wait_ready(c)
    r = put_voice(c)
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_cut_reference_is_reported_not_changed(client):
    c, _ = client
    p = put_voice(c, wav=make_wav(tail_silence=0)).json()
    assert "end_may_be_cut" in p["qc"]["warnings"]


def test_bad_voice_id_and_unknown_voice(client):
    c, _ = client
    assert put_voice(c, voice_id="a/b").status_code in (404, 405, 422)
    assert put_voice(c, voice_id="x").json()["error"]["code"] == "INVALID_PARAMS"
    assert c.get("/v1/voices/missing_voice", headers=AUTH).json()["error"]["code"] == "VOICE_NOT_FOUND"
    assert synth(c, voice_id="missing_voice").status_code == 404


def test_asr_failure_asks_for_transcript(client):
    c, engine = client
    engine.asr_text = "  "
    r = put_voice(c)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VOICE_ASR_FAILED"
    assert put_voice(c, transcript="Xin chào cả nhà.").status_code == 201


def test_list_get_delete(client):
    c, _ = client
    put_voice(c, voice_id="voice_a")
    put_voice(c, voice_id="voice_b")
    assert [v["voice_id"] for v in c.get("/v1/voices", headers=AUTH).json()["voices"]] == ["voice_a", "voice_b"]
    assert c.delete("/v1/voices/voice_a", headers=AUTH).status_code == 204
    assert c.get("/v1/voices/voice_a", headers=AUTH).status_code == 404
    assert c.delete("/v1/voices/voice_a", headers=AUTH).status_code == 404


# --- prompt cache across restarts --------------------------------------------------------------------
def test_restart_reuses_prompt_file_without_reencoding(app_factory):
    c1, e1 = app_factory()
    wait_ready(c1)
    put_voice(c1)
    c2, e2 = app_factory()                       # same data dir, fresh process state
    wait_ready(c2)
    w = c2.post("/v1/voices/shop_main/warmup", headers=AUTH).json()
    assert w["cache"] == "disk" and e2.calls["create_prompt"] == 0 and e2.calls["load_prompt"] == 1
    assert c2.post("/v1/voices/shop_main/warmup", headers=AUTH).json()["cache"] == "ram"
    assert synth(c2).headers["X-Prompt-Cache"] == "ram"


def test_new_checkpoint_revision_rebuilds_from_stored_asr_text(app_factory):
    c1, _ = app_factory()
    wait_ready(c1)
    first = put_voice(c1).json()
    c2, e2 = app_factory(FakeEngine(revision="another-rev"))
    wait_ready(c2)
    w = c2.post("/v1/voices/shop_main/warmup", headers=AUTH).json()
    assert w["cache"] == "encoded"
    assert e2.calls["asr"] == 0 and e2.prompt_texts == [first["transcript"]["asr_text"]]
    assert c2.get("/v1/voices/shop_main", headers=AUTH).json()["prompt"]["compat"] != first["prompt"]["compat"]


def test_damaged_prompt_file_is_rebuilt(app_factory, tmp_path):
    c1, _ = app_factory()
    wait_ready(c1)
    p = put_voice(c1).json()
    pt = tmp_path / "data" / "voices" / "shop_main" / "prompts" / f"{p['prompt']['compat']}.pt"
    pt.write_text("garbage", encoding="utf-8")
    c2, e2 = app_factory()
    wait_ready(c2)
    assert c2.post("/v1/voices/shop_main/warmup", headers=AUTH).json()["cache"] == "encoded"
    assert c2.get("/v1/status", headers=AUTH).json()["prompt_cache"]["disk_rejected"] == 1


# --- synthesis ----------------------------------------------------------------------------------------
def test_synthesize_returns_wav_and_trace_headers(client):
    c, _ = client
    rev = put_voice(c).json()["rev"]
    r = synth(c)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    info = sf.info(io.BytesIO(r.content))
    assert (info.samplerate, info.channels, info.subtype, info.format) == (24000, 1, "PCM_16", "WAV")
    assert r.headers["X-Voice-Rev"] == rev and r.headers["X-Preset"] == "demo_default"
    trace = json.loads(r.headers["X-Trace"])
    assert trace["generation_config"] == {"num_step": 32, "guidance_scale": 2.0, "denoise": True,
                                          "preprocess_prompt": True, "postprocess_output": True}
    for h in ("X-Request-Id", "X-Inference-Ms", "X-Queue-Ms", "X-Audio-Ms", "X-Server-Ms", "X-Prompt-Cache"):
        assert h in r.headers


@pytest.mark.parametrize("body,code", [
    ({"text": ""}, "INVALID_PARAMS"),
    ({"text": "   "}, "INVALID_PARAMS"),
    ({"text": "a" * 451}, "TEXT_TOO_LONG"),
    ({"preset": "fast"}, "INVALID_PARAMS"),
    ({"voice_id": "bad id"}, "INVALID_PARAMS"),
])
def test_synthesize_validation(client, body, code):
    c, _ = client
    put_voice(c)
    payload = {"voice_id": "shop_main", "text": "Dạ có size Mờ nha chị.", **body}
    r = c.post("/v1/tts/synthesize", headers=AUTH, json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_errors_never_echo_the_text(client):
    c, _ = client
    secret = "số điện thoại 0909" + "x" * 500
    r = synth(c, text=secret)
    assert "0909" not in r.text


def test_oom_and_inference_errors_leave_service_ready(client):
    c, engine = client
    put_voice(c)
    engine.fail_next = "oom"
    r = synth(c)
    assert r.status_code == 507 and r.json()["error"]["code"] == "TTS_OOM" and "Retry-After" in r.headers
    engine.fail_next = "inference"
    assert synth(c).json()["error"]["code"] == "TTS_INFERENCE_FAILED"
    assert synth(c).status_code == 200 and c.get("/health/ready").status_code == 200


def test_cuda_failure_marks_engine_not_ready(client):
    c, engine = client
    put_voice(c)
    engine.fail_next = "cuda"
    assert synth(c).status_code == 503
    assert c.get("/health/ready").json()["error"]["code"] == "TTS_GPU_FAILED"


def test_many_sequential_requests(client):
    c, engine = client
    put_voice(c)
    for i in range(50):
        assert synth(c, text=f"Câu số {i} nha chị.").status_code == 200
    status = c.get("/v1/status", headers=AUTH).json()
    assert status["synthesis"]["ok"] == 50 and status["prompt_cache"]["ram_hit"] >= 49


# --- one GPU lane -------------------------------------------------------------------------------------
async def _concurrent(app, n: int, text: str = "Dạ có size Mờ nha chị."):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        async def one():
            return await ac.post("/v1/tts/synthesize", headers=AUTH, json={"voice_id": "shop_main", "text": text})

        async def probe():
            await asyncio.sleep(0.05)
            started = time.perf_counter()
            r = await ac.get("/health/live")
            return r.status_code, time.perf_counter() - started
        results = await asyncio.gather(*[one() for _ in range(n)], probe())
        return results[:-1], results[-1]


def test_concurrent_requests_never_run_in_parallel_and_loop_stays_free(app_factory):
    c, engine = app_factory(FakeEngine(delay_s=0.15), max_pending=8)
    wait_ready(c)
    put_voice(c)
    responses, (live_status, live_s) = asyncio.run(_concurrent(c.app, 5))
    assert [r.status_code for r in responses] == [200] * 5
    assert engine.max_active == 1 and c.app.state.gate.max_active_seen == 1
    assert live_status == 200 and live_s < 0.1           # event loop not blocked by inference
    queued = sorted(float(r.headers["X-Queue-Ms"]) for r in responses)
    assert queued[-1] >= 4 * 150 * 0.8                   # the last one waited for the other four


def test_backpressure_rejects_with_retry_after(app_factory):
    c, engine = app_factory(FakeEngine(delay_s=0.2), max_pending=2)
    wait_ready(c)
    put_voice(c)
    responses, _ = asyncio.run(_concurrent(c.app, 5))
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 2 and codes.count(429) == 3
    busy = next(r for r in responses if r.status_code == 429)
    assert busy.json()["error"]["code"] == "TTS_BUSY" and busy.headers["Retry-After"] == "2"


def test_queue_timeout_withdraws_waiting_call(app_factory):
    c, engine = app_factory(FakeEngine(delay_s=0.4), max_pending=8, queue_timeout_s=0.2)
    wait_ready(c)
    put_voice(c)
    calls_before = engine.calls["synthesize"]
    responses, _ = asyncio.run(_concurrent(c.app, 3))
    codes = sorted(r.status_code for r in responses)
    assert codes[0] == 200 and codes.count(503) >= 1
    assert all(r.json()["error"]["code"] == "TTS_QUEUE_TIMEOUT" for r in responses if r.status_code == 503)
    time.sleep(0.5)
    assert engine.calls["synthesize"] - calls_before == codes.count(200)   # withdrawn calls never ran


def test_openapi_documents_security_and_wav(client):
    c, _ = client
    spec = c.get("/openapi.json").json()
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert "audio/wav" in spec["paths"]["/v1/tts/synthesize"]["post"]["responses"]["200"]["content"]
