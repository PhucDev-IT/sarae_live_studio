from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.catalog import public_router as catalog_public_router, router as catalog_router
from app.media import router as media_router
from app.live import router as live_router
from app.product_videos import router as product_videos_router
from app.media_presenters import router as media_presenters_router
from app.live_sessions import router as live_sessions_router
from app.tiktok_chat import router as tiktok_chat_router
from app.ai_service import router as ai_router
from app.ai_live_batches import router as ai_live_router
from app.ai_voice import router as ai_voice_router
from app.verification_monitor import router as verification_monitor_router
from app.cloud_agents import router as cloud_agents_router
from app.identity import router as identity_router
from app.agent_library import router as agent_library_router
from app.agent_playlists import router as agent_playlists_router
from app.owner_agents import router as owner_agents_router
from app.captcha_templates import router as captcha_templates_router

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.on_event("startup")
def start_runtime_janitor():
    from app.runtime import start_janitor
    start_janitor()


@app.middleware("http")
async def saas_legacy_guard(request, call_next):
    """Fail closed in shared VPS deployments: only APIs partitioned by OWNER (and by Agent where it applies) answer.

    Every router below checks the caller (current_owner / Agent credential). /api/media and /api/live are the Agent
    library and the LIVE facade: they need an agent_id owned by the caller and never touch MP4 files on the server.
    A new router must be added here on purpose, after it is scoped.
    """
    from fastapi.responses import JSONResponse
    path=request.url.path
    safe=("/api/identity", "/api/agents", "/api/agent-library", "/api/agent-playlists", "/api/owner",
          "/api/products", "/api/presenters", "/api/product-videos", "/api/presenter-videos",
          "/api/media", "/api/live", "/api/tiktok-chat", "/api/ai", "/api/verification-monitor",
          "/api/captcha-templates")
    if settings.sarae_saas_mode and path.startswith("/api/") and path!="/api/health":
        if not any(path==prefix or path.startswith(prefix+"/") for prefix in safe):
            return JSONResponse(status_code=403,content={
                "detail":"API local cũ chưa hỗ trợ tách dữ liệu OWNER; không khả dụng trong SaaS mode"})
    return await call_next(request)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


app.include_router(catalog_public_router)
app.include_router(catalog_router)
app.include_router(media_router)
app.include_router(live_router)
app.include_router(product_videos_router)
app.include_router(media_presenters_router)
app.include_router(live_sessions_router)
app.include_router(tiktok_chat_router)
app.include_router(ai_router)
app.include_router(ai_live_router)
app.include_router(ai_voice_router)
app.include_router(verification_monitor_router)
app.include_router(cloud_agents_router)
app.include_router(identity_router)
app.include_router(agent_library_router)
app.include_router(agent_playlists_router)
app.include_router(owner_agents_router)
app.include_router(captcha_templates_router)


@app.get("/api/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "UP", "service": "sarae-live-studio-api", "version": settings.app_version}
