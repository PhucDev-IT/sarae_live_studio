"""Process-level supervisor: `python -m sarae_voice.supervisor` (what the Windows scheduled task runs).

* Starts `python -m sarae_voice` (no console window), appends its output to data/logs/server.log (rotated at 20 MB).
* Restarts it when it exits unexpectedly: crash, CUDA failure (exit 75), killed. Backoff 2, 4, 8 ... 60 s; after
  10 quick failures within 10 minutes it gives up (exit 1) instead of hammering a broken GPU or driver.
* Does not restart after exit 2 (configuration / another instance owns the data directory).
* Graceful stop: create data/supervisor.stop (scripts/service.ps1 stop does). The supervisor writes data/server.stop,
  the server finishes the running GPU call (shutdown_grace_s), withdraws queued work and exits; killed only after
  the grace period.
* Only ever stops the child it started. It never touches other processes on the GPU.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from sarae_voice.config import ROOT, Settings
from sarae_voice.instance import InstanceLock

EXIT_CONFIG = 2
QUICK_S = 120
MAX_QUICK_FAILURES = 10
WINDOW_S = 600


def _rotate(log_path: Path, limit: int = 20 * 1024 * 1024) -> None:
    if log_path.exists() and log_path.stat().st_size > limit:
        old = log_path.with_suffix(".log.1")
        old.unlink(missing_ok=True)
        log_path.rename(old)


def main() -> None:
    settings = Settings()
    data = Path(settings.data_dir)
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s supervisor: %(message)s",
                        handlers=[logging.FileHandler(logs / "supervisor.log", encoding="utf-8"), logging.StreamHandler()])
    lock = InstanceLock(data / ".supervisor.lock")
    try:
        lock.acquire()
    except RuntimeError as exc:
        logging.error("%s", exc)
        sys.exit(EXIT_CONFIG)
    stop_request, server_stop = data / "supervisor.stop", data / "server.stop"
    stop_request.unlink(missing_ok=True)
    server_stop.unlink(missing_ok=True)
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":          # the child gets a real interpreter, still without a window
        python = python.with_name("python.exe")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    failures: list[float] = []
    try:
        while True:
            _rotate(logs / "server.log")
            with (logs / "server.log").open("ab") as out:
                started = time.monotonic()
                child = subprocess.Popen([str(python), "-m", "sarae_voice"], cwd=str(ROOT), stdout=out,
                                         stderr=subprocess.STDOUT, creationflags=flags,
                                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                logging.info("server started pid=%s", child.pid)
                stopping = False
                while child.poll() is None:
                    time.sleep(1)
                    if stop_request.exists() and not stopping:
                        stopping = True
                        logging.info("stop requested: asking the server to finish")
                        server_stop.write_text("stop", encoding="utf-8")
                        try:
                            child.wait(timeout=settings.shutdown_grace_s + 30)
                        except subprocess.TimeoutExpired:
                            logging.warning("server did not stop in time; terminating it")
                            child.kill()
                            child.wait()
                code = child.returncode
            uptime = time.monotonic() - started
            logging.info("server exited code=%s after %.0fs", code, uptime)
            if stopping or stop_request.exists():
                break
            if code == EXIT_CONFIG:
                logging.error("configuration error or another instance; not restarting (see server.log)")
                sys.exit(EXIT_CONFIG)
            now = time.monotonic()
            failures = [t for t in failures if now - t < WINDOW_S]
            if uptime < QUICK_S:
                failures.append(now)
            if len(failures) >= MAX_QUICK_FAILURES:
                logging.error("%s quick failures in %ss; giving up", len(failures), WINDOW_S)
                sys.exit(1)
            delay = min(60, 2 ** max(1, len(failures)))
            logging.info("restarting in %ss", delay)
            for _ in range(delay):
                if stop_request.exists():
                    break
                time.sleep(1)
            if stop_request.exists():
                break
    finally:
        stop_request.unlink(missing_ok=True)
        server_stop.unlink(missing_ok=True)
        lock.release()
        logging.info("supervisor stopped")


if __name__ == "__main__":
    main()
