"""Phase 1 measurement through real HTTP: spawns the service (`python -m sarae_voice`), then measures

  startup     process spawn -> /health/ready 200 (checkpoint cached), twice (second = restart)
  voice       PUT /v1/voices (Whisper, like the demo) and warmup after the restart (prompt from .pt)
  latency     client wall time per POST /v1/tts/synthesize vs the server's own X-Server-Ms / X-Inference-Ms
  baseline    seeded golden-set outputs compared sample for sample with the Phase 0 library WAVs (when present)
  vram        /v1/status memory at the end

    .venv\\Scripts\\python.exe bench\\run_http.py --ref C:\\path\\voice_ref_pause_7s_test.wav --run-name <run>

Rows go to <run>/http.jsonl (arm "HTTP") so bench/analyze.py can run the Whisper head/tail check on them.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

from common import BENCH, append_jsonl, golden, keep_reference, new_run, seed_for

PHASE0_RUN = BENCH / "runs" / "p0_20261102_2251"


def spawn(env: dict, log_path: Path) -> subprocess.Popen:
    log = log_path.open("ab")
    return subprocess.Popen([sys.executable, "-m", "sarae_voice"], env=env, stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(BENCH.parent))


def wait_ready(base: str, proc: subprocess.Popen, timeout: float = 600) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if proc.poll() is not None:
            raise SystemExit(f"server exited with {proc.returncode}")
        try:
            r = httpx.get(f"{base}/health/ready", timeout=2)
            if r.status_code == 200:
                return time.perf_counter() - started
            if r.json().get("state") == "error":
                raise SystemExit(f"server not ready: {r.json()}")
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise SystemExit("server did not become ready")


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def pct(values, q):
    values = sorted(values)
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 1)


def describe(values):
    return {"n": len(values), "median": pct(values, 0.5), "p95": pct(values, 0.95), "max": round(max(values), 1)} \
        if values else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--run-name")
    ap.add_argument("--port", type=int, default=8781)
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()

    run = new_run(args.run_name)
    keep_reference(run, "main", args.ref)
    token = secrets.token_urlsafe(24)
    env = {**os.environ, "SARAE_VOICE_ORCHESTRATOR_TOKENS": token, "SARAE_VOICE_PORT": str(args.port),
           "SARAE_VOICE_DATA_DIR": str(run / "server-data"), "SARAE_VOICE_ENGINE": "omnivoice",
           "PYTHONIOENCODING": "utf-8"}
    base = f"http://127.0.0.1:{args.port}"
    auth = {"Authorization": f"Bearer {token}"}
    report: dict = {}
    rows_path = run / "http.jsonl"

    proc = spawn(env, run / "server.log")
    report["startup_cold_process_s"] = round(wait_ready(base, proc), 2)
    client = httpx.Client(base_url=base, headers=auth, timeout=300)
    report["status_after_start"] = client.get("/v1/status").json()

    started = time.perf_counter()
    r = client.put("/v1/voices/shop_main", files={"wav": ("ref.wav", args.ref.read_bytes(), "audio/wav")})
    r.raise_for_status()
    report["register_voice"] = {"client_ms": round((time.perf_counter() - started) * 1000, 1),
                                "prompt": r.json()["prompt"], "transcript": r.json()["transcript"],
                                "qc_warnings": r.json()["qc"]["warnings"]}
    started = time.perf_counter()
    report["warmup_hot"] = {**client.post("/v1/voices/shop_main/warmup").json(),
                            "client_ms": round((time.perf_counter() - started) * 1000, 1)}

    def call(text: str, seed: int | None):
        t0 = time.perf_counter()
        resp = client.post("/v1/tts/synthesize", json={"voice_id": "shop_main", "text": text, "seed": seed})
        client_ms = (time.perf_counter() - t0) * 1000
        resp.raise_for_status()
        h = resp.headers
        return resp.content, {"client_ms": round(client_ms, 1), "server_ms": float(h["X-Server-Ms"]),
                              "inference_ms": float(h["X-Inference-Ms"]), "queue_ms": float(h["X-Queue-Ms"]),
                              "audio_ms": float(h["X-Audio-Ms"]), "prompt_cache": h["X-Prompt-Cache"],
                              "bytes": len(resp.content),
                              "http_overhead_ms": round(client_ms - float(h["X-Server-Ms"]), 1)}

    identical = []
    for item in golden():
        if item["category"] == "long":
            continue
        for rep in range(args.reps):
            content, stats = call(item["text"], seed_for(item["id"], rep))
            wav = run / "HTTP" / f"{item['id']}_r{rep}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(content)
            base_wav = PHASE0_RUN / "S-ASR" / f"{item['id']}_r{rep}.wav"
            if base_wav.is_file():
                a, _ = sf.read(wav, dtype="int16")
                b, _ = sf.read(base_wav, dtype="int16")
                same = a.shape == b.shape and int(np.max(np.abs(a.astype(int) - b.astype(int)))) == 0
                identical.append(same)
                stats["same_as_phase0"] = same
            append_jsonl(rows_path, {"arm": "HTTP", "item": item["id"], "category": item["category"], "rep": rep,
                                     "wav": str(wav.relative_to(run)), **stats})
            print(f"[golden] {item['id']} r{rep} client {stats['client_ms']} ms (server {stats['server_ms']}, "
                  f"inference {stats['inference_ms']}) phase0-identical={stats.get('same_as_phase0')}", flush=True)

    perf = json.loads((BENCH / "perf_texts.json").read_text(encoding="utf-8"))["buckets"]
    for bucket in ("live_short", "live_typical", "live_max"):
        for t, text in enumerate(perf[bucket]["texts"]):
            for rep in range(args.reps):
                _, stats = call(text, None)       # unseeded, like the demo
                append_jsonl(rows_path, {"arm": "HTTP-perf", "bucket": bucket, "text": t, "rep": rep,
                                         "chars": len(text), **stats})
        print(f"[{bucket}] done", flush=True)
    report["status_end_first_process"] = client.get("/v1/status").json()
    client.close()

    # --- restart: prompt must come back from the .pt file ------------------------------------------------
    stop(proc)
    proc = spawn(env, run / "server.log")
    report["startup_restart_s"] = round(wait_ready(base, proc), 2)
    client = httpx.Client(base_url=base, headers=auth, timeout=300)
    started = time.perf_counter()
    report["warmup_after_restart"] = {**client.post("/v1/voices/shop_main/warmup").json(),
                                      "client_ms": round((time.perf_counter() - started) * 1000, 1)}
    _, first = call(golden()[0]["text"], seed_for(golden()[0]["id"], 0))
    report["first_request_after_restart"] = first
    report["status_after_restart"] = client.get("/v1/status").json()
    client.close()
    stop(proc)

    rows = [json.loads(l) for l in rows_path.read_text(encoding="utf-8").splitlines()]
    summary = {"golden_identical_to_phase0": f"{sum(identical)}/{len(identical)}" if identical else "n/a"}
    for name, rs in (("golden", [r for r in rows if r["arm"] == "HTTP"]),
                     *((b, [r for r in rows if r.get("bucket") == b]) for b in ("live_short", "live_typical", "live_max"))):
        summary[name] = {k: describe([r[k] for r in rs]) for k in ("client_ms", "server_ms", "inference_ms",
                                                                    "http_overhead_ms", "audio_ms")}
    report["summary"] = summary
    (run / "http_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("startup_cold_process_s", "startup_restart_s", "register_voice",
                                             "warmup_hot", "warmup_after_restart", "first_request_after_restart",
                                             "summary")}, ensure_ascii=False, indent=2))
    print("memory:", report["status_after_restart"]["memory"], report["status_end_first_process"]["memory"])
    print(f"run folder: {run}")


if __name__ == "__main__":
    main()
