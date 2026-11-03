# Sarae Voice Server (G-OmniVoice)

Independent TTS service for Sarae Live Studio. Design and measured results: [docs/TTS_SERVER.md](../docs/TTS_SERVER.md).

**Status: Phase 1** — standalone FastAPI service: voice profiles with a `VoiceClonePrompt` cache, synchronous
synthesis returning WAV. Nothing in `backend/`, `web/` or `agent-core/` calls it yet. Queue priorities, jobs,
read tokens, relay and fallback are Phase 2+.

## Install (Windows 11, NVIDIA GPU)

```powershell
cd voice-server
.\scripts\install.ps1      # .venv: Python 3.12, torch 2.8.0+cu128, omnivoice==0.2.1, this package (editable)
```

Model weights stay in the Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub`, or `HF_HOME`) and are
never committed. The service loads `g-group-ai-lab/g-omnivoice` at the pinned revision `f2b73211…`; it downloads
that revision once if it is not cached. Set `HF_HUB_OFFLINE=1` afterwards to forbid network access.

## Run

```powershell
.\scripts\run.ps1          # creates .env with a random token the first time, then starts on 127.0.0.1:8780
```

One process, one GPU worker: `python -m sarae_voice` passes the app object to uvicorn, so it cannot fork workers.
Configuration is `SARAE_VOICE_*` environment variables or `voice-server\.env` (see `.env.example`).
OpenAPI: `http://127.0.0.1:8780/docs`.

The model loads in the background: `/health/live` answers at once, `/health/ready` returns 503 `loading` until
the model is on the GPU (≈ 20 s with a warm disk cache on the RTX 3060), then 200 `ready`.

## API (Phase 1)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health/live` | none | process alive |
| GET | `/health/ready` | none | `loading` / `ready` / `error` (+ error code), 200 only when ready |
| GET | `/v1/status` | orchestrator | engine, pinned versions, preset, VRAM, GPU lane, prompt cache hits, latency p50/p95 |
| PUT | `/v1/voices/{voice_id}` | orchestrator | multipart `wav` (+ optional `transcript`, `name`, `owner_ref`): create/replace, prepares the prompt |
| GET | `/v1/voices`, `/v1/voices/{voice_id}` | orchestrator | profiles (transcript actually used, QC report, revision) |
| PUT | `/v1/voices/{voice_id}/transcript` | orchestrator | `{"transcript": "..."}` manual, or `null` back to the stored Whisper text |
| DELETE | `/v1/voices/{voice_id}` | orchestrator | removes the WAV, profile and cached prompts |
| POST | `/v1/voices/{voice_id}/warmup` | orchestrator | prompt into RAM: `ram` / `disk` / `encoded` |
| POST | `/v1/tts/synthesize` | orchestrator | `{"voice_id", "text", "preset": "demo_default", "seed"?}` → `audio/wav` |

Errors are `{"error": {"code", "message"}}`; codes in docs/TTS_SERVER.md.

```powershell
$h = @{ Authorization = "Bearer <token from .env>" }
curl.exe -X PUT http://127.0.0.1:8780/v1/voices/shop_main -H "Authorization: Bearer <token>" -F "wav=@C:\path\voice.wav"
curl.exe -X POST http://127.0.0.1:8780/v1/tts/synthesize -H "Authorization: Bearer <token>" `
  -H "Content-Type: application/json" -d '{\"voice_id\":\"shop_main\",\"text\":\"Dạ có size Mờ nha chị.\"}' -o out.wav -D -
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q                                   # FakeEngine, no GPU
$env:SARAE_VOICE_GPU_TESTS = "1"; $env:SARAE_VOICE_TEST_REF = "C:\path\voice_ref.wav"
.venv\Scripts\python.exe -m pytest tests\test_gpu_integration.py -v -s  # real model; close other CUDA apps first
```

## Benchmarks (`bench/`)

| Script | What |
|---|---|
| `run_library.py`, `run_demo.py`, `analyze.py` | Phase 0 A/B against the official demo on 7862 |
| `run_perf.py`, `recheck_asr.py` | Phase 0 single-process performance, soak, second-ASR recheck |
| `run_http.py` | Phase 1: spawns the service, measures startup, prompt cache, HTTP latency, Phase 0 identity; then `analyze.py --run-name <run>` for the Whisper head/tail check |

Runs go to `bench\runs\` (ignored). Committed snapshots without audio: `bench\results\`.
