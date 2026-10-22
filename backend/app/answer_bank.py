"""Persistent answers per product version: questions answered once are not paid for again, even after a restart.

Entries come from the AI (a checked answer to a real question), from preparation when a product goes on air (pregen),
from the operator during a LIVE (an edited or spoken answer) or from the shop's own Q&A screen (manual).
- AI and pregen answers belong to one product *version* (the fingerprint of price, description and facts): when any
  of these change, they are simply not found any more.
- The shop's answers (operator, manual) are kept across versions (product_version = SHOP) because they are the shop's
  knowledge; each use re-checks their prices against the current product, so a stale price is never said.

Matching a new question, cheapest and safest first:
  1. the same normalized words (teencode expanded, marks removed);
  2. close wording: every number must be identical ("55kg" never matches "65kg"), the same negation, and most
     content words shared. Several phrasings of one question then cost nothing after the first.
"""
import json
import random
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, SessionLocal
from app.reply_rules import normalize

STOP = {"shop", "oi", "a", "vay", "nha", "ne", "the", "nhe", "ban", "minh", "em", "chi", "anh", "cho", "hoi", "voi",
        "di", "ha", "ho", "vay", "nhi", "ak", "a", "ah", "dau", "luon", "z", "ad", "admin", "c", "e"}
SIMILARITY = 0.75
MAX_PER_VERSION = 400
SHOP = "*"  # product_version of the shop's own answers
SHOP_SOURCES = ("operator", "manual")
INTENTS = ("ASK_PRICE", "ASK_SIZE", "ASK_COLOR", "ASK_MATERIAL", "ASK_STOCK", "ASK_SHIPPING", "SHOW_PRODUCT", "ORDER",
           "GENERAL")


class AnswerEntry(Base):
    __tablename__ = "ai_answers"
    __table_args__ = (UniqueConstraint("owner_id", "product_id", "product_version", "question_norm",
                                       name="uq_ai_answers_question"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int] = mapped_column(Integer, index=True)
    product_version: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(String(300))
    question_norm: Mapped[str] = mapped_column(String(300))
    intent: Mapped[str] = mapped_column(String(30))
    slots: Mapped[str] = mapped_column(Text, default="{}")
    say: Mapped[str] = mapped_column(Text, default="[]")
    text: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(20))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def ensure_schema(bind) -> None:
    AnswerEntry.__table__.create(bind=bind, checkfirst=True)


def _words(text: str) -> tuple[set[str], set[str], bool]:
    """(content words, numbers, negated) of a question."""
    tokens = normalize(text).split()
    numbers = {t for t in tokens if any(c.isdigit() for c in t)}
    negated = "khong" in tokens
    return {t for t in tokens if t not in STOP and t not in numbers and t != "khong"}, numbers, negated


def similar(a: str, b: str) -> bool:
    words_a, nums_a, neg_a = _words(a)
    words_b, nums_b, neg_b = _words(b)
    if nums_a != nums_b or neg_a != neg_b or len(words_a) < 2 or len(words_b) < 2:
        return False
    shared = words_a & words_b
    return len(shared) >= 2 and len(shared) / len(words_a | words_b) >= SIMILARITY


def _view(entry: AnswerEntry) -> dict:
    return {"id": entry.id, "question": entry.question, "intent": entry.intent, "slots": json.loads(entry.slots or "{}"),
            "say": json.loads(entry.say or "[]"), "text": json.loads(entry.text or "[]"), "source": entry.source,
            "approved": entry.approved, "uses": entry.uses, "scope": "shop" if entry.product_version == SHOP else "auto",
            "last_used_at": entry.last_used_at.isoformat() if entry.last_used_at else None}


def find(owner_id: str, product_id: int, version: str, question: str, rng: random.Random | None = None,
         allowed: set[int] | None = None) -> dict | None:
    """An answer (one phrasing picked at random) for `question`, or None. Counts the use. With `allowed`, phrasings
    stating another price are left out (the shop's answers may predate a price change)."""
    from app.speech_text import safety_issues
    norm = normalize(question)[:300]
    with SessionLocal() as db:
        rows = db.scalars(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                                    AnswerEntry.product_version.in_((version, SHOP)))).all()
        if allowed is not None:
            rows = [r for r in rows if r.product_version != SHOP or
                    any(not safety_issues(x, allowed) for x in json.loads(r.say or "[]") + json.loads(r.text or "[]"))]
        exact = [r for r in rows if r.question_norm == norm]
        close = exact or [r for r in rows if similar(question, r.question)]
        if not close:
            return None
        best = max(close, key=lambda r: (r.approved, r.source == "operator", r.uses))
        best.uses += 1
        best.last_used_at = datetime.now(timezone.utc)
        db.commit()
        view = _view(best)
    pick = (rng or random).choice
    if allowed is not None:
        view["say"] = [x for x in view["say"] if not safety_issues(x, allowed)]
        view["text"] = [x for x in view["text"] if not safety_issues(x, allowed)]
    if not view["say"] and not view["text"]:
        return None
    return {"bank_id": view["id"], "intent": view["intent"], "slots": view["slots"],
            "say": pick(view["say"]) if view["say"] else "", "text": pick(view["text"]) if view["text"] else "",
            "confidence": 1.0, "needs_review": False, "other_product": False, "approved": view["approved"]}


def save(owner_id: str, product_id: int, version: str, question: str, intent: str, slots: dict, say: list[str],
         text: list[str], source: str, approved: bool = False) -> int | None:
    """Add or update the answer to `question`. The shop's answers are kept across product versions and never
    overwritten by the AI or preparation."""
    norm = normalize(question)[:300]
    if source in SHOP_SOURCES:
        version = SHOP
    say, text = [s for s in say if s][:5], [t for t in text if t][:5]
    if not norm or not (say or text):
        return None
    with SessionLocal() as db:
        entry = db.scalar(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                                    AnswerEntry.product_version == version,
                                                    AnswerEntry.question_norm == norm))
        if entry is None:
            count = len(db.scalars(select(AnswerEntry.id).where(AnswerEntry.owner_id == owner_id,
                                                                AnswerEntry.product_id == product_id,
                                                                AnswerEntry.product_version == version)).all())
            if count >= MAX_PER_VERSION:
                return None
            entry = AnswerEntry(owner_id=owner_id, product_id=product_id, product_version=version, question=question[:300],
                                question_norm=norm, uses=0, approved=False)
            db.add(entry)
        elif entry.source in SHOP_SOURCES and source not in SHOP_SOURCES:
            return entry.id
        entry.intent, entry.slots = intent, json.dumps(slots, ensure_ascii=False, sort_keys=True)
        entry.say, entry.text = json.dumps(say, ensure_ascii=False), json.dumps(text, ensure_ascii=False)
        entry.source, entry.approved = source, approved or entry.approved
        db.commit()
        return entry.id


def approve(owner_id: str, entry_id: int) -> None:
    with SessionLocal() as db:
        entry = db.get(AnswerEntry, entry_id)
        if entry is not None and entry.owner_id == owner_id:
            entry.approved = True
            db.commit()


def has_source(owner_id: str, product_id: int, version: str, source: str) -> int:
    with SessionLocal() as db:
        return len(db.scalars(select(AnswerEntry.id).where(
            AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
            AnswerEntry.product_version == version, AnswerEntry.source == source)).all())


def remove_source(owner_id: str, product_id: int, version: str, source: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                             AnswerEntry.product_version == version, AnswerEntry.source == source))
        db.commit()


def list_for(owner_id: str, product_id: int, version: str) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                                    AnswerEntry.product_version == version)
                          .order_by(AnswerEntry.uses.desc(), AnswerEntry.id)).all()
        return [_view(r) for r in rows]


def delete_entry(owner_id: str, entry_id: int) -> bool:
    with SessionLocal() as db:
        entry = db.get(AnswerEntry, entry_id)
        if entry is None or entry.owner_id != owner_id:
            return False
        db.delete(entry)
        db.commit()
        return True


def shop_context(owner_id: str, product_id: int, limit: int = 12) -> list[dict]:
    """The shop's own Q&A for a product, as context for the AI ({q, a})."""
    with SessionLocal() as db:
        rows = db.scalars(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                                    AnswerEntry.product_version == SHOP)
                          .order_by(AnswerEntry.uses.desc(), AnswerEntry.id).limit(limit)).all()
        return [{"q": r.question, "a": (json.loads(r.say or "[]") or json.loads(r.text or "[]") or [""])[0]} for r in rows]


def list_product(owner_id: str, product_id: int, version: str) -> list[dict]:
    """The shop's answers, then those learned for the current version."""
    with SessionLocal() as db:
        rows = db.scalars(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == product_id,
                                                    AnswerEntry.product_version.in_((version, SHOP)))
                          .order_by(AnswerEntry.uses.desc(), AnswerEntry.id)).all()
        views = [_view(r) for r in rows]
    return [v for v in views if v["scope"] == "shop"] + [v for v in views if v["scope"] == "auto"]


def update_shop(owner_id: str, entry_id: int, question: str, intent: str, say: list[str], text: list[str]) -> dict | None:
    """Edit an answer; it becomes the shop's own (kept across versions, approved)."""
    with SessionLocal() as db:
        entry = db.get(AnswerEntry, entry_id)
        if entry is None or entry.owner_id != owner_id:
            return None
        norm = normalize(question)[:300]
        clash = db.scalar(select(AnswerEntry).where(AnswerEntry.owner_id == owner_id, AnswerEntry.product_id == entry.product_id,
                                                    AnswerEntry.product_version == SHOP, AnswerEntry.question_norm == norm,
                                                    AnswerEntry.id != entry_id))
        if clash is not None:
            db.delete(clash)
            db.flush()
        entry.question, entry.question_norm, entry.intent = question[:300], norm, intent
        entry.say, entry.text = json.dumps(say, ensure_ascii=False), json.dumps(text, ensure_ascii=False)
        entry.product_version, entry.source, entry.approved = SHOP, "manual", True
        db.commit()
        return _view(entry)


def get(owner_id: str, entry_id: int) -> dict | None:
    with SessionLocal() as db:
        entry = db.get(AnswerEntry, entry_id)
        return _view(entry) if entry is not None and entry.owner_id == owner_id else None
