"""The shop's Q&A per product and the AI playground: see how the AI answers, in comment and voice, without a LIVE.

- /api/ai/answers: the answers of one product (the shop's own first, then those learned for its current version).
  The shop adds or edits answers here; they are approved, kept across product versions, reused for the same or
  closely worded questions, and given to the AI as context for related ones (app/answer_bank.py).
- /api/ai/playground: runs comments through the LIVE pipeline (rules, answer bank, AI) for a chosen product and
  returns each reply with its spoken form (intro + body) and comment text. Nothing is spoken, posted or learned.
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import answer_bank
from app.ai_live_batches import answer_comments, public_reply, snapshot
from app.catalog import Product
from app.database import SessionLocal
from app.identity import current_owner
from app.live_reply_director import comment_text, compose_speech, reply_options, route, spoken_text
from app.ownership import Owner
from app.speech_text import allowed_amounts, blocked_words, clip, safety_issues, strip_openers

router = APIRouter(prefix="/api/ai", tags=["AI Q&A and playground"], dependencies=[Depends(current_owner)])


def owned_product(owner: dict, product_id: int) -> Product:
    with SessionLocal() as db:
        product = db.get(Product, product_id)
        if product is None or product.owner_id != owner["id"]:
            raise HTTPException(404, "Không tìm thấy sản phẩm")
        db.expunge(product)
        return product


def product_view(product: Product) -> dict:
    return {"id": product.id, "name": product.name, "price": product.price}


class AnswerBody(BaseModel):
    question: str = Field(min_length=1, max_length=200)
    intent: str = "GENERAL"
    say: list[str] = Field(default_factory=list, max_length=5)
    text: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("intent")
    @classmethod
    def known_intent(cls, value: str) -> str:
        if value not in answer_bank.INTENTS:
            raise ValueError("Loại câu hỏi không hợp lệ")
        return value


class NewAnswer(AnswerBody):
    product_id: int = Field(gt=0)


def clean(body: AnswerBody, product: Product) -> tuple[str, list[str], list[str]]:
    """The shop's answer as it will be used: robotic openers cut, lengths capped, prices and words checked."""
    question = " ".join(body.question.split())
    say = [clip(strip_openers(s), 160) for s in body.say if s.strip()]
    text = [clip(strip_openers(t), 120) for t in body.text if t.strip()]
    if not question:
        raise HTTPException(422, "Thiếu câu hỏi")
    if not say and not text:
        raise HTTPException(422, "Cần ít nhất một câu nói hoặc một câu comment")
    allowed = allowed_amounts(snapshot(product)["product"])
    for line in say + text:
        issues = safety_issues(line, allowed)
        if issues:
            raise HTTPException(422, f"“{line}”: {'; '.join(issues)}")
    if blocked_words(question):
        raise HTTPException(422, "Câu hỏi có từ ngữ không phù hợp")
    return question, say, text


@router.get("/answers")
def list_answers(product_id: int, owner: Owner):
    product = owned_product(owner, product_id)
    version = snapshot(product)["version"]
    return {"product": product_view(product), "answers": answer_bank.list_product(owner["id"], product.id, version)}


@router.post("/answers", status_code=201)
def add_answer(body: NewAnswer, owner: Owner):
    product = owned_product(owner, body.product_id)
    question, say, text = clean(body, product)
    entry_id = answer_bank.save(owner["id"], product.id, answer_bank.SHOP, question, body.intent, {}, say, text,
                                "manual", approved=True)
    if entry_id is None:
        raise HTTPException(409, "Sản phẩm đã có quá nhiều câu trả lời")
    return answer_bank.get(owner["id"], entry_id)


@router.put("/answers/{entry_id}")
def edit_answer(entry_id: int, body: AnswerBody, owner: Owner):
    entry = answer_bank.get(owner["id"], entry_id)
    if entry is None:
        raise HTTPException(404, "Không tìm thấy câu trả lời")
    with SessionLocal() as db:
        from app.answer_bank import AnswerEntry
        product_id = db.get(AnswerEntry, entry_id).product_id
    product = owned_product(owner, product_id)
    question, say, text = clean(body, product)
    return answer_bank.update_shop(owner["id"], entry_id, question, body.intent, say, text)


@router.delete("/answers/{entry_id}")
def remove_answer(entry_id: int, owner: Owner):
    if not answer_bank.delete_entry(owner["id"], entry_id):
        raise HTTPException(404, "Không tìm thấy câu trả lời")
    return {"ok": True}


class PlayComment(BaseModel):
    username: str = Field(default="", max_length=60)
    nickname: str = Field(default="", max_length=60)
    comment: str = Field(min_length=1, max_length=300)


class PlaygroundBody(BaseModel):
    product_id: int = Field(gt=0)
    comments: list[PlayComment] = Field(min_length=1, max_length=20)


@router.post("/playground")
def playground(body: PlaygroundBody, owner: Owner):
    """How the AI would answer these comments about this product on a LIVE (nothing spoken, posted or learned)."""
    product = owned_product(owner, body.product_id)
    if product.status != "ACTIVE":
        raise HTTPException(409, "Sản phẩm đang tắt")
    ctx = snapshot(product)
    options = reply_options(owner["id"])
    now, stamp = time.monotonic(), "playground"
    items = [{"id": i, "username": c.username or f"khach{i}", "nickname": c.nickname or c.username or f"Khách {i}",
              "comment": c.comment, "context": ctx, "asked_at": now, "asked_iso": stamp}
             for i, c in enumerate(body.comments, 1)]
    counter = iter(range(1, 1000))
    replies, batch = answer_comments(owner["id"], ctx, items, options, log_key=f"{owner['id']}:playground", batch_id=0,
                                     next_id=lambda: next(counter), learn=False)
    out = []
    for reply in replies:
        channel, why = route(reply, options)
        intro, body_text = compose_speech(reply, options, product.id) if reply["say"] else (None, "")
        out.append({**public_reply(reply), "route": channel, "note": why, "intro": intro, "body": body_text,
                    "spoken_text": spoken_text(intro, body_text),
                    "comment_text": comment_text(reply) if reply["text"] else ""})
    return {"product": product_view(product), "replies": out, "skipped": batch["skipped"], "usage": batch["usage"],
            "error": batch["error"]}
