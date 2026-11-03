"""Phase 2 benchmark over real HTTP: many jobs waiting on one GPU lane.

    .venv\\Scripts\\python.exe bench\\run_jobs.py --ref C:\\path\\voice_ref_pause_7s_test.wav --run-name <run>

Spawns `python -m sarae_voice` (real model), registers the voice, then:
  A  burst     30 jobs at once (10 live, 10 preview, 10 prepare, typical LIVE texts): queue wait per priority,
               inference, HTTP create and audio download (read token) latency
  B  live under load   40 prepare jobs queued, one live job every 3 s (10): live queue wait while prepare drains
  C  long before live  a 2-minute long job starts, then a LIVE lease and a live job: what the live job really waits,
               next to the lease's estimate
Every job has a deadline long enough to finish (the expiry behaviour is covered by the tests).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from common import BENCH, keep_reference, new_run
from run_http import spawn, stop, wait_ready


def pct(values, q):
    values = sorted(values)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def describe(values):
    return {"n": len(values), "p50": pct(values, 0.5), "p95": pct(values, 0.95), "max": round(max(values), 1)} if values else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--run-name")
    ap.add_argument("--port", type=int, default=8782)
    ap.add_argument("--scenarios", default="ABC")
    args = ap.parse_args()
    run = new_run(args.run_name)
    keep_reference(run, "main", args.ref)
    token = secrets.token_urlsafe(24)
    env = {**os.environ, "SARAE_VOICE_ORCHESTRATOR_TOKENS": token, "SARAE_VOICE_PORT": str(args.port),
           "SARAE_VOICE_DATA_DIR": str(run / "server-data"), "SARAE_VOICE_ENGINE": "omnivoice",
           "SARAE_VOICE_QUEUE_LIVE": "20", "SARAE_VOICE_QUEUE_PREVIEW": "20", "SARAE_VOICE_QUEUE_PREPARE": "60",
           "PYTHONIOENCODING": "utf-8"}
    base = f"http://127.0.0.1:{args.port}"
    texts = json.loads((BENCH / "perf_texts.json").read_text(encoding="utf-8"))["buckets"]
    typical = texts["live_typical"]["texts"] + texts["live_short"]["texts"]
    proc = spawn(env, run / "server.log")
    report: dict = {"startup_s": round(wait_ready(base, proc), 1)}
    c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    c.put("/v1/voices/shop_main", files={"wav": ("ref.wav", args.ref.read_bytes(), "audio/wav")}).raise_for_status()
    late = lambda s=600: (datetime.now(timezone.utc) + timedelta(seconds=s)).isoformat()   # noqa: E731

    def create(text, priority, i):
        t0 = time.perf_counter()
        r = c.post("/v1/tts/jobs", json={"voice_id": "shop_main", "text": text, "priority": priority,
                                         "reader_agent_id": "agent-1", "deadline_at": late(), "seed": i})
        r.raise_for_status()
        return r.json(), (time.perf_counter() - t0) * 1000

    def finish(body):
        while True:
            v = c.get(f"/v1/tts/jobs/{body['job_id']}", params={"wait": 10}).json()
            if v["state"] in ("done", "failed", "cancelled", "expired"):
                return v

    def download(body):
        t0 = time.perf_counter()
        r = httpx.get(f"{base}/v1/tts/jobs/{body['job_id']}/audio", timeout=60,
                      headers={"Authorization": f"Bearer {body['read_token']}", "X-Sarae-Agent-Id": "agent-1"})
        r.raise_for_status()
        return (time.perf_counter() - t0) * 1000, len(r.content)

    rows = []

    def keep(scenario, views):
        for v in views:
            rows.append({"scenario": scenario, "job_id": v["job_id"], "priority": v["priority"], "chars": v["chars"],
                         "state": v["state"], "source": v["source"], "created_at": v["created_at"],
                         "started_at": v["started_at"], "finished_at": v["finished_at"], **v["timings"]})

    # --- A: burst ----------------------------------------------------------------------------------------
    if "A" not in args.scenarios:
        return run_bc(args, c, create, finish, download, texts, typical, report, rows, keep, proc, run)
    t0 = time.perf_counter()
    created = [create(typical[i % len(typical)], ("live", "preview", "prepare")[i % 3], 1000 + i) for i in range(30)]
    finals = [finish(b) for b, _ in created]
    wall = time.perf_counter() - t0
    downloads = [download(b) for b, _ in created]
    a = {"wall_s": round(wall, 1), "states": [f["state"] for f in finals].count("done"),
         "create_ms": describe([ms for _, ms in created]),
         "download_ms": describe([ms for ms, _ in downloads]), "download_bytes_p50": pct([n for _, n in downloads], 0.5),
         "inference_ms": describe([f["timings"]["inference_ms"] for f in finals])}
    for prio in ("live", "preview", "prepare"):
        a[f"queue_ms_{prio}"] = describe([f["timings"]["queue_ms"] for f in finals if f["priority"] == prio])
    report["A_burst_30"] = a
    keep("A", finals)
    print("A", json.dumps(a), flush=True)
    return run_bc(args, c, create, finish, download, texts, typical, report, rows, keep, proc, run)


def run_bc(args, c, create, finish, download, texts, typical, report, rows, keep, proc, run):
    if "B" not in args.scenarios:
        return run_c(args, c, create, finish, texts, report, rows, keep, proc, run)

    # --- B: live while prepare drains ---------------------------------------------------------------------
    prep = [create(texts["live_typical"]["texts"][i % 6] + f" Câu {i}.", "prepare", 2000 + i)[0] for i in range(40)]
    live = []
    with ThreadPoolExecutor(4) as pool:
        for i in range(10):
            live.append(pool.submit(lambda i=i: finish(create(typical[i % len(typical)] + " Dạ.", "live", 3000 + i)[0])))
            time.sleep(3)
        live_finals = [f.result() for f in live]
    prep_finals = [finish(b) for b in prep]
    b = {"live_queue_ms": describe([f["timings"]["queue_ms"] for f in live_finals]),
         "live_inference_ms": describe([f["timings"]["inference_ms"] for f in live_finals]),
         "prepare_queue_ms": describe([f["timings"]["queue_ms"] for f in prep_finals]),
         "fair_turns": c.get("/v1/status").json()["gate"]["fair_turns"],
         "states": {s: [f["state"] for f in live_finals + prep_finals].count(s) for s in ("done", "expired", "failed")}}
    report["B_live_under_prepare_load"] = b
    keep("B", live_finals + prep_finals)
    print("B", json.dumps(b), flush=True)
    return run_c(args, c, create, finish, texts, report, rows, keep, proc, run)


def run_c(args, c, create, finish, texts, report, rows, keep, proc, run):
    if "C" not in args.scenarios:
        return end(c, report, rows, proc, run)

    # --- C: long job running when LIVE starts ---------------------------------------------------------------
    long_body, _ = create(texts["long_3min"]["texts"][0], "long", 4000)
    while c.get(f"/v1/tts/jobs/{long_body['job_id']}").json()["state"] != "running":
        time.sleep(0.1)
    time.sleep(3)
    lease = c.put("/v1/admission/live", json={"owner_ref": "o", "session_id": "s", "ttl_s": 120}).json()
    t0 = time.perf_counter()
    live_body, _ = create("Dạ có size Mờ nha chị.", "live", 4001)
    live_final = finish(live_body)
    waited = time.perf_counter() - t0
    rejected = c.post("/v1/tts/jobs", json={"voice_id": "shop_main", "text": "đoạn dài " * 60, "priority": "long"})
    long_final = finish(long_body)
    c.delete("/v1/admission/live", params={"owner_ref": "o", "session_id": "s"})
    report["C_long_before_live"] = {
        "lease_ready_for_live": lease["ready_for_live"], "lease_reason": lease["reason"],
        "estimated_remaining_s": lease["gpu"]["running"]["estimated_remaining_s"],
        "live_waited_s": round(waited, 1), "live_queue_ms": live_final["timings"]["queue_ms"],
        "long_audio_s": round(long_final["audio"]["audio_ms"] / 1000, 1),
        "long_inference_s": round(long_final["timings"]["inference_ms"] / 1000, 1),
        "new_long_during_live": rejected.status_code}
    keep("C", [live_final, long_final])
    print("C", json.dumps(report["C_long_before_live"]), flush=True)
    return end(c, report, rows, proc, run)


def end(c, report, rows, proc, run):
    status = c.get("/v1/status").json()
    report["status_end"] = {k: status[k] for k in ("memory", "gate", "jobs", "prompt_cache", "result_cache", "latency",
                                                   "generated_not_consumed", "discarded_after_inference")}
    report["metrics_sample"] = [l for l in c.get("/metrics").text.splitlines() if l.startswith(("tts_jobs_created", "tts_jobs_finished"))]
    c.close()
    stop(proc)
    (run / "jobs_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (run / "jobs_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("memory", report["status_end"]["memory"])
    print(f"run folder: {run}")


if __name__ == "__main__":
    main()
