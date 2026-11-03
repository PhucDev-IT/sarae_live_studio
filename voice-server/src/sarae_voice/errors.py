"""One error type for the API: a stable code (docs/TTS_SERVER.md 7.1), an HTTP status and a safe message."""
from __future__ import annotations


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 500, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after
