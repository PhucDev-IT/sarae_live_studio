from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.catalog import public_router as catalog_public_router, router as catalog_router
from app.live import router as live_router
from app.live_sessions import router as live_sessions_router
from app.tiktok_chat import router as tiktok_chat_router
from app.ai_service import router as ai_router
from app.ai_live_batches import router as ai_live_router
from app.ai_voice import router as ai_voice_router
from app.verification_monitor import router as verification_monitor_router
from app.cloud_agents import router as cloud_agents_router
from app.identity import router as identity_router
from app.agent_library_compat import router as agent_library_router
from app.owner_agents import router as owner_agents_router
from app.captcha_templates import router as captcha_templates_router
from app.tiktok_products import router as tiktok_products_router
from app.tiktok_catalog import router as tiktok_catalog_router
from app.android_agents import router as android_agents_router
from app.storage_admin import router as storage_admin_router
from app.media_clips import router as media_clips_router
from app.media_library import products_router as media_library_products_router, router as media_library_router
from app.tiktok_chat import session_router as tiktok_chat_session_router
from app.playback_policy import router as playback_policy_router
from app.sales_brain import router as sales_brain_router
from app.ai_answers import router as ai_answers_router
from app.agent_media import router as agent_media_router

app = FastAPI(title=settings.app_name, version=settings.app_version)


@app.on_event("startup")
def start_runtime_janitor():
    from app.shop_schema import upgrade
    upgrade()
    from app.storage_models import ensure_schema
    ensure_schema()
    from app.runtime import start_janitor
    start_janitor()


@app.middleware("http")
async def saas_legacy_guard(request, call_next):
    """Fail closed in shared VPS deployments: only APIs partitioned by OWNER (and by Agent where it applies) answer.

    Every router below checks the caller (current_owner / Agent credential). /api/live is the LIVE facade: it needs an
    agent_id owned by the caller. /api/agent-library only keeps a compatibility stub for older Agents.
    A new router must be added here on purpose, after it is scoped.
    """
    from fastapi.responses import JSONResponse
    path=request.url.path
    safe=("/api/identity", "/api/agents", "/api/agent-library", "/api/owner",
          "/api/android-agents",
          "/api/products", "/api/presenters",
          "/api/live", "/api/tiktok-chat", "/api/ai", "/api/verification-monitor",
          "/api/captcha-templates", "/api/tiktok-products", "/api/tiktok-catalog",
          "/api/storage-admin", "/api/media-library", "/api/agent-media")
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
app.include_router(live_router)
app.include_router(live_sessions_router)
app.include_router(tiktok_chat_router)
app.include_router(tiktok_chat_session_router)
app.include_router(ai_router)
app.include_router(ai_live_router)
app.include_router(ai_voice_router)
app.include_router(ai_answers_router)
app.include_router(sales_brain_router)
app.include_router(playback_policy_router)
app.include_router(verification_monitor_router)
app.include_router(cloud_agents_router)
app.include_router(identity_router)
app.include_router(agent_library_router)
app.include_router(owner_agents_router)
app.include_router(captcha_templates_router)


@app.get("/api/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "UP", "service": "sarae-live-studio-api", "version": settings.app_version}

app.include_router(tiktok_products_router)

app.include_router(tiktok_catalog_router)
app.include_router(android_agents_router)
app.include_router(storage_admin_router)
app.include_router(media_clips_router)
app.include_router(media_library_router)
app.include_router(media_library_products_router)
app.include_router(agent_media_router)
