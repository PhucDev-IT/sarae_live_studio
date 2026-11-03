# Phase 2 results (snapshot)

No audio, no tokens. Source runs in `bench/runs/` on the GPU machine (`bench/run_jobs.py`, real model over HTTP).

| File | Source | Contents |
|---|---|---|
| `jobs_report_first_run.json` | `p2_jobs_20261103_0923` | scenarios A (30-job burst), B (live every 3 s while 40 prepare jobs drain), C (2-minute long job running when a LIVE lease starts); `/v1/status` and metrics samples at the end |
| `jobs_report_burst_rerun.json`, `jobs_rows_burst_rerun.json` | `p2_jobsA_*` | scenario A alone, every job's priority, queue wait, inference and start time |
| `nvidia_smi_burst_rerun.csv` | same | GPU utilisation, memory, SM clock, temperature, power every second during the rerun |

The first run's scenario A had a few 25–37 s inference calls that the isolated rerun did not reproduce (all calls
1.8–2.7 s); see docs/TTS_SERVER.md section 16.8.
