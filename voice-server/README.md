# Sarae Voice Server (G-OmniVoice)

Independent TTS service for Sarae Live Studio. Design and measured results: [docs/TTS_SERVER.md](../docs/TTS_SERVER.md).

**Status: Phase 2** — voice profiles with a `VoiceClonePrompt` cache, synchronous synthesis (Phase 1), plus jobs
with one priority-scheduled GPU lane, idempotency, a result cache, per-job read tokens, LIVE leases, Prometheus
metrics and a supervised background service. Nothing in `backend/`, `web/` or `agent-core/` calls it yet (Phase 3).

## Install (Windows 11, NVIDIA GPU)

```powershell
cd voice-server
.\scripts\install.ps1      # .venv: Python 3.12, torch 2.8.0+cu128, omnivoice==0.2.1, this package (editable)
```

Model weights stay in the Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub`, or `HF_HOME`) and are never
committed. The service loads `g-group-ai-lab/g-omnivoice` at the pinned revision `f2b73211…`; it downloads that
revision once if it is not cached. Set `HF_HUB_OFFLINE=1` afterwards to forbid network access.
Voice data, results and logs live in `voice-server\data\` (ignored by Git).

## Development mode

```powershell
.\scripts\run.ps1          # foreground; creates .env with a random token the first time; Ctrl+C stops it
```

## Service mode (background, restarted after crashes)

```powershell
.\scripts\service.ps1 install            # scheduled task at your logon (no password stored); -AtStartup = at boot
.\scripts\service.ps1 start
.\scripts\service.ps1 status             # task state + /health/ready
.\scripts\service.ps1 logs               # data\logs\supervisor.log and server.log
.\scripts\service.ps1 restart | stop | uninstall
```

The task runs `pythonw -m sarae_voice.supervisor`, which starts `python -m sarae_voice` without a window, restarts
it after a crash or a CUDA failure (exit 75; backoff 2–60 s, gives up after 10 quick failures in 10 minutes) and
stops it gracefully (`stop` writes `data\supervisor.stop`; the running GPU call finishes, queued work is withdrawn).
One server per data directory (file lock); a second one exits with code 2. Nothing else on the GPU is ever stopped.

One process, one GPU worker: `python -m sarae_voice` passes the app object to uvicorn, so it cannot fork workers.
Configuration: `SARAE_VOICE_*` environment variables or `voice-server\.env` (see `.env.example`).
OpenAPI: `http://127.0.0.1:8780/docs`.

## API

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health/live`, `/health/ready` | none | alive; `loading` / `ready` / `error` (200 only when ready) |
| GET | `/v1/status` | orchestrator | engine, versions, VRAM, GPU lane, jobs, leases, caches, latency p50/p95 |
| GET | `/metrics` | orchestrator | Prometheus text |
| PUT | `/v1/voices/{voice_id}` | orchestrator | multipart `wav` (+ `transcript`, `name`, `owner_ref`): create/replace, prepares the prompt |
| GET | `/v1/voices`, `/v1/voices/{voice_id}` | orchestrator | profiles |
| PUT | `/v1/voices/{voice_id}/transcript` | orchestrator | manual transcript, or `null` for the stored Whisper text |
| DELETE | `/v1/voices/{voice_id}` | orchestrator | remove a voice |
| POST | `/v1/voices/{voice_id}/warmup` | orchestrator | prompt into RAM |
| POST | `/v1/tts/synthesize` | orchestrator | short text → `audio/wav` at once (preview priority) |
| POST | `/v1/tts/jobs` | orchestrator | queue a job (`Idempotency-Key` header); returns `read_token` for `reader_agent_id` |
| GET | `/v1/tts/jobs/{job_id}?wait=` | orchestrator | job state (never the token) |
| GET | `/v1/tts/jobs/{job_id}/audio?wait=` | read token + `X-Sarae-Agent-Id`, or orchestrator | the WAV |
| DELETE | `/v1/tts/jobs/{job_id}`, `/v1/tts/jobs?tag=` | orchestrator | cancel one job / every job with a tag |
| PUT / GET / DELETE | `/v1/admission/live` | orchestrator | LIVE lease (renew about every ttl/2) and GPU readiness for LIVE |

Errors are `{"error": {"code", "message"}}`; codes and lifecycle in docs/TTS_SERVER.md section 16.

```powershell
curl.exe -X PUT http://127.0.0.1:8780/v1/voices/shop_main -H "Authorization: Bearer <token>" -F "wav=@C:\path\voice.wav"
curl.exe -X POST http://127.0.0.1:8780/v1/tts/jobs -H "Authorization: Bearer <token>" -H "Idempotency-Key: utt-123" `
  -H "Content-Type: application/json" -d '{\"voice_id\":\"shop_main\",\"text\":\"Dạ có size Mờ nha chị.\",\"priority\":\"live\",\"reader_agent_id\":\"agent-1\"}'
curl.exe "http://127.0.0.1:8780/v1/tts/jobs/<job_id>/audio?wait=20" -H "Authorization: Bearer <read_token>" `
  -H "X-Sarae-Agent-Id: agent-1" -o out.wav
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q                                    # FakeEngine, no GPU
$env:SARAE_VOICE_PROCESS_TESTS = "1"; .venv\Scripts\python.exe -m pytest tests\test_supervisor.py -v
$env:SARAE_VOICE_GPU_TESTS = "1"; $env:SARAE_VOICE_TEST_REF = "C:\path\voice_ref.wav"
.venv\Scripts\python.exe -m pytest tests\test_gpu_integration.py tests\test_gpu_jobs.py -v -s   # close other CUDA apps first
```

## Benchmarks (`bench/`)

| Script | What |
|---|---|
| `run_library.py`, `run_demo.py`, `analyze.py` | Phase 0 A/B against the official demo on 7862 |
| `run_perf.py`, `recheck_asr.py` | Phase 0 single-process performance, soak, second-ASR recheck |
| `run_http.py` | Phase 1: startup, prompt cache, HTTP latency, Phase 0 identity |
| `run_jobs.py` | Phase 2: 30-job burst, live under prepare load, long job running when LIVE starts |

Runs go to `bench\runs\` (ignored). Committed snapshots without audio: `bench\results\`.
