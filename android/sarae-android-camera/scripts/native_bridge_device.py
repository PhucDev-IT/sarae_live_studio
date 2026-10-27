"""One bounded Sarae-owned OpenSL bridge run. Never starts or changes TikTok."""

import datetime
from decimal import Decimal
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tools" / "native-bridge"
ADB = "D:/window/Android/Sdk/platform-tools/adb.exe"
OWN = "co.sarae.camera.nativeaudiotest"
TARGET = "com.ss.android.ugc.trill"
APK = ROOT / "native-audio-test/build/outputs/apk/debug/native-audio-test-debug.apk"
FILES = f"/sdcard/Android/data/{OWN}/files"
started = time.monotonic()
entries = []
state = {"startedUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "targetModified": False}


def adb(*args, timeout=6, optional=False):
    remaining = 290 - (time.monotonic() - started)
    if remaining <= 1:
        raise TimeoutError("five-minute device budget exhausted")
    before = time.monotonic()
    try:
        result = subprocess.run(
            [ADB, *map(str, args)], capture_output=True, timeout=min(timeout, remaining),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode and not optional:
            raise RuntimeError(f"adb {args}: {result.stderr.decode(errors='replace')}")
        return result.stdout.decode(errors="replace")
    finally:
        entries.append({"args": list(map(str, args)), "seconds": round(time.monotonic()-before, 3)})


def run_case(run_id, seconds, aba, loop_ns=0):
    folder = "native-bridge" if aba or loop_ns else "phase15"
    destination = OUT / run_id
    destination.mkdir(parents=True, exist_ok=True)
    # -S stops only our completed harness task, ensuring a fresh onCreate/intent.
    args = ["shell", "am", "start", "-S", "-W", "-n", OWN + "/.RecorderActivity",
            "--ei", "seconds", str(seconds), "--es", "runId", run_id]
    if aba:
        args += ["--ez", "bridgeAba", "true"]
    if loop_ns:
        args += ["--ez", "loopOnly", "true", "--el", "loopDurationNs", str(loop_ns)]
    adb(*args, timeout=10)
    deadline = time.monotonic() + seconds * (1 if aba else 3) + 25
    while time.monotonic() < deadline:
        marker = adb("shell", "ls", f"{FILES}/{folder}/{run_id}/DONE",
                     f"{FILES}/{folder}/{run_id}/ERROR", timeout=3, optional=True)
        if "/ERROR" in marker:
            raise RuntimeError(f"owned harness {run_id} reported ERROR")
        if "/DONE" in marker:
            break
        time.sleep(2)
    else:
        raise TimeoutError(f"owned harness {run_id} did not finish")
    adb("shell", "input", "keyevent", "KEYCODE_HOME", timeout=3)
    adb("pull", f"{FILES}/{folder}/{run_id}", str(destination), timeout=20)
    result = destination / run_id
    if not (result / "DONE").exists():
        raise RuntimeError(f"missing local completion marker: {result}")
    state[run_id] = {"path": str(result), "files": sorted(p.name for p in result.iterdir())}


def main():
    aba_only = sys.argv[1:] == ["--aba-only"]
    aba_long = sys.argv[1:] == ["--aba-long"]
    loop_corrected = sys.argv[1:] == ["--loop-corrected"]
    if sys.argv[1:] not in ([], ["--aba-only"], ["--aba-long"], ["--loop-corrected"]):
        raise ValueError("expected --aba-only, --aba-long, --loop-corrected or no arguments")
    if (OUT / "device-state.json").exists():
        prior = json.loads((OUT / "device-state.json").read_text(encoding="utf-8"))
        if ("bridge-loop-corrected" in prior or
            ("bridge-aba-long" in prior and not loop_corrected) or
            ("bridge-aba" in prior and not (aba_long or loop_corrected)) or
            ("bridge-modes" in prior and not (aba_only or aba_long or loop_corrected))):
            raise RuntimeError("a bridge capture already exists; preserve it and diagnose before retry")
        if aba_only or aba_long or loop_corrected:
            if ("bridge-modes" not in prior or (aba_long and "bridge-aba" not in prior) or
                (loop_corrected and "bridge-aba-long" not in prior)):
                raise RuntimeError("correction requires the prior completed run")
            state.update(prior)
            state["correctionStartedUtc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        else:
            state["priorPreflight"] = prior
    if not APK.is_file():
        raise RuntimeError("build the owned harness APK first")
    for source in (("source-long.mp4", "sourceB-long.mp4") if aba_long else ("source.mp4", "sourceB.mp4")):
        if not (OUT / source).is_file():
            raise RuntimeError(f"missing test audio: {OUT / source}")
    activity = adb("shell", "dumpsys", "activity", "activities", timeout=8)
    top = [line for line in activity.splitlines() if "topResumedActivity=" in line]
    if not any("com.sec.android.app.launcher" in line for line in top):
        raise RuntimeError(f"device must be at HOME, observed: {top[:2]}")
    state["model"] = adb("shell", "getprop", "ro.product.model").strip()
    state["api"] = adb("shell", "getprop", "ro.build.version.sdk").strip()
    if (state["model"], state["api"]) != ("SM-G996U", "34"):
        raise RuntimeError("unexpected lab target")
    state["targetPidBefore"] = adb("shell", "pidof", TARGET, optional=True).strip()
    paths = adb("shell", "pm", "path", TARGET)
    base = next(line[8:] for line in paths.splitlines() if line.endswith("/base.apk"))
    state["targetBaseBefore"] = adb("shell", "sha256sum", base).split()[0]
    if not (aba_only or aba_long):
        adb("install", "-r", APK, timeout=35)
        adb("shell", "pm", "grant", OWN, "android.permission.RECORD_AUDIO")
        adb("shell", "mkdir", "-p", FILES)
    source_names = (("source-long.mp4", "sourceB-long.mp4") if aba_long else ("source.mp4", "sourceB.mp4"))
    for source in source_names:
        local = OUT / source
        if not aba_only:
            destination = "sourceB.mp4" if source.startswith("sourceB") else "source.mp4"
            adb("push", local, f"{FILES}/{destination}", timeout=15)
        else:
            destination = source
        digest = adb("shell", "sha256sum", f"{FILES}/{destination}").split()[0]
        if digest != hashlib.sha256(local.read_bytes()).hexdigest():
            raise RuntimeError(f"source hash mismatch: {source}")
    state["latestSourceHashes"] = {name: hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in source_names}
    if not (aba_only or aba_long or loop_corrected):
        run_case("bridge-modes", 10, False)
    if loop_corrected:
        # This audio-only harness supplies its synthetic *video* loop duration
        # from the MP4 container timeline, not the Android audio track format.
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(OUT / "source.mp4")],
            capture_output=True, timeout=8, check=True,
        )
        loop_ns = int(Decimal(probe.stdout.decode().strip()) * 1_000_000_000)
        if not 3_000_000_000 <= loop_ns <= 5_000_000_000:
            raise ValueError("the targeted three-loop fixture must be about four seconds")
        state["loopFixtureDurationNs"] = loop_ns
        run_case("bridge-loop-corrected", 16, False, loop_ns)
    elif not aba_long:
        run_case("bridge-aba", 12, True)
    else:
        run_case("bridge-aba-long", 12, True)
    state["completed"] = True
    state["targetBaseAfter"] = adb("shell", "sha256sum", base).split()[0]
    state["targetPidAfter"] = adb("shell", "pidof", TARGET, optional=True).strip()
    state["targetPreserved"] = state["targetBaseBefore"] == state["targetBaseAfter"]


try:
    main()
finally:
    try:
        adb("shell", "input", "keyevent", "KEYCODE_HOME", timeout=3, optional=True)
    except Exception:
        pass
    state["deviceSeconds"] = round(state.get("deviceSeconds", 0) + time.monotonic() - started, 3)
    state["endedUtc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "device-state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (OUT / "adb-commands.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
    print(json.dumps(state, indent=2))
