#!/usr/bin/env python3
"""Summarize captured JSONL. Exit nonzero when the Phase 1 checks fail."""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def events(rows: list[dict], kind: str) -> list[dict]:
    return [row for row in rows if row["kind"] == kind]


def summarize(switch_log: Path, loop_log: Path) -> dict:
    switching = read(switch_log)
    looping = read(loop_log)
    checks = events(switching, "switch_continuity")
    requests = events(switching, "switch_requested_ui")
    frames = events(switching, "frame_rendered")
    sources = Counter(Path(row["source"]).name for row in frames)
    errors = [row for row in switching + looping if row["kind"] in
              ("decoder_error", "camera2_error", "crash")]
    sessions = events(switching, "camera2_session_started")
    video_surfaces = [row["detail"] for row in events(switching, "surface_available")
                      if row["detail"].startswith("video ")]
    fps = {
        name: round(statistics.median(row["fps"] for row in events(switching, "fps")
                                      if row.get("source", "").endswith(name)), 2)
        for name in sources
    }
    loop_events = events(looping, "loop")
    loop_frames = events(looping, "frame_rendered")
    checks_by_number = [int(row["detail"].split("number=")[1].split()[0]) for row in checks]
    pass_switch = (
        len(requests) >= 10
        and len(checks) == len(requests)
        and checks_by_number == list(range(1, len(requests) + 1))
        and all("result=PASS" in row["detail"] and
                "cameraAdvanced=true" in row["detail"] and
                "surfaceStable=true" in row["detail"] for row in checks)
        and len(sessions) == 1
        and len(video_surfaces) == 1
        and len(sources) == 2
        and all(count > 100 for count in sources.values())
        and all(value > 0 for value in fps.values())
        and not errors
    )
    pass_loop = (
        len(loop_events) >= 2
        and len(loop_frames) > 180
        and len(events(looping, "camera2_session_started")) == 1
        and not errors
    )
    return {
        "device": "SM-G996U / Android 14 API 34 / arm64-v8a",
        "switching": {
            "result": "PASS" if pass_switch else "FAIL",
            "requests": len(requests),
            "continuity_pass": sum("result=PASS" in row["detail"] for row in checks),
            "continuity_fail": sum("result=FAIL" in row["detail"] for row in checks),
            "camera2_sessions": len(sessions),
            "video_surfaces": len(video_surfaces),
            "rendered_frames_by_source": dict(sources),
            "median_decoder_fps_by_source": fps,
        },
        "looping": {
            "result": "PASS" if pass_loop else "FAIL",
            "completed_loops": len(loop_events),
            "rendered_frames": len(loop_frames),
        },
        "errors": [row["detail"] for row in errors],
        "scope": "Own app: real Camera2 preview beside MP4 Surface output; no virtual Camera2 injection",
    }


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: analyze_phase1.py SWITCH.jsonl LOOP.jsonl")
    result = summarize(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["switching"]["result"] != "PASS" or result["looping"]["result"] != "PASS":
        sys.exit(1)
