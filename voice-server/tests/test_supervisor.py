"""Real processes (FakeEngine, no GPU): supervisor restart after a crash, graceful stop, one instance per data dir.

    $env:SARAE_VOICE_PROCESS_TESTS = "1"; .venv\\Scripts\\python.exe -m pytest tests\\test_supervisor.py -v
Only processes started by this test are stopped.
"""
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SARAE_VOICE_PROCESS_TESTS") != "1",
                                reason="set SARAE_VOICE_PROCESS_TESTS=1 (spawns server processes)")
ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(port: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise AssertionError("server not ready")


def child_pids(log: Path) -> list[int]:
    return [int(p) for p in re.findall(r"server started pid=(\d+)", log.read_text(encoding="utf-8"))] if log.exists() else []


def test_supervisor_restarts_after_crash_and_stops_gracefully(tmp_path):
    port = free_port()
    env = {**os.environ, "SARAE_VOICE_ENGINE": "fake", "SARAE_VOICE_DATA_DIR": str(tmp_path),
           "SARAE_VOICE_PORT": str(port), "SARAE_VOICE_ORCHESTRATOR_TOKENS": "tok", "SARAE_VOICE_SHUTDOWN_GRACE_S": "5"}
    sup = subprocess.Popen([sys.executable, "-m", "sarae_voice.supervisor"], cwd=str(ROOT), env=env)
    log = tmp_path / "logs" / "supervisor.log"
    try:
        wait_ready(port)
        first = child_pids(log)[-1]
        # A second server on the same data directory refuses to start (exit 2), whatever the port.
        dup = subprocess.run([sys.executable, "-m", "sarae_voice"], cwd=str(ROOT), timeout=60, capture_output=True,
                             env={**env, "SARAE_VOICE_PORT": str(free_port())})
        assert dup.returncode == 2
        # Crash: kill the supervisor's own child; it comes back with a new pid.
        subprocess.run(["taskkill", "/PID", str(first), "/F"], capture_output=True, check=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and len(child_pids(log)) < 2:
            time.sleep(0.5)
        assert len(child_pids(log)) == 2 and child_pids(log)[-1] != first
        wait_ready(port)
        # Graceful stop through the stop file.
        (tmp_path / "supervisor.stop").write_text("stop")
        assert sup.wait(timeout=60) == 0
        text = log.read_text(encoding="utf-8")
        assert "stop requested" in text and "supervisor stopped" in text
        assert "server exited code=0" in text.split("stop requested")[-1]
    finally:
        if sup.poll() is None:
            sup.kill()
