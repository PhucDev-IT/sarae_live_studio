from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.catalog import public_router as catalog_public_router, router as catalog_router
from app.media import router as media_router
from app.local_media import router as local_media_router
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
from app.agent_media import router as agent_media_router
from app.identity import router as identity_router
from app.agent_library import router as agent_library_router
from app.agent_playlists import router as agent_playlists_router

app = FastAPI(title=settings.app_name, version=settings.app_version)

@app.middleware("http")
async def saas_legacy_guard(request, call_next):
    """Fail closed for legacy single-tenant APIs in shared VPS deployments.

    Until products/live sessions/media are partitioned by OWNER, they MUST NOT
    be reachable by any SaaS user or by guessing endpoint URLs.
    """
    from fastapi.responses import JSONResponse
    path=request.url.path
    safe=("/api/identity", "/api/agents", "/api/agent-library", "/api/agent-playlists")
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
app.include_router(local_media_router)
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
## Cloud MP4 relay disabled in local-first multi-tenant phase.
# app.include_router(agent_media_router)
app.include_router(identity_router)
app.include_router(agent_library_router)
app.include_router(agent_playlists_router)


@app.get("/api/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "UP", "service": "sarae-live-studio-api", "version": settings.app_version}
