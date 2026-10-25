"""Summarize the one valid Phase 22 MIC run and its profiler window."""
import json
from pathlib import Path
import statistics

root = Path(__file__).resolve().parents[1]
state = json.loads((root / "test-results/phase22/state.json").read_text())
run = state["run"]
events = [json.loads(s) for s in (root / ".tools/phase22" / run / "harness/events.jsonl").read_text().splitlines()]
begin = int(float(state["profileStartUptime"]) * 1_000_000_000)
end = int(float(state["profileEndUptime"]) * 1_000_000_000)


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


mic = [e for e in events if e["event"] == "buffer_complete" and e["mode"] == "MICROPHONE"]
windows = {"before": [], "during": [], "after": []}
for event in mic:
    tick = event["callbackNs"]
    windows["before" if tick < begin else "during" if tick <= end else "after"].append(event)


def stats(rows):
    times = [e["callbackNs"] for e in rows]
    gaps_ms = [(b - a) / 1e6 for a, b in zip(times, times[1:])]
    work_us = [e["callbackWorkNs"] / 1e3 for e in rows]
    return {"callbacks": len(rows), "callbackGapMedianMs": statistics.median(gaps_ms) if gaps_ms else None,
            "callbackGapP99Ms": percentile(gaps_ms, .99), "callbackGapMaxMs": max(gaps_ms) if gaps_ms else None,
            "callbackWorkMedianUs": statistics.median(work_us) if work_us else None,
            "callbackWorkP99Us": percentile(work_us, .99),
            "callbackWorkMaxUs": max(work_us) if work_us else None,
            "missingFrames": sum(e["missingFrames"] for e in rows),
            "enqueueFailures": sum(e["enqueueResult"] != 0 for e in rows)}


record = [e for e in events if e["event"] == "record_state" and e["mode"] == "MICROPHONE"]
result = {"run": run, "sampleWindowUptimeSeconds": [state["profileStartUptime"], state["profileEndUptime"]],
          "micStateTimesNs": [{"state": e["state"], "time": e["emissionNs"]} for e in record],
          "profileFullyInsideMic": record[0]["emissionNs"] < begin < end < record[1]["emissionNs"],
          "allMic": stats(mic), "windows": {key: stats(rows) for key, rows in windows.items()},
          "suiteComplete": any(e["event"] == "suite_complete" for e in events),
          "suiteErrors": [e for e in events if e["event"] == "suite_error"]}
(root / "test-results/phase22/analysis.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
