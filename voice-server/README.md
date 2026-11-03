# Sarae Voice Server (G-OmniVoice)

Independent TTS service for Sarae Live Studio. Design: [docs/TTS_SERVER.md](../docs/TTS_SERVER.md).

**Status: Phase 0 (baseline benchmark) only.** There is no service code yet; nothing in `backend/`, `web/` or
`agent-core/` uses this folder.

## Setup (Windows, NVIDIA GPU)

```powershell
cd voice-server
.\scripts\install.ps1      # .venv with Python 3.12, torch 2.8.0+cu128, omnivoice==0.2.1 (same as the lab baseline)
```

Model weights stay in the Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub` unless `HF_HOME` is set) and
are never committed. Checkpoint: `g-group-ai-lab/g-omnivoice`.

## Phase 0 — baseline against the official demo

The official demo must already be running on `127.0.0.1:7862` with the same checkpoint
(`g_omnivoice_lab\run_official.ps1`). No TikTok LIVE, OBS or Sarae component is involved.

```powershell
$env:PYTHONIOENCODING = "utf-8"
$run = "p0_" + (Get-Date -Format yyyyMMdd_HHmm)
.venv\Scripts\python.exe bench\run_library.py --ref C:\path\voice_ref_pause_7s_test.wav --cut-ref C:\path\voice_ref.wav --run-name $run
.venv\Scripts\python.exe bench\run_demo.py    --ref C:\path\voice_ref_pause_7s_test.wav --run-name $run
.venv\Scripts\python.exe bench\analyze.py     --run-name $run
```

| Arm | What it is |
|---|---|
| `D-ASR` | Official demo, Reference Text empty (Whisper transcribes) — the baseline you listened to |
| `S-ASR` | Library called exactly like the demo (same load, same `OmniVoiceGenerationConfig`, `ref_text=None`), prompt made once and reused |
| `S-ASRTXT` | Prompt rebuilt from the stored ASR transcript (the service's re-encode path); must equal `S-ASR` |
| `R-CUT` | `S-ASR` with the reference whose end was cut (reproduces the first-word loss case) |
| `S-MAN` / `D-MAN` | Optional, with `--manual-transcript-file` once a checked transcript exists |

Outputs go to `bench\runs\<run>\` (ignored by Git): byte-for-byte copies of the references with SHA-256 and a QC
report, every generated WAV (int16, 24 kHz, exactly as the demo returns it), saved prompts (`.pt`), timings and
VRAM per call, Whisper transcripts, `metrics.csv`, `summary.md` and a blind listening sheet (`listening\sheet.csv`,
arm names in `listening\key.json` — open the key only after scoring).

Unit tests for the measurements: `.venv\Scripts\python.exe -m pytest tests -q`.
