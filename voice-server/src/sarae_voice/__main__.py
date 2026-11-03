"""`python -m sarae_voice` / `sarae-voice-server`: one process, one GPU worker. Never start several workers."""
from __future__ import annotations

import logging
import sys

import uvicorn

from sarae_voice.api import create_app
from sarae_voice.config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    try:
        app = create_app(settings)
    except RuntimeError as exc:
        sys.exit(str(exc))
    # An app object (not an import string) keeps uvicorn at a single process: workers/reload cannot fork it.
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
