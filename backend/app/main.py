from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.catalog import router as catalog_router
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

app = FastAPI(title=settings.app_name, version=settings.app_version)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
)


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


@app.get("/api/health", tags=["System"])
def health() -> dict[str, str]:
    return {"status": "UP", "service": "sarae-live-studio-api", "version": settings.app_version}
