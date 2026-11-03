"""`python -m sarae_voice` / `sarae-voice-server`: one process, one GPU worker. Never start several workers.

Exit codes: 0 normal stop; 2 configuration or another instance owns the data directory; 75 the GPU failed
(the supervisor restarts a clean process).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

from sarae_voice.api import create_app
from sarae_voice.config import Settings
from sarae_voice.instance import InstanceLock

EXIT_CONFIG = 2
EXIT_GPU_FAILED = 75


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    lock = InstanceLock(Path(settings.data_dir) / ".instance.lock")
    try:
        lock.acquire()
        app = create_app(settings)
    except RuntimeError as exc:
        logging.error("%s", exc)
        sys.exit(EXIT_CONFIG)
    # An app object (not an import string) keeps uvicorn at a single process: workers/reload cannot fork it.
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info",
                                           access_log=False, timeout_graceful_shutdown=int(settings.shutdown_grace_s)))
    app.state.request_exit = lambda: setattr(server, "should_exit", True)
    try:
        server.run()
    finally:
        lock.release()
    engine = app.state.engine
    if engine.state == "error" and (engine.error or {}).get("code") == "TTS_GPU_FAILED":
        sys.exit(EXIT_GPU_FAILED)
    if not server.started:
        sys.exit(EXIT_CONFIG)        # e.g. the port is taken


if __name__ == "__main__":
    main()
