"""Payload of the `chat_send` Agent command (docs/AI_AUTO_REPLY.md); the Agent validates it again on its side."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ChatTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg_id: str = Field(default="", max_length=80)
    user_id: str = Field(default="", max_length=80, pattern=r"^[0-9]*$")
    username: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9._]*$")
    nickname: str = Field(default="", max_length=80)
    comment: str = Field(default="", max_length=300)


class ChatSend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply_id: int = Field(ge=1)
    session_id: int = Field(ge=1)
    expected_user_id: str = Field(min_length=1, max_length=80, pattern=r"^[0-9]+$")
    ttl_seconds: float = Field(ge=3, le=300)
    text: str = Field(min_length=1, max_length=150)
    target: ChatTarget
    min_interval_seconds: float = Field(default=8, ge=4, le=120)
    max_per_minute: int = Field(default=5, ge=1, le=20)


def validate_chat_send(payload: dict) -> dict:
    """The payload as the Agent expects it: unknown viewer details are left out, not sent empty."""
    try:
        data = ChatSend.model_validate(payload).model_dump()
        data["target"] = {k: v for k, v in data["target"].items() if v}
        return data
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "payload"
        raise HTTPException(422, f"chat_send {where}: {first.get('msg')}") from None
