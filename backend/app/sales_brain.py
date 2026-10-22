"""Sales brain: reusable answers for every product, without training per product (docs/AI_KNOWLEDGE_FORMAT.md).

Three layers:
- shop policies (kb_policies): facts true for the whole shop ({shop.ship_time}...), written once;
- templates (kb_templates + the built-in library app/knowledge/builtin_vi.jsonl): a kind of question (patterns) and
  natural answers with {placeholders};
- product data (products.ai_facts + TikTok price/stock): values only.
An answer is a template whose placeholders all have a value for the product asked about. A new product works with every
template at once. Questions no template covers go to the AI, whose answer comes back with a generalized template that
is checked and saved (source "ai"), so the next product gets it for free.

Matching a question to a template: both are normalized (marks removed, teencode expanded) and the values a viewer
states are replaced by markers (<w> weight, <h> height, <c> color, <s> size). A template matches when its markers
are the same and most content words are shared. Mixed questions ("giá bn, có size L k") match nothing and go to the AI.

Storage and backup: the database rows plus JSONL import/export (one object per line, app/knowledge_cli.py and
/api/ai/knowledge/*); the files are meant to live in git (folder knowledge/) as the source of truth.
"""
from __future__ import annotations

import json
import random
import re
import threading
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, SessionLocal
from app.identity import current_owner
from app.ownership import Owner
from app.reply_rules import body_measures, mentions_other_product, normalize
from app.speech_text import blocked_words, fold, money_amounts, price_short, safety_issues, strip_openers

BUILTIN_FILE = Path(__file__).resolve().parent / "knowledge" / "builtin_vi.jsonl"
INTENTS = ("ASK_PRICE", "ASK_SIZE", "ASK_COLOR", "ASK_MATERIAL", "ASK_STOCK", "ASK_SHIPPING", "ASK_RETURN", "ASK_PAYMENT",
           "ASK_PROMO", "ASK_CARE", "ASK_STYLE", "ASK_QUALITY", "SHOW_PRODUCT", "ORDER", "GENERAL")
WHEN = ("asked_color_available", "asked_color_unavailable", "asked_size_available", "asked_size_unavailable",
        "in_stock", "out_of_stock", "has_discount")
PRODUCT_KEYS = ("name", "price", "old_price", "discount_percent", "colors", "sizes", "material", "stock", "category")
ASK_KEYS = ("weight", "height", "color", "size")
CALC_KEYS = ("size_for_weight", "color_count")
# Values are read inside template sentences, so each has a shape (docs/AI_KNOWLEDGE_GUIDE.md, policy table).
SUGGESTED_POLICIES = [
    ("ship_time", "Thời gian giao (chỉ ghi khoảng thời gian)", "2-4 ngày"),
    ("ship_fee", "Phí ship (chỉ ghi số tiền)", "30k"),
    ("freeship_min", "Freeship cho đơn từ (chỉ ghi số tiền)", "299k"),
    ("return_policy", "Đổi trả (đọc sau chữ “Shop”)", "cho đổi size trong 7 ngày, còn tem mác là được"),
    ("payment", "Cách thanh toán", "COD, chuyển khoản hoặc ví TikTok"),
    ("cod", "COD (đọc sau “Bên mình”)", "có COD, nhận hàng rồi mới trả tiền"),
    ("promo", "Khuyến mãi (đọc sau “Đang có”)", "giảm 10% khi mua từ 2 món"),
    ("warranty", "Hàng lỗi / bảo hành (đọc sau “Yên tâm nha,”)", "hàng lỗi do shop thì đổi mới trong 7 ngày"),
    ("order_how", "Cách đặt hàng (bắt đầu bằng động từ)", "bấm giỏ hàng góc trái, chọn màu size rồi đặt"),
    ("shop_hours", "Giờ LIVE (đọc sau “Shop lên LIVE”)", "từ 8h tới 11h tối mỗi ngày"),
]
KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")
ID = re.compile(r"[a-z0-9_.-]{3,80}")
PLACEHOLDER = re.compile(r"\{([a-z]+)\.([a-z][a-z0-9_]{0,30})\}")
MATCH_SCORE = 0.6
STOP = {"shop", "oi", "a", "vay", "nha", "ne", "the", "nhe", "ban", "minh", "em", "chi", "anh", "cho", "hoi", "voi", "di",
        "ha", "ho", "nhi", "ak", "ah", "dau", "luon", "ad", "admin", "c", "e", "co", "khong", "duoc", "la", "thi", "nay",
        "do", "kia", "ma", "nua", "roi", "z"}
# Common color words without marks -> how they are written and said.
COLOR_NAMES = {"den": "đen", "trang": "trắng", "be": "be", "kem": "kem", "xanh": "xanh", "xanh than": "xanh than",
               "xanh duong": "xanh dương", "xanh la": "xanh lá", "xanh reu": "xanh rêu", "xanh navy": "xanh navy",
               "do": "đỏ", "do do": "đỏ đô", "hong": "hồng", "vang": "vàng", "nau": "nâu", "xam": "xám", "ghi": "ghi",
               "tim": "tím", "cam": "cam", "bac": "bạc", "ruou vang": "đỏ rượu vang", "navy": "navy", "nude": "nude",
               "cafe": "cà phê", "mint": "xanh mint"}
COLORS = tuple(COLOR_NAMES)
SIZES = ("xxxl", "xxl", "3xl", "2xl", "xl", "xs", "s", "m", "l", "freesize", "free size")
SIZE_LEADS = {"size", "co", "lay", "mac", "con", "sz"}


class KbTemplate(Base):
    __tablename__ = "kb_templates"
    __table_args__ = (UniqueConstraint("owner_id", "tid", name="uq_kb_templates_owner_tid"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    tid: Mapped[str] = mapped_column(String(80))
    intent: Mapped[str] = mapped_column(String(30))
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    when: Mapped[str | None] = mapped_column(String(40), nullable=True)
    patterns: Mapped[str] = mapped_column(Text, default="[]")
    say: Mapped[str] = mapped_column(Text, default="[]")
    text: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(20))
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KbPolicy(Base):
    __tablename__ = "kb_policies"
    __table_args__ = (UniqueConstraint("owner_id", "key", name="uq_kb_policies_owner_key"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(String(200), default="")


def ensure_schema(bind) -> None:
    KbTemplate.__table__.create(bind=bind, checkfirst=True)
    KbPolicy.__table__.create(bind=bind, checkfirst=True)


# ----- validation (shared by the web, import, the CLI and AI learning) ----------------------------------

def _lines(values, limit: int, length: int, name: str, minimum: int = 0, clean: bool = True) -> list[str]:
    if values is None:
        values = []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise ValueError(f"{name} phải là danh sách")
    out = []
    for value in values:
        line = " ".join(str(value).split())
        if clean:
            line = strip_openers(line)
        if not line:
            continue
        if len(line) > length:
            raise ValueError(f"{name}: “{line[:40]}…” dài quá {length} ký tự")
        if line not in out:
            out.append(line)
    if not minimum <= len(out) <= limit:
        raise ValueError(f"{name}: cần {minimum}-{limit} dòng")
    return out


def markers(text: str) -> set[str]:
    return {t for t in question_tokens(text) if t.startswith("<")}


def check_placeholders(line: str, pattern_markers: set[str]) -> None:
    rest = PLACEHOLDER.sub("", line)
    if "{" in rest or "}" in rest:
        raise ValueError(f"“{line[:50]}”: dấu {{ }} sai cú pháp, viết như {{product.price}}")
    for space, key in PLACEHOLDER.findall(line):
        if space == "product":
            continue  # name, price... or any extra fact of the product
        if space == "shop":
            continue
        if space == "ask":
            if key not in ASK_KEYS:
                raise ValueError(f"{{ask.{key}}} không tồn tại (có: {', '.join(ASK_KEYS)})")
            marker = {"weight": "<w>", "height": "<h>", "color": "<c>", "size": "<s>"}[key]
            if marker not in pattern_markers:
                raise ValueError(f"{{ask.{key}}} dùng trong câu trả lời nhưng không câu hỏi mẫu nào có {{ask.{key}}}")
            continue
        if space == "calc":
            if key not in CALC_KEYS:
                raise ValueError(f"{{calc.{key}}} không tồn tại (có: {', '.join(CALC_KEYS)})")
            if key == "size_for_weight" and "<w>" not in pattern_markers:
                raise ValueError("{calc.size_for_weight} cần câu hỏi mẫu có {ask.weight}")
            continue
        raise ValueError(f"Nhóm “{space}” không hợp lệ (dùng product, ask, calc, shop)")


def validate_template(raw: dict) -> dict:
    """A template ready to store, or ValueError with a Vietnamese reason."""
    if not isinstance(raw, dict):
        raise ValueError("Mẫu phải là JSON object")
    tid = raw.get("id")
    if tid is not None and (not isinstance(tid, str) or not ID.fullmatch(tid)):
        raise ValueError("id chỉ gồm chữ thường, số, . _ - (3-80 ký tự)")
    intent = str(raw.get("intent") or "").upper()
    if intent not in INTENTS:
        raise ValueError(f"intent “{raw.get('intent')}” không hợp lệ")
    category = " ".join(str(raw.get("category") or "").split()).casefold() or None
    if category and len(category) > 40:
        raise ValueError("category dài quá 40 ký tự")
    when = raw.get("when") or None
    if when is not None and when not in WHEN:
        raise ValueError(f"when “{when}” không hợp lệ")
    patterns = _lines(raw.get("patterns"), 30, 120, "patterns", 1, clean=False)
    say = _lines(raw.get("say"), 8, 160, "say", 1)
    text = _lines(raw.get("text"), 8, 120, "text")
    pattern_markers = set().union(*(markers(p) for p in patterns))
    for line in say + text:
        check_placeholders(line, pattern_markers)
        plain = PLACEHOLDER.sub(" ", line)
        if money_amounts(plain):
            raise ValueError(f"“{line[:50]}”: không ghi số tiền cố định, dùng {{product.price}} hoặc {{shop.…}}")
        issues = [i for i in safety_issues(plain, set()) if not i.startswith("Số tiền")]
        if issues:
            raise ValueError(f"“{line[:50]}”: {'; '.join(issues)}")
    for pattern in patterns:
        if blocked_words(pattern):
            raise ValueError(f"Câu hỏi mẫu “{pattern[:40]}” có từ ngữ không phù hợp")
    return {"id": tid, "intent": intent, "category": category, "when": when, "patterns": patterns, "say": say, "text": text}


def validate_policy(raw: dict) -> dict:
    key = str(raw.get("key") or "").strip()
    if not KEY.fullmatch(key):
        raise ValueError(f"Tên chính sách “{key}” chỉ gồm chữ thường không dấu, số và _ (vd ship_time)")
    value = " ".join(str(raw.get("value") or "").split())
    if not 1 <= len(value) <= 300:
        raise ValueError(f"{key}: nội dung cần 1-300 ký tự")
    issues = safety_issues(value, set(money_amounts(value)))
    if issues:
        raise ValueError(f"{key}: {'; '.join(issues)}")
    return {"key": key, "value": value, "note": " ".join(str(raw.get("note") or "").split())[:200]}


# ----- the library of an owner (built-in + the shop's own rows, cached) ---------------------------------

_cache: dict[str, tuple[int, list[dict], dict[str, str]]] = {}
_versions: dict[str, int] = {}
_lock = threading.Lock()
_builtin: list[dict] | None = None


def builtin() -> list[dict]:
    """The library shipped with the app; lines that fail validation are skipped (and reported by the CLI)."""
    global _builtin
    if _builtin is None:
        rows = []
        if BUILTIN_FILE.is_file():
            records, _errors = parse_jsonl(BUILTIN_FILE.read_text(encoding="utf-8"))
            rows = [{**data, "source": "builtin", "approved": True, "enabled": True, "uses": 0}
                    for _line, kind, data in records if kind == "template" and data.get("id")]
        _builtin = rows
    return _builtin


def touch(owner_id: str) -> None:
    with _lock:
        _versions[owner_id] = _versions.get(owner_id, 0) + 1


def _row_view(row: KbTemplate) -> dict:
    return {"id": row.tid, "intent": row.intent, "category": row.category, "when": row.when,
            "patterns": json.loads(row.patterns), "say": json.loads(row.say), "text": json.loads(row.text),
            "source": row.source, "approved": row.approved, "enabled": row.enabled, "uses": row.uses}


def library(owner_id: str) -> tuple[list[dict], dict[str, str]]:
    """(templates incl. disabled ones, policies) of an owner; built-in templates the shop edited or switched off are
    replaced by its own row with the same id."""
    with _lock:
        version = _versions.get(owner_id, 0)
        cached = _cache.get(owner_id)
        if cached and cached[0] == version:
            return cached[1], cached[2]
    with SessionLocal() as db:
        own = [_row_view(r) for r in db.scalars(select(KbTemplate).where(KbTemplate.owner_id == owner_id)).all()]
        policies = {p.key: p.value for p in db.scalars(select(KbPolicy).where(KbPolicy.owner_id == owner_id)).all()}
    ids = {t["id"] for t in own}
    templates = own + [dict(t) for t in builtin() if t["id"] not in ids]
    for t in templates:
        t["_norm"] = [question_tokens(p) for p in t["patterns"]]
    with _lock:
        _cache[owner_id] = (version, templates, policies)
    return templates, policies


def policies_of(owner_id: str) -> dict[str, str]:
    return library(owner_id)[1]


# ----- matching -------------------------------------------------------------------------------------------

def _size_words(text: str) -> list[str]:
    """Words without marks but before teencode expansion (where "m" would become "minh"), sizes marked qqs."""
    words = re.findall(r"[a-z0-9]+", fold(text))
    for i, word in enumerate(words):
        if word in SIZES and i and words[i - 1] in SIZE_LEADS:
            words[i] = "qqs"
    return words


def _fold_phrases(text: str) -> str:
    """Normalized question with stated values replaced by markers: "55kg mac size gi" -> "<w> mac size gi"."""
    for name, marker in (("weight", "qqw"), ("height", "qqh"), ("color", "qqc"), ("size", "qqs")):
        text = text.replace("{ask." + name + "}", f" {marker} ")
    t = f" {normalize(' '.join(_size_words(text)))} "
    for _ in range(2):  # overlapping matches ("55kg 1m6")
        t = re.sub(r" \d{2,3} (?:kg|ky|ki|can) | \d{2,3}kg | nang \d{2,3} ", " qqw ", t)
        t = re.sub(r" 1m\d{1,2} | 1 m \d{1,2} | 1\d\d ?cm | cao 1\d\d ", " qqh ", t)
    for color in sorted(COLORS, key=len, reverse=True):
        t = t.replace(f" mau {color} ", " mau qqc ")
    return " ".join(t.split())


def question_tokens(text: str) -> set[str]:
    words = _fold_phrases(text).split()
    marks = {"qqw": "<w>", "qqh": "<h>", "qqc": "<c>", "qqs": "<s>"}
    return {marks.get(w, w) for w in words if w not in STOP}


def score(question: set[str], pattern: set[str]) -> float:
    if {t for t in question if t.startswith("<")} != {t for t in pattern if t.startswith("<")}:
        return 0.0
    words_q = {t for t in question if not t.startswith("<")}
    words_p = {t for t in pattern if not t.startswith("<")}
    if not words_p:
        return 0.0
    shared = words_q & words_p
    if len(shared) < min(2, len(words_p)):
        return 0.0
    return len(shared) / len(words_q | words_p)


def slots(question: str, product: dict) -> dict:
    """Values the viewer states: weight, height, color, size."""
    weight, height = body_measures(question)
    folded = f" {normalize(question)} "
    out: dict = {}
    if weight is not None:
        out["weight"] = weight
    if height is not None:
        out["height"] = height
    # A color counts only after "màu" ("đến" and "đen" are the same word without marks).
    if " mau " in folded:
        colors = (product.get("facts") or {}).get("colors") or []
        named = next((c for c in colors if f" mau {normalize(c)} " in folded), None)
        named = named or next((COLOR_NAMES[c] for c in sorted(COLORS, key=len, reverse=True) if f" mau {c} " in folded),
                              None)
        if named:
            out["color"] = named
    words = re.findall(r"[a-z0-9]+", fold(question))
    for i, word in enumerate(words):
        if word in SIZES and i and words[i - 1] in SIZE_LEADS:
            out["size"] = word.upper()
    return out


def join_vi(values: list[str]) -> str:
    values = [v for v in values if v]
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + f" với {values[-1]}" if values else ""


def variables(product: dict, policies: dict[str, str], asked: dict) -> dict[str, str]:
    """Every placeholder that has a value for this product, shop and question."""
    facts = product.get("facts") or {}
    price, old = product.get("price"), product.get("origin_price")
    v: dict[str, str] = {"product.name": product.get("name") or ""}
    if price:
        v["product.price"] = price_short(price)
    if price and old and old > price:
        v["product.old_price"] = price_short(old)
        v["product.discount_percent"] = f"{round((1 - price / old) * 100)}%"
    if facts.get("colors"):
        v["product.colors"] = join_vi(facts["colors"])
        v["calc.color_count"] = str(len(facts["colors"]))
    if facts.get("sizes"):
        v["product.sizes"] = join_vi([r["size"] for r in facts["sizes"]])
    for key in ("material", "category"):
        if facts.get(key):
            v[f"product.{key}"] = facts[key]
    if product.get("stock") is not None:
        v["product.stock"] = str(product["stock"])
    for key, value in (facts.get("extras") or {}).items():
        v.setdefault(f"product.{key}", value)
    for key, value in policies.items():
        v[f"shop.{key}"] = value
    if "weight" in asked:
        v["ask.weight"] = f"{asked['weight']:g}kg"
        from app.product_facts import sizes_for
        fits = sizes_for(facts, asked["weight"], asked.get("height"))
        if 1 <= len(fits) <= 2:
            v["calc.size_for_weight"] = " hoặc ".join(fits)
    if "height" in asked:
        v["ask.height"] = f"{int(asked['height'])}cm"
    if "color" in asked:
        v["ask.color"] = asked["color"]
    if "size" in asked:
        v["ask.size"] = asked["size"]
    return {k: val for k, val in v.items() if val}


def holds(when: str | None, product: dict, asked: dict) -> bool:
    if not when:
        return True
    facts = product.get("facts") or {}
    colors = [normalize(c) for c in facts.get("colors") or []]
    sizes = [r["size"].upper() for r in facts.get("sizes") or []]
    stock = product.get("stock")
    return {
        "asked_color_available": "color" in asked and normalize(asked["color"]) in colors,
        "asked_color_unavailable": "color" in asked and bool(colors) and normalize(asked["color"]) not in colors,
        "asked_size_available": "size" in asked and asked["size"] in sizes,
        "asked_size_unavailable": "size" in asked and bool(sizes) and asked["size"] not in sizes,
        "in_stock": stock is not None and stock > 0,
        "out_of_stock": stock is not None and stock <= 0,
        "has_discount": bool(product.get("origin_price") and product.get("price")
                             and product["origin_price"] > product["price"]),
    }.get(when, False)


def render(line: str, values: dict[str, str]) -> str | None:
    """`line` with every placeholder filled, or None when one has no value."""
    missing = [f"{a}.{b}" for a, b in PLACEHOLDER.findall(line) if f"{a}.{b}" not in values]
    if missing:
        return None
    return PLACEHOLDER.sub(lambda m: values[f"{m.group(1)}.{m.group(2)}"], line)


def answer(owner_id: str, product: dict, question: str, allowed: set[int],
           rng: random.Random | None = None) -> dict | None:
    """The best template answer for `question` about `product`, or None (then the bank or the AI answers)."""
    if mentions_other_product(question):
        return None
    templates, policies = library(owner_id)
    asked = slots(question, product)
    tokens = question_tokens(question)
    category = ((product.get("facts") or {}).get("category") or "").casefold()
    ranked = []
    for t in templates:
        if not t["enabled"] or (t["category"] and t["category"] != category) or not holds(t["when"], product, asked):
            continue
        best = max((score(tokens, p) for p in t["_norm"]), default=0.0)
        if best >= MATCH_SCORE:
            # The shop's own templates win ties over the built-in library; conditional ones over general ones.
            ranked.append((best, t["source"] != "builtin", bool(t["when"]), t))
    values = variables(product, policies, asked)
    pick = (rng or random).choice
    for _score, _own, _cond, t in sorted(ranked, key=lambda r: r[:3], reverse=True):
        say = [s for s in (render(x, values) for x in t["say"]) if s and not safety_issues(s, allowed)]
        text = [s for s in (render(x, values) for x in t["text"]) if s and not safety_issues(s, allowed)]
        if not say:
            continue
        count_use(owner_id, t)
        return {"intent": t["intent"], "slots": _slot_view(asked), "say": pick(say), "text": pick(text) if text else "",
                "confidence": 1.0, "needs_review": False, "other_product": False,
                "approved": t["approved"], "template_id": t["id"]}
    return None


def _slot_view(asked: dict) -> dict:
    out = {}
    if "weight" in asked:
        out["weight_kg"] = asked["weight"]
    if "height" in asked:
        out["height_cm"] = asked["height"]
    if "color" in asked:
        out["color"] = normalize(asked["color"])
    if "size" in asked:
        out["size"] = asked["size"]
    return out


def count_use(owner_id: str, template: dict) -> None:
    """Uses are counted on the shop's rows only (the built-in library is read-only)."""
    template["uses"] = template.get("uses", 0) + 1
    if template["source"] == "builtin":
        return
    with SessionLocal() as db:
        row = db.scalar(select(KbTemplate).where(KbTemplate.owner_id == owner_id, KbTemplate.tid == template["id"]))
        if row is not None:
            row.uses += 1
            db.commit()


def placeholders_for(product: dict, policies: dict[str, str]) -> list[str]:
    """Placeholders the AI may use for this product when it writes a reusable template."""
    names = [k for k in variables(product, policies, {}) if not k.startswith("product.name")]
    return sorted(set(names) | {"ask.weight", "ask.height", "ask.color", "ask.size", "calc.size_for_weight"})


# ----- writing --------------------------------------------------------------------------------------------

def _new_id(prefix: str, seed: str) -> str:
    return f"{prefix}.{sha256(seed.encode()).hexdigest()[:10]}"


def save_template(owner_id: str, data: dict, source: str, approved: bool = True, enabled: bool = True) -> dict:
    """Insert or update (same id) one of the shop's templates; a built-in id becomes the shop's own copy."""
    tid = data.get("id") or _new_id(source if source != "manual" else "shop", json.dumps(data["patterns"], ensure_ascii=False))
    with SessionLocal() as db:
        row = db.scalar(select(KbTemplate).where(KbTemplate.owner_id == owner_id, KbTemplate.tid == tid))
        if row is None:
            row = KbTemplate(owner_id=owner_id, tid=tid, uses=0)
            db.add(row)
        row.intent, row.category, row.when = data["intent"], data["category"], data["when"]
        row.patterns = json.dumps(data["patterns"], ensure_ascii=False)
        row.say, row.text = json.dumps(data["say"], ensure_ascii=False), json.dumps(data["text"], ensure_ascii=False)
        row.source, row.approved, row.enabled = source, approved, enabled
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        view = _row_view(row)
    touch(owner_id)
    return view


def find_template(owner_id: str, tid: str) -> dict | None:
    return next((t for t in library(owner_id)[0] if t["id"] == tid), None)


def learn(owner_id: str, question: str, intent: str, template: dict | None, product: dict, allowed: set[int]) -> str | None:
    """Save the AI's generalized answer as a template if it is valid and renders for this product. Returns its id."""
    if not isinstance(template, dict) or intent not in INTENTS or intent == "GENERAL":
        return None
    try:
        data = validate_template({"intent": intent, "patterns": [question], "say": template.get("say"),
                                  "text": template.get("text") or []})
    except ValueError:
        return None
    if not any(PLACEHOLDER.search(x) for x in data["say"]):
        return None  # nothing generalized: it would only fit this product
    values = variables(product, policies_of(owner_id), slots(question, product))
    rendered = [render(x, values) for x in data["say"]]
    if not all(rendered) or any(safety_issues(r, allowed) for r in rendered):
        return None
    data["id"] = _new_id("ai", f"{intent}|{' '.join(sorted(question_tokens(question)))}")
    if find_template(owner_id, data["id"]):
        return data["id"]
    return save_template(owner_id, data, "ai", approved=False)["id"]


def save_policies(owner_id: str, items: list[dict], replace: bool) -> None:
    clean = {p["key"]: p for p in (validate_policy(x) for x in items)}
    with SessionLocal() as db:
        rows = {r.key: r for r in db.scalars(select(KbPolicy).where(KbPolicy.owner_id == owner_id)).all()}
        for key, row in rows.items():
            if replace and key not in clean:
                db.delete(row)
        for key, p in clean.items():
            row = rows.get(key) or KbPolicy(owner_id=owner_id, key=key)
            row.value, row.note = p["value"], p["note"]
            db.add(row)
        db.commit()
    touch(owner_id)


# ----- JSONL import / export ------------------------------------------------------------------------------

def parse_jsonl(text: str) -> tuple[list[tuple[int, str, dict]], list[dict]]:
    """[(line, type, cleaned data)] and [{line, error}] for a JSONL knowledge file (no database needed)."""
    records, errors = [], []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
            kind = obj.get("type") if isinstance(obj, dict) else None
            if kind == "meta":
                continue
            if kind == "template":
                records.append((number, kind, validate_template(obj)))
            elif kind == "policy":
                records.append((number, kind, validate_policy(obj)))
            elif kind in ("product_facts", "product_qa"):
                ref = obj.get("product")
                if not isinstance(ref, dict) or not any(ref.get(k) for k in ("sku", "tiktok_product_id", "name")):
                    raise ValueError("product cần sku, tiktok_product_id hoặc name")
                if kind == "product_facts":
                    from app.product_facts import ProductFacts
                    if not isinstance(obj.get("facts"), dict):
                        raise ValueError("facts phải là object")
                    ProductFacts.model_validate(obj["facts"])
                    records.append((number, kind, {"product": ref, "facts": obj["facts"]}))
                else:
                    intent = str(obj.get("intent") or "GENERAL").upper()
                    if intent not in INTENTS:
                        raise ValueError(f"intent “{intent}” không hợp lệ")
                    question = " ".join(str(obj.get("question") or "").split())
                    if not 1 <= len(question) <= 200:
                        raise ValueError("question cần 1-200 ký tự")
                    records.append((number, kind, {"product": ref, "question": question, "intent": intent,
                                                   "say": _lines(obj.get("say"), 5, 160, "say", 1),
                                                   "text": _lines(obj.get("text"), 5, 120, "text")}))
            else:
                raise ValueError(f"type “{kind}” không hợp lệ (template, policy, product_facts, product_qa)")
        except json.JSONDecodeError as exc:
            errors.append({"line": number, "error": f"JSON sai: {exc.msg}"})
        except ValueError as exc:
            errors.append({"line": number, "error": str(exc).splitlines()[0][:300]})
    return records, errors


def _find_product(db, owner_id: str, ref: dict):
    from app.catalog import Product
    for field in ("sku", "tiktok_product_id", "name"):
        value = ref.get(field)
        if value:
            row = db.scalar(select(Product).where(Product.owner_id == owner_id, getattr(Product, field) == str(value)))
            if row is not None:
                return row
    return None


def import_jsonl(owner_id: str, text: str, dry_run: bool) -> dict:
    """Merge a knowledge file into an owner's data: templates and policies by id/key, product facts merged into the
    product, product answers added to its Q&A. Invalid lines are reported and skipped."""
    from app import answer_bank
    from app.ai_live_batches import snapshot
    from app.catalog import Product
    from app.product_facts import ProductFacts, parse as parse_facts
    from app.speech_text import allowed_amounts
    records, errors = parse_jsonl(text)
    added = {"template": 0, "policy": 0, "product_facts": 0, "product_qa": 0}
    updated = dict(added)
    templates, policies = library(owner_id)
    known_ids, known_keys = {t["id"] for t in templates if t["source"] != "builtin"}, set(policies)
    policy_batch = []
    with SessionLocal() as db:
        for number, kind, data in records:
            try:
                if kind == "template":
                    (updated if data["id"] and data["id"] in known_ids else added)["template"] += 1
                    if not dry_run:
                        save_template(owner_id, data, "import")
                elif kind == "policy":
                    (updated if data["key"] in known_keys else added)["policy"] += 1
                    policy_batch.append(data)
                else:
                    product = _find_product(db, owner_id, data["product"])
                    if product is None:
                        raise ValueError(f"không tìm thấy sản phẩm {json.dumps(data['product'], ensure_ascii=False)}")
                    if kind == "product_facts":
                        merged = parse_facts(product.ai_facts)
                        incoming = data["facts"]
                        extras = {**merged.get("extras", {}), **(incoming.get("extras") or {})}
                        merged = ProductFacts.model_validate({**merged, **incoming, "extras": extras}).model_dump()
                        updated["product_facts"] += 1
                        if not dry_run:
                            product.ai_facts = json.dumps(merged, ensure_ascii=False)
                            db.commit()
                    else:
                        allowed = allowed_amounts(snapshot(product)["product"])
                        for line in data["say"] + data["text"]:
                            issues = safety_issues(line, allowed)
                            if issues:
                                raise ValueError(f"“{line[:40]}”: {'; '.join(issues)}")
                        added["product_qa"] += 1
                        if not dry_run:
                            answer_bank.save(owner_id, product.id, answer_bank.SHOP, data["question"], data["intent"]
                                             if data["intent"] in answer_bank.INTENTS else "GENERAL", {}, data["say"],
                                             data["text"], "manual", approved=True)
            except ValueError as exc:
                errors.append({"line": number, "error": str(exc)[:300]})
    if policy_batch and not dry_run:
        save_policies(owner_id, policy_batch, replace=False)
    errors.sort(key=lambda e: e["line"])
    return {"dry_run": dry_run, "added": added, "updated": updated, "skipped": len(errors), "errors": errors[:200]}


def export_jsonl(owner_id: str) -> str:
    """Everything the shop added (not the built-in library): policies, templates, product facts, product answers."""
    from app import answer_bank
    from app.catalog import Product
    from app.product_facts import parse as parse_facts
    lines = [{"type": "meta", "format": "sarae-knowledge", "version": 1,
              "exported_at": datetime.now(timezone.utc).isoformat()}]
    templates, policies = library(owner_id)
    with SessionLocal() as db:
        notes = {p.key: p.note for p in db.scalars(select(KbPolicy).where(KbPolicy.owner_id == owner_id)).all()}
        lines += [{"type": "policy", "key": k, "value": v, "note": notes.get(k, "")} for k, v in sorted(policies.items())]
        lines += [{"type": "template", **{k: t[k] for k in ("id", "intent", "category", "when", "patterns", "say", "text")},
                   "source": t["source"], "enabled": t["enabled"]} for t in templates if t["source"] != "builtin"]
        products = db.scalars(select(Product).where(Product.owner_id == owner_id)).all()
        for p in products:
            ref = {"sku": p.sku, "tiktok_product_id": p.tiktok_product_id, "name": p.name}
            facts = {k: v for k, v in parse_facts(p.ai_facts).items() if v}
            if facts:
                lines.append({"type": "product_facts", "product": ref, "facts": facts})
            for entry in answer_bank.list_product(owner_id, p.id, ""):
                if entry["scope"] == "shop":
                    lines.append({"type": "product_qa", "product": ref, "question": entry["question"],
                                  "intent": entry["intent"], "say": entry["say"], "text": entry["text"]})
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"


# ----- API ------------------------------------------------------------------------------------------------

router = APIRouter(prefix="/api/ai/knowledge", tags=["AI sales brain"], dependencies=[Depends(current_owner)])


def public(t: dict) -> dict:
    return {**{k: v for k, v in t.items() if not k.startswith("_")}, "editable": True,
            "approved": t.get("approved", True), "uses": t.get("uses", 0)}


@router.get("/templates")
def list_templates(owner: Owner, q: str = "", intent: str = "", source: str = ""):
    templates, policies = library(owner["id"])
    term = normalize(q)
    rows = [t for t in templates if (not intent or t["intent"] == intent) and (not source or t["source"] == source)
            and (not term or term in normalize(" ".join(t["patterns"] + t["say"] + t["text"])))]
    rows.sort(key=lambda t: (INTENTS.index(t["intent"]), t["source"] == "builtin", t["id"]))
    return {"templates": [public(t) for t in rows], "intents": list(INTENTS),
            "placeholders": {"product": [*PRODUCT_KEYS, "form", "care", "fit_tip", "origin", "thickness", "stretch"],
                             "ask": list(ASK_KEYS), "calc": list(CALC_KEYS),
                             "shop": sorted(set(policies) | {k for k, _l, _e in SUGGESTED_POLICIES})}}


class TemplateBody(BaseModel):
    intent: str
    category: str | None = None
    when: str | None = None
    patterns: list[str] = Field(default_factory=list)
    say: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)


def _valid(body: TemplateBody, tid: str | None = None) -> dict:
    try:
        return validate_template({**body.model_dump(), "id": tid})
    except ValueError as exc:
        raise HTTPException(422, str(exc).splitlines()[0]) from None


@router.post("/templates", status_code=201)
def add_template(body: TemplateBody, owner: Owner):
    return public(save_template(owner["id"], _valid(body), "manual"))


@router.put("/templates/{tid}")
def edit_template(tid: str, body: TemplateBody, owner: Owner):
    current = find_template(owner["id"], tid)
    if current is None:
        raise HTTPException(404, "Không tìm thấy mẫu")
    return public(save_template(owner["id"], _valid(body, tid), "manual", enabled=current["enabled"]))


class ToggleBody(BaseModel):
    enabled: bool


@router.post("/templates/{tid}/toggle")
def toggle_template(tid: str, body: ToggleBody, owner: Owner):
    current = find_template(owner["id"], tid)
    if current is None:
        raise HTTPException(404, "Không tìm thấy mẫu")
    data = {k: current[k] for k in ("id", "intent", "category", "when", "patterns", "say", "text")}
    return public(save_template(owner["id"], data, current["source"], current["approved"], body.enabled))


@router.post("/templates/{tid}/approve")
def approve_template(tid: str, owner: Owner):
    current = find_template(owner["id"], tid)
    if current is None:
        raise HTTPException(404, "Không tìm thấy mẫu")
    data = {k: current[k] for k in ("id", "intent", "category", "when", "patterns", "say", "text")}
    return public(save_template(owner["id"], data, current["source"], True, current["enabled"]))


@router.delete("/templates/{tid}")
def delete_template(tid: str, owner: Owner):
    with SessionLocal() as db:
        row = db.scalar(select(KbTemplate).where(KbTemplate.owner_id == owner["id"], KbTemplate.tid == tid))
        if row is None:
            if any(t["id"] == tid for t in builtin()):
                raise HTTPException(409, "Mẫu có sẵn không xoá được, hãy tắt nó")
            raise HTTPException(404, "Không tìm thấy mẫu")
        db.delete(row)
        db.commit()
    touch(owner["id"])
    return {"ok": True}


class PreviewBody(TemplateBody):
    product_id: int = Field(gt=0)
    comment: str = Field(default="", max_length=300)


@router.post("/templates/preview")
def preview_template(body: PreviewBody, owner: Owner):
    """How a template reads for a product (and whether a comment would match it), before saving."""
    from app.ai_answers import owned_product
    from app.ai_live_batches import snapshot
    data = _valid(TemplateBody(**body.model_dump(exclude={"product_id", "comment"})))
    product = snapshot(owned_product(owner, body.product_id))["product"]
    asked = slots(body.comment, product) if body.comment else {}
    values = variables(product, policies_of(owner["id"]), asked)
    lines = data["say"] + data["text"]
    missing = sorted({f"{a}.{b}" for line in lines for a, b in PLACEHOLDER.findall(line) if f"{a}.{b}" not in values})
    matched = None
    if body.comment:
        tokens = question_tokens(body.comment)
        matched = max((score(tokens, question_tokens(p)) for p in data["patterns"]), default=0) >= MATCH_SCORE
    return {"say": [r for r in (render(x, values) for x in data["say"]) if r],
            "text": [r for r in (render(x, values) for x in data["text"]) if r], "missing": missing, "matched": matched}


@router.get("/policies")
def get_policies(owner: Owner):
    with SessionLocal() as db:
        rows = db.scalars(select(KbPolicy).where(KbPolicy.owner_id == owner["id"]).order_by(KbPolicy.key)).all()
        items = [{"key": r.key, "value": r.value, "note": r.note} for r in rows]
    return {"policies": items, "suggested": [{"key": k, "label": label, "example": ex} for k, label, ex in SUGGESTED_POLICIES]}


class PoliciesBody(BaseModel):
    policies: list[dict] = Field(default_factory=list, max_length=200)


@router.put("/policies")
def put_policies(body: PoliciesBody, owner: Owner):
    try:
        save_policies(owner["id"], body.policies, replace=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc).splitlines()[0]) from None
    return get_policies(owner)


@router.get("/export")
def export(owner: Owner):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(export_jsonl(owner["id"]), media_type="application/x-ndjson; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="sarae-knowledge-{stamp}.jsonl"'})


@router.post("/import")
async def import_file(request: Request, owner: Owner, dry_run: bool = True):
    raw = await request.body()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "File tối đa 20 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "File phải là UTF-8") from None
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            text = json.loads(text).get("content", "")
        except (ValueError, AttributeError):
            raise HTTPException(422, "Body JSON cần trường content") from None
    return import_jsonl(owner["id"], text, dry_run)
