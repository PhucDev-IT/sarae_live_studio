"""Lean server for customer Studios (docs/HYBRID_STUDIO_SERVER.md): `uvicorn app.gateway_main:app`.

Only health, device activation and /api/studio/v1 (license, voices, TTS). No legacy router, no startup hook
(shop_schema, runtime janitor, TTS orchestrator), no CORS (the Studio is a desktop client, not a browser page).
Binds 127.0.0.1 behind cloudflared, so the client IP comes from CF-Connecting-IP, then X-Forwarded-For.
/docs and /openapi.json exist only with SARAE_GATEWAY_DOCS=1.
"""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import identity, studio_gateway
from app.config import settings
from app.identity import agent_error

GATEWAY_VERSION = "1.0.0"
MAX_BODY_BYTES = 64 * 1024
log = logging.getLogger(__name__)

_docs = settings.sarae_gateway_docs
app = FastAPI(title="Sarae Studio Gateway", version=GATEWAY_VERSION,
              docs_url="/docs" if _docs else None, redoc_url="/redoc" if _docs else None,
              openapi_url="/openapi.json" if _docs else None)

_TRANSLATED = {"Not Found": "Không tìm thấy.", "Method Not Allowed": "Phương thức không được hỗ trợ."}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = _TRANSLATED.get(exc.detail, exc.detail) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Dữ liệu gửi lên không hợp lệ."}, status_code=422,
                        headers={"X-Sarae-Error": "INVALID_PARAMS"})


@app.middleware("http")
async def limit_body(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return JSONResponse({"detail": "Dữ liệu gửi lên quá lớn."}, status_code=413,
                            headers={"X-Sarae-Error": "INVALID_PARAMS"})
    return await call_next(request)


def client_ip(request: Request) -> str:
    ip = request.headers.get("cf-connecting-ip", "").strip()
    if not ip:
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return ip[:64] or "unknown"


def activation_rate(request: Request) -> None:
    retry = studio_gateway.activate_limiter.hit(client_ip(request))
    if retry is not None:
        raise studio_gateway.rate_limited(retry)


class ActivateIn(BaseModel):
    activation_key: str = Field(max_length=500)
    hostname: str = Field(default="", max_length=500)
    platform: str = Field(default="WINDOWS", max_length=20)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "UP", "service": "sarae-studio-gateway", "version": GATEWAY_VERSION}


@app.post("/api/identity/activate", dependencies=[Depends(activation_rate)])
def activate(body: ActivateIn):
    key = body.activation_key.strip()
    if not 20 <= len(key) <= 200:
        raise agent_error(403, "Mã kích hoạt không hợp lệ hoặc đã sử dụng", "ACTIVATION_KEY_INVALID")
    platform = body.platform.strip().upper() or "WINDOWS"
    if platform != "WINDOWS":
        raise agent_error(422, "Nền tảng máy không được hỗ trợ.", "INVALID_PARAMS")
    hostname = body.hostname.strip()[:160] or "Máy Windows"
    result = identity.activate(identity.Activate(activation_key=key, hostname=hostname, platform=platform))
    log.info("device activated agent=%s owner=%s", result["agent_id"], result["owner_id"])
    return result


app.include_router(studio_gateway.router)
