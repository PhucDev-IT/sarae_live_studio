"""Bounded, read-only Phase 23 preflight and one-shot LIVE diagnostics.

Run `preflight` while TikTok is in its lab camera preview. Run `capture` only
after the user confirms that the single LIVE session is active. It never sends
UI input, launches/restarts an app, edits a module, or retries a profile.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ADB = "D:/window/Android/Sdk/platform-tools/adb.exe"
TARGET = "com.ss.android.ugc.trill"
EXPECTED_BASE = "4cab9b7751c35601594d9b12fc94d9ed74bd3ad5a485a9161c522fee118f900b"
EXPECTED_MODULE = "66dc9f4a34558748fcdafd73afcf82e1a128a9f36955e8564ae9c18ab849f120"
EXPECTED_OBSERVER = "e065436052ed31317b27a781665647c4c80fbc7a565addf70b6a688db3cf1881"
EXPECTED_LYRA = "9d82c55f5cf86ed140d9236b15db95c9846cdcac9a08ef058ab03b20d806c4de"
MODULE = "cache/lspatch/co.sarae.camera.poc/183496786.apk"
OBSERVER = ("code_cache/sarae-native-observer/" + EXPECTED_OBSERVER +
            "/libsarae_native_observer.so")
REMOTE = "/data/local/tmp/sarae-phase23-perf.data"
START = time.monotonic()
ADB_ROWS: list[dict] = []


def adb(*args: str, timeout: float = 8, check: bool = True) -> str:
    if time.monotonic() - START > 250:
        raise TimeoutError("Phase 23 active diagnostics reached 250-second guard")
    began = time.monotonic()
    proc = subprocess.run([ADB, *args], capture_output=True, timeout=timeout,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    output = (proc.stdout + proc.stderr).decode(errors="replace")
    ADB_ROWS.append({"args": args, "seconds": time.monotonic() - began,
                     "exitCode": proc.returncode, "output": output[:4000]})
    if check and proc.returncode:
        raise RuntimeError(f"adb {args}: {output[:800]}")
    return output


def preflight() -> dict:
    assert "RFCR10DGF1T" in adb("devices", "-l")
    assert adb("shell", "getprop", "ro.product.model").strip() == "SM-G996U"
    assert adb("shell", "getprop", "ro.build.version.sdk").strip() == "34"
    assert adb("shell", "getprop", "ro.product.cpu.abi").strip() == "arm64-v8a"
    package = adb("shell", "dumpsys", "package", TARGET)
    assert "versionName=47.4.3" in package and "DEBUGGABLE" in package
    paths = adb("shell", "pm", "path", TARGET).splitlines()
    base_line = next(line for line in paths if line.endswith("/base.apk"))
    base_path = base_line.removeprefix("package:").strip()
    base_hash = adb("shell", "sha256sum", base_path).split()[0]
    module_hash = adb("shell", "run-as", TARGET, "sha256sum", MODULE).split()[0]
    observer_hash = adb("shell", "run-as", TARGET, "sha256sum", OBSERVER).split()[0]
    assert base_hash == EXPECTED_BASE
    assert module_hash == EXPECTED_MODULE
    assert observer_hash == EXPECTED_OBSERVER
    run_as = adb("shell", "run-as", TARGET, "id")
    assert f"uid=" in run_as
    pid = adb("shell", "pidof", TARGET).strip().split()[0]
    maps = adb("shell", "run-as", TARGET, "cat", f"/proc/{pid}/maps")
    assert "libsarae_native_observer.so" in maps
    lyra_lines = [line for line in maps.splitlines() if "liblyrax.so" in line]
    assert lyra_lines
    lyra_path = lyra_lines[0].split()[-1]
    lyra_hash = adb("shell", "run-as", TARGET, "sha256sum", lyra_path).split()[0]
    assert lyra_hash == EXPECTED_LYRA
    help_text = adb("shell", "simpleperf", "help", "record")
    assert "--app package_name" in help_text and "--duration" in help_text and "--call-graph" in help_text
    storage = adb("shell", "df", "-Pk", "/data/local/tmp")
    free_kb = int(storage.splitlines()[-1].split()[3])
    assert free_kb > 100 * 1024
    media = adb("shell", "ls", "-l",
                f"/sdcard/Android/data/{TARGET}/files/A.mp4",
                f"/sdcard/Android/data/{TARGET}/files/B.mp4")
    assert "/A.mp4" in media and "/B.mp4" in media
    bridge_log = adb("logcat", "-d", "-t", "1800", "-v", "threadtime",
                     "-s", "SaraeBridge:I")
    assert "event=capture_result_observed" in bridge_log
    source_a = "source=file:///storage/emulated/0/Android/data/" + TARGET + "/files/A.mp4"
    assert source_a in bridge_log and ("event=frame_rendered " + source_a in bridge_log
                                       or "event=output_present " + source_a in bridge_log)
    focus_dump = adb("shell", "dumpsys", "activity", "activities")
    focus = next((line.strip() for line in focus_dump.splitlines()
                  if "topResumedActivity=" in line), "unknown")
    state = {"device": "SM-G996U", "api": 34, "abi": "arm64-v8a",
             "package": TARGET, "version": "47.4.3", "pid": pid,
             "baseSha256": base_hash, "moduleSha256": module_hash,
             "observerSha256": observer_hash,
             "observerMapped": True,
             "liblyraxCurrentlyMapped": True, "liblyraxSha256": lyra_hash,
             "camera2CaptureCallbacksObserved": True,
             "rendererFrameSourceAObserved": True,
             "storageFreeKiB": free_kb, "mediaFiles": media,
             "foreground": focus,
             "previewForeground": TARGET in focus,
             "simpleperfAppMode": "supported by flags/run-as; historical --app run succeeded",
             "liveStarted": False}
    return state


def main() -> None:
    if sys.argv[1:] not in (["preflight"], ["capture"]):
        raise SystemExit("usage: phase23_live_diagnostics.py preflight|capture")
    action = sys.argv[1]
    out = ROOT / "test-results" / "phase23"
    raw = ROOT / ".tools" / "phase23"
    out.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    state = {"action": action, "startedUtc": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        state.update(preflight())
        if action == "capture":
            run_id = dt.datetime.now(dt.timezone.utc).strftime("live-%Y%m%dT%H%M%SZ")
            run_dir = raw / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            state["runId"] = run_id
            state["liveStarted"] = "user-confirmed before command"
            profile_started = time.monotonic()
            try:
                state["profileOutput"] = adb(
                    "shell", "simpleperf", "record", "--app", TARGET,
                    "-f", "499", "-e", "task-clock", "-g", "--duration", "6",
                    "-o", REMOTE, timeout=14)
                state["profileWallSeconds"] = time.monotonic() - profile_started
                adb("pull", REMOTE, str(run_dir / "perf.data"), timeout=10)
                perf = run_dir / "perf.data"
                if perf.exists():
                    state["perfBytes"] = perf.stat().st_size
                    state["perfSha256"] = hashlib.sha256(perf.read_bytes()).hexdigest()
            except Exception as exc:
                state["profileError"] = str(exc)

            pid_after = adb("shell", "pidof", TARGET, check=False).strip()
            state["pidAfterProfile"] = pid_after
            if pid_after and pid_after.split()[0] == state["pid"]:
                state["processLogcat"] = adb("logcat", "-d", "--pid=" + state["pid"],
                                              "-t", "1800", "-v", "threadtime", timeout=10)
            else:
                state["pidChangedDuringProfile"] = True
            state["crashLogcat"] = adb("logcat", "-b", "crash", "-d", "-t", "120",
                                       "-v", "threadtime", timeout=8, check=False)
            for name, count in (("audio", 240), ("bridge", 320), ("observer", 35)):
                relative = {"audio": "phase11/audio-", "bridge": "phase6/bridge-",
                            "observer": "phase18/native-"}[name]
                path = f"/sdcard/Android/data/{TARGET}/files/{relative}{state['pid']}.jsonl"
                state[name + "JsonlTail"] = adb("shell", "run-as", TARGET, "tail", "-n",
                                                 str(count), path, timeout=8, check=False)
            state["audioFlinger"] = adb("shell", "dumpsys", "media.audio_flinger",
                                        timeout=10, check=False)
            state["audioService"] = adb("shell", "dumpsys", "audio", timeout=10, check=False)
            state["cameraService"] = adb("shell", "dumpsys", "media.camera", timeout=10, check=False)
            state["mapsAfter"] = adb("shell", "run-as", TARGET, "cat",
                                     f"/proc/{state['pid']}/maps", timeout=8, check=False)
            # Save each bounded raw artifact separately for offline correlation.
            for key, value in list(state.items()):
                if key in ("processLogcat", "crashLogcat", "audioJsonlTail", "bridgeJsonlTail",
                           "observerJsonlTail", "audioFlinger", "audioService", "cameraService", "mapsAfter"):
                    (run_dir / f"{key}.txt").write_text(str(value), encoding="utf-8")
            # This is Sarae's unique temporary path; remove only after pulling it.
            adb("shell", "rm", REMOTE, timeout=5, check=False)
        else:
            state["liveStarted"] = False
    except Exception as exc:
        state["preflightError"] = f"{type(exc).__name__}: {exc!r}"
    finally:
        state["deviceWorkSeconds"] = time.monotonic() - START
        state["endedUtc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (out / f"{action}-latest.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (out / f"{action}-adb.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in ADB_ROWS), encoding="utf-8")
        print(json.dumps({k: v for k, v in state.items()
                          if k not in ("processLogcat", "crashLogcat", "audioJsonlTail",
                                       "bridgeJsonlTail", "observerJsonlTail", "audioFlinger",
                                       "audioService", "cameraService", "mapsAfter")}, indent=2))


if __name__ == "__main__":
    main()
