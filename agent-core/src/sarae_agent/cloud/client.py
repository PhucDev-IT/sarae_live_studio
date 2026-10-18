"""HTTPS client for the Sarae Cloud Agent API.

Outbound only: the Agent never opens an inbound port. Every failure is mapped to a stable
error code so the worker can decide between retry (transient) and stop (requires the user).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from sarae_agent import __version__
from sarae_agent.errors import AgentError

log = logging.getLogger("sarae.cloud")

# Codes that cannot be fixed by retrying; the worker stops and the GUI asks the user to act.
FATAL_CODES = {"AGENT_REVOKED", "LICENSE_EXPIRED", "AGENT_UNAUTHORIZED", "AGENT_NOT_ACTIVATED"}


class CloudError(AgentError):
    @property
    def fatal(self) -> bool:
        return self.code in FATAL_CODES


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(item.get("msg")) for item in detail[:3] if isinstance(item, dict))
    return str(detail or body)[:300]


def classify(response: httpx.Response, context: str) -> CloudError:
    status = response.status_code
    message = _detail(response)
    code = response.headers.get("X-Sarae-Error", "").strip().upper()
    if not code:
        # Older Cloud builds do not send X-Sarae-Error; fall back to status + context.
        if status == 401:
            code = "AGENT_UNAUTHORIZED"
        elif status == 403 and context == "activate":
            code = "LICENSE_EXPIRED" if "license" in message.lower() else "ACTIVATION_KEY_INVALID"
        elif status == 403:
            code = "LICENSE_EXPIRED"
        elif status == 409 and context == "activate":
            code = "AGENT_LIMIT_REACHED"
        elif status == 404 and context == "ack":
            code = "COMMAND_NOT_RUNNING"
        elif status == 422:
            code = "CLOUD_VALIDATION_ERROR"
        elif status >= 500:
            code = "CLOUD_SERVER_ERROR"
        else:
            code = f"CLOUD_HTTP_{status}"
    retryable = status >= 500 or status == 429
    return CloudError(code, message or f"Cloud trả về HTTP {status}", {"status": status}, retryable=retryable)


class CloudClient:
    def __init__(self, api_url: str, *, timeout: float = 12.0, transport: httpx.BaseTransport | None = None):
        if not api_url:
            raise AgentError("CLOUD_URL_MISSING", "Chưa cấu hình Cloud API URL")
        self.api_url = api_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.api_url,
            timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)),
            headers={"User-Agent": f"SaraeAgent/{__version__}", "Accept": "application/json"},
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CloudClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, method: str, path: str, context: str, *, key: str | None = None,
                 json: Any = None) -> dict:
        headers = {"X-Sarae-Agent-Key": key} if key else None
        try:
            response = self._http.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise CloudError("CLOUD_TIMEOUT", f"Cloud không phản hồi ({type(exc).__name__})",
                             retryable=True) from exc
        except httpx.TransportError as exc:
            raise CloudError("CLOUD_UNREACHABLE", f"Không kết nối được Cloud: {str(exc)[:160]}",
                             retryable=True) from exc
        if response.status_code >= 400:
            raise classify(response, context)
        try:
            body = response.json()
        except ValueError as exc:
            raise CloudError("CLOUD_BAD_RESPONSE", "Cloud trả về dữ liệu không phải JSON",
                             retryable=True) from exc
        return body if isinstance(body, dict) else {"data": body}

    # --- Endpoints -----------------------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health", "health")

    def activate(self, activation_key: str, hostname: str) -> dict:
        body = self._request("POST", "/identity/activate", "activate",
                             json={"activation_key": activation_key, "hostname": hostname})
        if not body.get("agent_id") or not body.get("agent_credential"):
            raise CloudError("CLOUD_BAD_RESPONSE", "Cloud không trả về agent_id/agent_credential")
        return body

    def heartbeat(self, key: str, report: dict) -> dict:
        return self._request("POST", "/agents/heartbeat", "heartbeat", key=key, json=report)

    def next_command(self, key: str, agent_id: str) -> dict | None:
        body = self._request("GET", f"/agents/commands/{agent_id}/next", "poll", key=key)
        command = body.get("command")
        return command if isinstance(command, dict) else None

    def ack(self, key: str, agent_id: str, command_id: str, status: str, result: str) -> dict:
        """status: accepted | running | succeeded | failed. `success` kept for older Cloud builds."""
        payload = {"agent_id": agent_id, "command_id": command_id, "status": status,
                   "result": result[:1900]}
        if status in ("succeeded", "failed"):
            payload["success"] = status == "succeeded"
        return self._request("POST", "/agents/commands/ack", "ack", key=key, json=payload)

    def sync_library(self, key: str, agent_id: str, videos: list[dict]) -> dict:
        return self._request("POST", "/agent-library/sync", "sync", key=key,
                             json={"agent_id": agent_id, "videos": videos})
