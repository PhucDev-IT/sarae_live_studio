# Phase 0 results (snapshot)

Copied from `bench/runs/` (ignored by Git) so the baseline stays traceable. No audio, no prompt `.pt` and no
reference WAV are included: the generated WAVs and the private reference voice stay on the GPU machine.

| Folder | Source run | Contents |
|---|---|---|
| `quality/` | `p0_20261102_2251` | A/B against the official demo on 7862: `summary.md`, per-file `metrics.csv` (levels, Whisper text, head/tail check), library environment and integrity checks, prompt metadata, reference QC, demo upload check, second-ASR recheck (`recheck.md`/`recheck.csv`) |
| `perf/` | `perf_20261102_2336` | Single-process performance: `perf_report.json` (summary, startup, prompt, soak, GPU snapshots) and every measured request (`perf_rows.json`) |

Reference: `voice_ref_pause_7s_test.wav`, sha256 `25b48ad4888ccd7dbd4e072095318b124e6ae8c5f9d63e63fe90e7071ecc2552`.
Checkpoint `g-group-ai-lab/g-omnivoice` @ `f2b73211e57db389d81ab817987f90c02e0898ba`, omnivoice 0.2.1,
torch 2.8.0+cu128, transformers 5.14.1. Interpretation: `docs/TTS_SERVER.md` section 14.
