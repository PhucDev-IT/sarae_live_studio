"""Sample GPU/CPU/RAM once per interval into a CSV while an engine runs (Phase 0.4 / 0.7).

    uv run python tools/monitor.py --out outputs/phase0/bench/lt_w2l256.csv [--pid PID] [--interval 1]

Records the baseline VRAM before the engine starts (first rows) so shared use with OBS or the TTS
server can be subtracted. Stop with Ctrl+C; a summary (peak VRAM, mean/peak util, RSS slope) is printed
and written next to the CSV as .summary.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import psutil
import pynvml


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pid", type=int, help="engine process; its RSS and CPU are recorded")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--gpu", type=int, default=0)
    args = ap.parse_args(argv)

    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(args.gpu)
    proc = psutil.Process(args.pid) if args.pid else None
    if proc:
        proc.cpu_percent(None)
    psutil.cpu_percent(None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    t0 = time.time()
    fields = ["t", "vram_used_mb", "gpu_util", "mem_util", "gpu_temp", "gpu_power_w",
              "cpu_total", "ram_used_mb", "proc_cpu", "proc_rss_mb"]
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        try:
            while True:
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                row = {
                    "t": round(time.time() - t0, 2),
                    "vram_used_mb": round(mem.used / 2**20),
                    "gpu_util": util.gpu,
                    "mem_util": util.memory,
                    "gpu_temp": pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU),
                    "gpu_power_w": round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000, 1),
                    "cpu_total": psutil.cpu_percent(None),
                    "ram_used_mb": round(psutil.virtual_memory().used / 2**20),
                    "proc_cpu": "",
                    "proc_rss_mb": "",
                }
                if proc:
                    try:
                        row["proc_cpu"] = proc.cpu_percent(None)
                        row["proc_rss_mb"] = round(sum(p.memory_info().rss for p in [proc, *proc.children(True)]) / 2**20)
                    except psutil.NoSuchProcess:
                        break
                wr.writerow(row)
                fh.flush()
                rows.append(row)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass

    if not rows:
        return 1
    rss = [(r["t"], r["proc_rss_mb"]) for r in rows if r["proc_rss_mb"] != ""]
    slope = None
    if len(rss) > 10:
        ts, vs = zip(*rss)
        n = len(ts)
        mt, mv = sum(ts) / n, sum(vs) / n
        slope = sum((a - mt) * (b - mv) for a, b in rss) / (sum((a - mt) ** 2 for a in ts) or 1) * 60
    summary = {
        "seconds": rows[-1]["t"],
        "vram_baseline_mb": rows[0]["vram_used_mb"],
        "vram_peak_mb": max(r["vram_used_mb"] for r in rows),
        "gpu_util_mean": round(sum(r["gpu_util"] for r in rows) / len(rows), 1),
        "gpu_util_peak": max(r["gpu_util"] for r in rows),
        "cpu_total_mean": round(sum(r["cpu_total"] for r in rows) / len(rows), 1),
        "proc_rss_peak_mb": max((v for _, v in rss), default=None),
        "proc_rss_slope_mb_per_min": round(slope, 2) if slope is not None else None,
    }
    args.out.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
