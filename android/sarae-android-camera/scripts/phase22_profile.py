"""One bounded simpleperf run during the existing owned OpenSL recorder suite.

No TikTok process or package mutation. The suite's first 10 seconds are MIC.
Run from the Android project directory; no automatic retry.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ADB = "D:/window/Android/Sdk/platform-tools/adb.exe"
OWN = "co.sarae.camera.nativeaudiotest"
TARGET = "com.ss.android.ugc.trill"
MODULE = "cache/lspatch/co.sarae.camera.poc/183496786.apk"
EXPECTED_MODULE = "66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120"
RUN = "phase22-" + dt.datetime.now(dt.timezone.utc).strftime("%H%M%S")
OUT = ROOT / "test-results" / "phase22"
RAW = ROOT / ".tools" / "phase22" / RUN
DEVICE_DATA = "/data/local/tmp/sarae-phase22-perf.data"
FILES = f"/sdcard/Android/data/{OWN}/files"
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)
START = time.monotonic()
COMMANDS = []


def adb(*args: str, timeout: float = 6, allow_error: bool = False) -> str:
    if time.monotonic() - START > 270:
        raise TimeoutError("Phase 22 device budget guard")
    began = time.monotonic()
    try:
        process = subprocess.run(
            [ADB, *args], capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (process.stdout + process.stderr).decode(errors="replace")
        COMMANDS.append({"args": args, "seconds": time.monotonic() - began,
                         "exitCode": process.returncode, "output": output[:1200]})
        if process.returncode and not allow_error:
            raise RuntimeError(f"adb {args}: {output[:500]}")
        return output
    except subprocess.TimeoutExpired:
        COMMANDS.append({"args": args, "seconds": time.monotonic() - began,
                         "timedOut": True})
        raise


def main() -> None:
    state = {"run": RUN, "startUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
             "recordingStarted": False, "tiktokModified": False,
             "profileSecondsRequested": 6, "profileFrequencyHz": 499}
    launched = False
    stay_on_before = None
    try:
        assert "device" in adb("devices", "-l")
        assert adb("shell", "getprop", "ro.product.model").strip() == "SM-G996U"
        assert adb("shell", "pm", "path", OWN).startswith("package:")
        permissions = adb("shell", "dumpsys", "package", OWN)
        assert "android.permission.RECORD_AUDIO: granted=true" in permissions
        assert FILES + "/source.mp4" in adb("shell", "ls", FILES + "/source.mp4")
        state["targetPidBefore"] = adb("shell", "pidof", TARGET, allow_error=True).strip()
        state["moduleBefore"] = adb("shell", "run-as", TARGET, "sha256sum", MODULE).split()[0]
        assert state["moduleBefore"] == EXPECTED_MODULE
        assert "mWakefulness=Awake" in adb("shell", "dumpsys", "power")
        assert "mInputRestricted=false" in adb("shell", "dumpsys", "window")
        stay_on_before = adb("shell", "settings", "get", "global",
                             "stay_on_while_plugged_in").strip()
        assert stay_on_before.isdigit()
        adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", "2")
        focus = adb("shell", "dumpsys", "activity", "activities")
        state["foregroundBefore"] = next((line.strip() for line in focus.splitlines()
                                           if "topResumedActivity=" in line), "unknown")
        assert TARGET not in state["foregroundBefore"], "TikTok is foreground; stop"
        # -S stops only this owned harness so onCreate gets the new runId.
        activity = adb("shell", "am", "start", "-S", "-W", "-n", OWN + "/.RecorderActivity",
                       "--ei", "seconds", "10", "--es", "runId", RUN, timeout=8)
        assert "Warning: Activity not started" not in activity
        (RAW / "launch.txt").write_text(activity)
        launched = True
        state["recordingStarted"] = True
        state["ownPid"] = adb("shell", "pidof", OWN).strip()
        # First suite case is MICROPHONE for 10 s. Keep profiling within it.
        time.sleep(0.5)
        try:
            profile_start = time.monotonic()
            state["profileStartUptime"] = adb("shell", "cat", "/proc/uptime").split()[0]
            profile = adb("shell", "simpleperf", "record", "--app", OWN,
                          "-f", "499", "-e", "task-clock", "-g", "--duration", "6",
                          "-o", DEVICE_DATA, timeout=11)
            state["profileWallSeconds"] = time.monotonic() - profile_start
            state["profileEndUptime"] = adb("shell", "cat", "/proc/uptime").split()[0]
            (RAW / "simpleperf-record.txt").write_text(profile)
            adb("pull", DEVICE_DATA, str(RAW / "perf.data"), timeout=8)
            state["perfBytes"] = (RAW / "perf.data").stat().st_size
            state["perfSha256"] = hashlib.sha256((RAW / "perf.data").read_bytes()).hexdigest()
            report = adb("shell", "simpleperf", "report", "-i", DEVICE_DATA,
                         "--sort", "comm,pid,tid,symbol", timeout=12, allow_error=True)
            (RAW / "simpleperf-report.txt").write_text(report)
        except Exception as exc:
            state["profileError"] = str(exc)
        # Let the existing 10 s MIC / 10 s VIDEO / 10 s MUTED suite finish.
        deadline = time.monotonic() + 44
        while time.monotonic() < deadline:
            listing = adb("shell", "ls", f"{FILES}/phase15/{RUN}/DONE",
                          f"{FILES}/phase15/{RUN}/ERROR", timeout=4, allow_error=True)
            present = {line.strip() for line in listing.splitlines()
                       if line.startswith(FILES + "/phase15/")}
            if f"{FILES}/phase15/{RUN}/DONE" in present:
                state["suiteDone"] = True
                break
            if f"{FILES}/phase15/{RUN}/ERROR" in present:
                state["suiteError"] = True
                break
            time.sleep(1)
        else:
            state["suiteTimedOut"] = True
    finally:
        if launched:
            adb("pull", f"{FILES}/phase15/{RUN}", str(RAW / "harness"),
                timeout=9, allow_error=True)
            adb("shell", "input", "keyevent", "KEYCODE_HOME", allow_error=True)
        if stay_on_before is not None:
            adb("shell", "settings", "put", "global", "stay_on_while_plugged_in",
                stay_on_before, allow_error=True)
        adb("shell", "rm", DEVICE_DATA, allow_error=True)
        state["targetPidAfter"] = adb("shell", "pidof", TARGET, allow_error=True).strip()
        state["moduleAfter"] = adb("shell", "run-as", TARGET, "sha256sum", MODULE).split()[0]
        state["tiktokPreserved"] = (state["moduleBefore"] == state["moduleAfter"]
                                    == EXPECTED_MODULE)
        state["deviceWallSeconds"] = time.monotonic() - START
        state["endUtc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (OUT / "state.json").write_text(json.dumps(state, indent=2))
        (OUT / "adb.jsonl").write_text("".join(json.dumps(row) + "\n" for row in COMMANDS))
        print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
