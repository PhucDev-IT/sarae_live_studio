"""Monitor an already-started Android Agent mock playback through the existing FastAPI owner API."""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://localhost:8765")
    parser.add_argument("--credentials", required=True, type=Path)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--sample-seconds", type=int, default=15)
    args = parser.parse_args()
    credentials = json.loads(args.credentials.read_text(encoding="utf-8"))
    headers = {"Authorization": "Bearer " + credentials["owner_token"]}
    start = time.monotonic()
    end = start + args.duration_seconds
    samples, online, playing, transitions, foreground_checks, process_checks = 0, 0, 0, 0, 0, 0
    previous_media = None
    missing_samples = []
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(verify=False, headers=headers, timeout=20) as client, args.output.open("w", encoding="utf-8") as log:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= args.duration_seconds:
                break
            stamp = datetime.now(timezone.utc).isoformat()
            response = client.get(args.base_url + "/api/owner/agents")
            response.raise_for_status()
            agent = next((item for item in response.json()["agents"] if item["id"] == args.agent_id), None)
            if agent is None:
                missing_samples.append({"at": stamp, "reason": "agent_missing"})
                playback = {}
            else:
                if agent.get("online"):
                    online += 1
                state = agent.get("playback_state")
                current = agent.get("playback_media_id")
                if state == "PLAYING" and agent.get("playing") is True:
                    playing += 1
                if previous_media is not None and current and current != previous_media:
                    transitions += 1
                if current:
                    previous_media = current
                if not agent.get("online") or state not in ("PLAYING", "PREPARING") or agent.get("playback_error"):
                    missing_samples.append({"at": stamp, "online": agent.get("online"), "state": state, "error": agent.get("playback_error")})
            row = {"at": stamp, "elapsed_seconds": round(elapsed, 1), "online": agent.get("online") if agent else False,
                   "state": agent.get("playback_state") if agent else None, "media_id": agent.get("playback_media_id") if agent else None,
                   "position_ms": agent.get("playback_position_ms") if agent else None,
                   "duration_ms": agent.get("playback_duration_ms") if agent else None,
                   "mock_live": agent.get("mock_live") if agent else None,
                   "error": agent.get("playback_error") if agent else None}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            if samples % 20 == 0:
                services = subprocess.run([args.adb, "shell", "dumpsys", "activity", "services", "co.sarae.agent"],
                                          capture_output=True, text=True, timeout=15)
                foreground_checks += int(services.returncode == 0 and "isForeground=true" in services.stdout)
                pid = subprocess.run([args.adb, "shell", "pidof", "co.sarae.agent"], capture_output=True,
                                     text=True, timeout=15)
                process_checks += int(pid.returncode == 0 and bool(pid.stdout.strip()))
            samples += 1
            time.sleep(min(args.sample_seconds, max(0, end - time.monotonic())))

    duration = round(time.monotonic() - start, 1)
    summary = {"result": "PASS" if duration >= args.duration_seconds and samples >= 200 and online / max(samples, 1) >= .95
                                  and playing / max(samples, 1) >= .90 and transitions >= 100
                                  and foreground_checks >= 10 and process_checks >= 10 and not missing_samples
                                  else "FAIL",
               "duration_seconds": duration, "samples": samples, "online_samples": online,
               "playing_samples": playing, "online_ratio": round(online / max(samples, 1), 4),
               "playing_ratio": round(playing / max(samples, 1), 4), "media_transitions": transitions,
               "foreground_checks_passed": foreground_checks, "process_checks_passed": process_checks,
               "missing_or_unhealthy_samples": missing_samples}
    args.output.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
