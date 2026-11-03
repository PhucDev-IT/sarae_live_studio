# Phase 1 results (snapshot)

No audio. Source runs live in `bench/runs/` on the GPU machine.

| File | Source | Contents |
|---|---|---|
| `http_report.json` | `p1_http_20261103_0832` (`bench/run_http.py`) | process start → ready (first start and restart), voice registration with Whisper, warmup from RAM and after restart from `.pt`, `/v1/status` snapshots (VRAM, cache counters), latency summary |
| `http_rows.jsonl` | same | every request: client wall time, server time, inference, queue, HTTP overhead, audio length, identity with Phase 0 |
| `summary.md`, `metrics.csv` | `bench/analyze.py` on the same run | Whisper head/tail check of the 45 golden outputs served over HTTP |
| `perf_recheck_same_session.json` | `bench/run_perf.py --buckets live_short,live_typical` | library called directly, same session, to compare with the service |

Interpretation: `docs/TTS_SERVER.md` section 15.
