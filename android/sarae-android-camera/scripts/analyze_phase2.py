"""Audit captured loader and independent consumer evidence, without interpreting a second preview as a hook."""
import collections
import json
import re
import statistics
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "test-results" / "phase2"
loader = [json.loads(line) for line in (root / "loader-empty.jsonl").read_text(encoding="utf-8-sig").splitlines()]
hook = (root / "consumer-hook-logcat.txt").read_text(encoding="utf-8-sig")
physical = (root / "consumer-physical-logcat.txt").read_text(encoding="utf-8-sig")
lines = hook.splitlines()
switches = []
for index, line in enumerate(lines):
    if "event=switch " in line:
        source = re.search(r"source=([AB])", line).group(1)
        following = lines[index + 1:]
        end = next((i for i, item in enumerate(following) if "event=switch " in item), len(following))
        observed = any(f"pattern={source} " in item for item in following[:end])
        switches.append({"source": source, "observed_by_consumer": observed})
frames = [line for line in lines if "event=frame " in line]
gaps = [int(re.search(r"gapNs=(\d+)", line).group(1)) for line in frames]
timestamps = [int(re.search(r"timestampNs=(\d+)", line).group(1)) for line in frames]
times = [int(re.search(r"ns=(\d+)", line).group(1)) for line in frames]
release_count = sum("event=frame_rendered " in line for line in lines)
patterns = collections.Counter(re.findall(r"pattern=(\w+)", hook))
errors = [line for line in lines if any(term in line for term in (
    "FATAL EXCEPTION", "Fatal signal", "event=decoder_error", "event=error ", "Unexpected session recreation"))]
loader_errors = [row for row in loader if row["kind"] in ("decoder_error", "camera2_error", "crash")]
loader_passes = sum(row["kind"] == "switch_continuity" and "result=PASS" in row["detail"] for row in loader)
loader_duration = (loader[-1]["wallTimeMs"] - loader[0]["wallTimeMs"]) / 1000
crash_text = "\n".join((root / name).read_text(encoding="utf-8-sig") for name in
    ("loader-empty-crash.txt", "crash-buffer.txt", "native-java-diagnostics.txt"))
crash_free = not re.search(r"FATAL EXCEPTION|Fatal signal|SIGSEGV|SIGABRT", crash_text)
duration = (times[-1] - times[0]) / 1e9 if len(times) > 1 else 0
sessions = hook.count("event=session_started ")
result = {
    "loader": {"duration_seconds": loader_duration, "switch_pass": loader_passes,
               "errors": loader_errors, "pass": loader_duration >= 120 and loader_passes >= 30 and not loader_errors
               and sum(row["kind"] == "camera2_session_started" for row in loader) == 1
               and not any(row["kind"] == "switch_continuity" and "result=FAIL" in row["detail"] for row in loader)
               and crash_free},
    "bridge": {"duration_seconds": round(duration, 3), "consumer_frames": len(frames),
               "consumer_fps": round((len(frames)-1)/duration, 3) if duration else 0,
               "median_frame_gap_ms": round(statistics.median(gaps)/1e6, 3) if gaps else None,
               "max_frame_gap_ms": round(max(gaps)/1e6, 3) if gaps else None,
               "gaps_over_100ms": sum(gap > 100_000_000 for gap in gaps),
               "estimated_missing_30fps_slots": sum(max(0, round(gap/(1e9/30))-1) for gap in gaps),
               "decoder_released_frames": release_count,
               "release_minus_surface_updates": release_count-len(frames),
               "timestamp_regressions": sum(b < a for a,b in zip(timestamps,timestamps[1:])),
               "sessions": sessions, "patterns": dict(patterns), "switches": switches,
               "errors": errors,
               "physical_baseline_patterns": dict(collections.Counter(re.findall(r"pattern=(\w+)", physical)))},
    "measurement_limit": "Missing 30fps slots estimate includes decoder restart gaps; release/update delta includes capture boundaries and SurfaceTexture coalescing. Not an exact HAL drop counter. PTS resets at loops/switches.",
}
result["bridge"]["pass"] = (len(switches) >= 12 and all(item["observed_by_consumer"] for item in switches)
    and sessions == 1 and patterns["A"] > 0 and patterns["B"] > 0 and not errors and duration >= 120
    and hook.count("event=session_redirect ") == 1 and hook.count("event=target_redirect ") == 1
    and "PHYSICAL_OR_UNKNOWN" in physical and not re.search(r"pattern=[AB] ", physical)
    and crash_free)
print(json.dumps(result, indent=2))
sys.exit(0 if result["loader"]["pass"] and result["bridge"]["pass"] else 1)
