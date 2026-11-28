"""What the shop tells its AI once, and the light check on every reply.

- policies: the shop's own words on shipping, returns, inspection on delivery, payment. The AI may state them as facts.
- qa: the shop's ready answers. A short comment that matches exactly ONE entry by its keywords is answered at once with
  the shop's text (no AI call, no wait); every entry also reaches the AI as knowledge for longer comments.
- tidy_reply: viewers do not wait for a reviewer, so a reply is never held for one bad sentence. A sentence that quotes
  a price the data does not have, or promises "shop sẽ kiểm tra / phản hồi", is dropped and the rest goes out. Only a
  reply with nothing left goes to the operator.
"""
from __future__ import annotations

import re
import unicodedata

from .models import StudioError

SETTING_KEY = "shop"
POLICY_FIELDS = ("shipping", "returns", "inspection", "payment", "other")
POLICY_LABELS = {"shipping": "giao hàng, phí ship", "returns": "đổi trả", "inspection": "kiểm hàng khi nhận",
                 "payment": "thanh toán", "other": "khác"}
DEFAULT = {"policies": {field: "" for field in POLICY_FIELDS}, "qa": []}
MAX_POLICY, MAX_QA, MAX_ANSWER, MAX_KEYWORDS = 600, 60, 300, 6
# A matched Q&A answers the whole comment, so only short comments take the instant path.
QA_MAX_WORDS = 12


def fold(text: str) -> str:
    """Lowercase, no accents, words only: 'Kiểm hàng được ko?' -> 'kiem hang duoc ko'."""
    text = unicodedata.normalize("NFD", str(text).casefold().replace("đ", "d"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def validate_shop(params: dict) -> dict:
    if not isinstance(params, dict) or set(params) - {"policies", "qa"}:
        raise StudioError("VALIDATION_ERROR", "Unexpected parameters.")
    policies = params.get("policies", {})
    if not isinstance(policies, dict) or set(policies) - set(POLICY_FIELDS):
        raise StudioError("VALIDATION_ERROR", "Unknown policy.", {"field": "policies"})
    clean_policies = {}
    for field in POLICY_FIELDS:
        value = policies.get(field, "")
        if not isinstance(value, str) or len(value) > MAX_POLICY or "\x00" in value:
            raise StudioError("VALIDATION_ERROR", f"Chính sách {POLICY_LABELS[field]} tối đa {MAX_POLICY} ký tự.",
                              {"field": f"policies.{field}"})
        clean_policies[field] = " ".join(value.split())
    qa = params.get("qa", [])
    if not isinstance(qa, list) or len(qa) > MAX_QA:
        raise StudioError("VALIDATION_ERROR", f"Tối đa {MAX_QA} câu Q&A.", {"field": "qa"})
    clean_qa = []
    for index, item in enumerate(qa):
        if not isinstance(item, dict) or set(item) - {"keywords", "answer"}:
            raise StudioError("VALIDATION_ERROR", "Một câu Q&A không hợp lệ.", {"field": "qa", "index": index})
        keywords, answer = item.get("keywords"), item.get("answer")
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= MAX_KEYWORDS \
                or not all(isinstance(word, str) and fold(word) and len(word) <= 40 for word in keywords):
            raise StudioError("VALIDATION_ERROR", f"Câu Q&A {index + 1}: cần 1 đến {MAX_KEYWORDS} từ khóa.",
                              {"field": "qa", "index": index})
        if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER or "\x00" in answer:
            raise StudioError("VALIDATION_ERROR", f"Câu Q&A {index + 1}: câu trả lời 1 đến {MAX_ANSWER} ký tự.",
                              {"field": "qa", "index": index})
        words = []
        for word in keywords:
            word = " ".join(word.split())
            if fold(word) not in {fold(w) for w in words}:
                words.append(word)
        clean_qa.append({"keywords": words, "answer": " ".join(answer.split())})
    return {"policies": clean_policies, "qa": clean_qa}


def load_shop(service) -> dict:
    try:
        return validate_shop(service.get_setting(SETTING_KEY, DEFAULT))
    except StudioError:
        return {"policies": dict(DEFAULT["policies"]), "qa": []}


def qa_match(text: str, qa: list[dict]) -> dict | None:
    """The one Q&A entry whose keywords all appear in a short comment; None when zero or several match."""
    words = fold(text)
    if not words or len(words.split()) > QA_MAX_WORDS:
        return None
    padded = f" {words} "
    hits = [item for item in qa if all(f" {fold(word)} " in padded for word in item["keywords"])]
    return hits[0] if len(hits) == 1 else None


def shop_prompt_context(shop: dict) -> dict | None:
    policies = {POLICY_LABELS[field]: value for field, value in shop["policies"].items() if value}
    qa = [{"tu_khoa": item["keywords"], "tra_loi": item["answer"]} for item in shop["qa"]]
    if not policies and not qa:
        return None
    return {"chinh_sach": policies or None, "hoi_dap": qa or None}


# --- light check ---------------------------------------------------------------------------
# "109.000đ" (thousands), "1,5tr" (a decimal), "109k"; never the tail of a longer number.
MONEY = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(k|ca|nghin|ngan|dong|d|vnd|tr|trieu)(?![a-z0-9])")
# Promises to check back, said by the shop. Never a fact about checking: "Shop kiểm tra kỹ từng đơn trước khi gửi",
# "bạn sẽ được kiểm tra hàng khi nhận", "nhận hàng mình kiểm tra thoải mái" are inspection policies.
FILLER = re.compile(
    r"\b(?:shop|em|ben em|ben shop)\s+se\s+(?:kiem tra|check|xem lai|bao lai|phan hoi|tra loi)\b"
    r"|\b(?:shop|em|ben em|ben shop)\s+(?:check|xem lai|bao lai|phan hoi lai)\b"
    r"|\b(?:shop|em|ben em|ben shop)\s+kiem tra\s+(?:lai|giup|cho ban|roi bao)\b"
    r"|\bxin phep\s+(?:kiem tra|check|xem lai)\b"
    r"|^(?:da\s+)?se\s+(?:kiem tra|check|xem lai|bao lai|phan hoi)\b"
    r"|\b(?:phan hoi|bao lai|tra loi lai)\s+(?:ngay|sau|lai|ban|som|nhe|nha)\b"
    r"|\bkiem tra\s+(?:lai\s+)?don\b")


def _plain(text: str) -> str:
    """Like fold() but keeps punctuation, so '109.000đ' stays one number."""
    text = unicodedata.normalize("NFD", str(text).casefold().replace("đ", "d").replace("₫", "d"))
    return "".join(char for char in text if not unicodedata.combining(char))


def money_values(text: str) -> set[int]:
    """Amounts of money in đồng: '109k', '109.000đ', '109 nghìn'. '1tr2' is not parsed (rare on LIVE)."""
    values = set()
    for number, unit in MONEY.findall(_plain(text)):
        if re.fullmatch(r"\d+[.,]\d{1,2}", number):  # "1,5tr" = 1.5 million, "2.5k" = 2,500
            amount = float(number.replace(",", "."))
        else:
            amount = float(re.sub(r"[.,]", "", number))
        if unit in ("k", "ca", "nghin", "ngan"):  # "99 cá": sellers' word for thousand
            amount *= 1000
        elif unit in ("tr", "trieu"):
            amount *= 1_000_000
        digits = round(amount)
        if digits >= 1000:
            values.add(digits)
    return values


def allowed_money(*sources) -> set[int]:
    """Every amount the data the AI saw really contains (prices as numbers, money written in shop texts)."""
    values: set[int] = set()
    for source in sources:
        if source is None:
            continue
        if isinstance(source, bool):
            continue
        if isinstance(source, (int, float)):
            values.add(int(source))
        elif isinstance(source, str):
            values |= money_values(source)
        elif isinstance(source, dict):
            values |= allowed_money(*source.values())
        elif isinstance(source, (list, tuple, set)):
            values |= allowed_money(*source)
    return values


def tidy_reply(reply: str, allowed: set[int]) -> tuple[str, list[str]]:
    """Drop the sentences that quote an unknown price or promise to check back; keep the rest."""
    if not reply:
        return reply, []
    kept, dropped = [], []
    for sentence in re.split(r"(?<=[.!?…])\s+", reply):
        if not sentence.strip():
            continue
        if FILLER.search(fold(sentence)) or money_values(sentence) - allowed:
            dropped.append(sentence)
        else:
            kept.append(sentence)
    return " ".join(kept).strip(), dropped
