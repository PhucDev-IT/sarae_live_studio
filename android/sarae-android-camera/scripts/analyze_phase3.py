"""Fixed Phase 3 thresholds. Exit 1 on any missed acceptance criterion; no threshold tuning."""
import argparse
import gzip
import json
import re
import statistics
from pathlib import Path

def read(path):
    return [json.loads(line) for line in text(path).splitlines() if line.strip()]

def text(path):
    path=Path(path)
    if path.exists():
        return path.read_text(encoding="utf-8-sig")
    with gzip.open(str(path)+".gz","rt",encoding="utf-8-sig") as stream:
        return stream.read()

def number(detail, key):
    match = re.search(rf"(?:^|\s){re.escape(key)}=(-?\d+)", detail)
    return int(match[1]) if match else None

def analyze(directory):
    directory = Path(directory)
    bridge = read(directory / "bridge.jsonl")
    consumer = read(directory / "consumer.jsonl")
    outputs = [x for x in bridge if x["kind"] == "output_present"]
    frames = [x for x in consumer if x["kind"] == "frame"]
    assert outputs and frames, "No rendered/received frames"
    start, end = frames[0]["ns"], frames[-1]["ns"]
    active = [x for x in bridge if start <= x["ns"] <= end]
    requests = [x for x in bridge if x["kind"] == "switch_request"]
    completes = [x for x in bridge if x["kind"] == "switch_complete" and x.get("switchId", 0) > 0]
    switch_results = []
    for request in requests:
        ident = number(request["detail"], "id")
        done = next((x for x in completes if x["switchId"] == ident), None)
        mode = "after" if "mode=after" in request["detail"] else "now"
        pattern = "A" if "next=file:///storage/emulated/0/Android/data/co.sarae.camera.consumer/files/A.mp4" in request["detail"] else "B"
        observed = next((x for x in frames if done and done["ns"] <= x["ns"] <= done["ns"] + 1_000_000_000
                         and f"pattern={pattern} " in x["detail"]), None)
        eos = mode != "after" or bool(done and any(x["kind"] == "loop" and request["ns"] <= x["ns"] <= done["ns"] for x in bridge))
        switch_results.append({"id": ident, "mode": mode, "source": pattern, "completed": bool(done),
                               "correct_pixel_observed": bool(observed), "after_eos_respected": eos,
                               "prepare_and_wait_ms": round((done["ns"]-request["ns"])/1e6, 3) if done else None,
                               "consumer_observation_after_commit_ms": round((observed["ns"]-done["ns"])/1e6, 3) if observed else None})
    consumer_pts = [number(x["detail"], "timestampNs") for x in frames]
    renderer_pts = [x["outputPtsNs"] for x in outputs]
    consumer_nonmonotonic = sum(b <= a for a, b in zip(consumer_pts, consumer_pts[1:]))
    renderer_nonmonotonic = sum(b <= a for a, b in zip(renderer_pts, renderer_pts[1:]))
    gaps = [number(x["detail"], "gapNs") for x in frames[1:]]
    renderer_gaps = [number(x["detail"], "gapNs") for x in outputs[1:]]
    black = sum("black=true" in x["detail"] for x in frames)
    unsampled = sum(max(0,b["frames"]-a["frames"]-1) for a,b in zip(frames,frames[1:]))
    unknown = sum("pattern=PHYSICAL_OR_UNKNOWN" in x["detail"] for x in frames)
    errors = [x for x in active if x["kind"] in ("pipeline_error", "decoder_error", "preload_failed", "switch_rejected")]
    sessions = [x for x in consumer if x["kind"] == "session_started"]
    native_text = "\n".join(text(directory / name) for name in
                            ("crash-buffer.txt", "native-java-diagnostics.txt", "pid-logcat.txt"))
    fatal = re.findall(r"^.*(?:FATAL EXCEPTION|Fatal signal).*$", native_text, re.MULTILINE)
    duration = (end-start)/1e9
    correct = sum(x["completed"] and x["correct_pixel_observed"] and x["after_eos_respected"] for x in switch_results)
    egl_create = [x for x in bridge if x["kind"] == "egl_created"]
    criteria = {
        "strictly_monotonic_consumer_and_renderer_timestamps": consumer_nonmonotonic == renderer_nonmonotonic == 0,
        "one_consumer_camera_session": len(sessions) == 1 and all(x["generation"] == 1 for x in frames),
        "one_output_surface_and_egl_lifecycle": len(egl_create) == 1 and sum(x["kind"] == "session_redirect" for x in bridge) == 1,
        "100_of_100_correct_switches": len(requests) == len(completes) == correct == 100,
        "both_switch_modes": {x["mode"] for x in switch_results} == {"now", "after"},
        "zero_black_or_unknown_received_frames": black == unknown == 0,
        "pixel_samples_cover_every_consumer_callback": unsampled == 0,
        "max_consumer_and_renderer_frame_gap_below_100ms": max(gaps) < 100_000_000 and max(renderer_gaps) < 100_000_000,
        "30_minutes_continuous_received_frames": duration >= 1800,
        "no_observed_java_native_or_pipeline_failure": not errors and not fatal,
        "loop_observed": any(x["kind"] == "loop" for x in active),
        "egl_and_decoder_resources_released": sum(x["kind"] == "egl_released" for x in bridge) == 1
            and sum(x["kind"] == "decoder_started" for x in bridge) == sum(x["kind"] == "decoder_released" for x in bridge),
    }
    return {
        "result": "PASS" if all(criteria.values()) else "FAIL", "criteria": criteria,
        "duration_seconds": round(duration, 3), "consumer_frames": len(frames), "renderer_frames": len(outputs),
        "consumer_fps": round((len(frames)-1)/duration, 4),
        "consumer_timestamp_non_increasing": consumer_nonmonotonic, "renderer_timestamp_non_increasing": renderer_nonmonotonic,
        "consumer_gap_max_ms": round(max(gaps)/1e6, 4), "consumer_gap_median_ms": round(statistics.median(gaps)/1e6, 4),
        "renderer_gap_max_ms": round(max(renderer_gaps)/1e6, 4), "gaps_at_or_above_100ms": sum(x >= 100_000_000 for x in gaps),
        "black_frames": black, "unknown_frames": unknown, "correct_switches": correct,
        "unsampled_consumer_callbacks": unsampled,
        "decoder_creations": sum(x["kind"] == "decoder_started" for x in bridge),
        "decoder_releases": sum(x["kind"] == "decoder_released" for x in bridge),
        "egl_creations": len(egl_create), "egl_releases": sum(x["kind"] == "egl_released" for x in bridge),
        "renderer_repeated_frames": number(outputs[-1]["detail"], "repeated"),
        "renderer_skipped_ticks": number(outputs[-1]["detail"], "skippedTicks"),
        "source_coalesced_callbacks": sum(x["kind"] == "source_coalesced" for x in bridge),
        "renderer_minus_consumer_frames": len(outputs)-len(frames),
        "errors": errors, "fatal_diagnostics": fatal, "switches": switch_results,
        "errors_outside_active_foreground_window": [x for x in bridge if x["kind"] in ("pipeline_error", "decoder_error", "preload_failed") and not start <= x["ns"] <= end],
        "limits": "Repeats include deliberate 15-to-30fps conversion/holding. Coalesced callbacks and output/consumer delta are not exact HAL drop counters. Thresholds apply to active foreground interval, not Surface recreation during lifecycle tests.",
    }

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", default="test-results/phase3/endurance")
    args=parser.parse_args()
    result=analyze(args.directory)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result["result"] == "PASS" else 1)
