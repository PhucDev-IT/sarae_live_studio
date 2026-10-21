"""Compatibility stub for Windows Agents released before the Agent-local video library was removed.

Those Agents still POST their local MP4 inventory to /api/agent-library/sync every minute. The Cloud no longer
stores it (videos live in the centralized Media Library), but answering 404 would make them resend about once per
second, and the SaaS guard's 403 would stop their whole Cloud worker. So the request is authenticated, accepted and
dropped. Remove this module once every Agent runs a version without the local library sync.
"""
from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.identity import verify_agent

router = APIRouter(prefix="/api/agent-library", tags=["Agent compatibility"])


class Inventory(BaseModel):
    agent_id: str
    videos: list = []


@router.post("/sync")
def sync(body: Inventory, x_sarae_agent_key: str | None = Header(None)):
    verify_agent(body.agent_id, x_sarae_agent_key)
    return {"accepted": 0, "stored": False, "deprecated": True, "thumbs_missing": []}
