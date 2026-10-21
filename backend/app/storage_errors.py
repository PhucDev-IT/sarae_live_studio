"""Provider-neutral storage errors with stable codes (X-Sarae-Error) for Web, Agent and logs.

Storage problems are system problems: they never answer 401 (Sarae Web would sign the customer out) and their
customer-facing text never reveals credentials, folder ids or provider URLs.
"""
from fastapi import HTTPException


class StorageError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


HTTP_STATUS = {
    "STORAGE_NOT_CONFIGURED": 503,
    "STORAGE_NOT_CONNECTED": 503,
    "STORAGE_TOKEN_REVOKED": 503,
    "STORAGE_TOKEN_INVALID": 503,
    "STORAGE_RATE_LIMITED": 503,
    "STORAGE_UNREACHABLE": 502,
    "STORAGE_PROVIDER_ERROR": 502,
    "STORAGE_FORBIDDEN": 502,
    "STORAGE_QUOTA_EXCEEDED": 507,
    "STORAGE_OBJECT_NOT_FOUND": 404,
    "STORAGE_UPLOAD_EXPIRED": 410,
    "STORAGE_PROVIDER_UNKNOWN": 500,
}


def http_error(error: StorageError) -> HTTPException:
    return HTTPException(HTTP_STATUS.get(error.code, 502), error.message, headers={"X-Sarae-Error": error.code})


def fail(status: int, code: str, message: str) -> HTTPException:
    """API error with a stable code; `detail` stays human text (Vietnamese) for Sarae Web."""
    return HTTPException(status, message, headers={"X-Sarae-Error": code})
