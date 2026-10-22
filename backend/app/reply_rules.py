"""Answers that need no AI call: chat noise is skipped, a plain price question is answered from the product data.

Conservative on purpose: a rule answers only when the comment asks ONE thing it fully understands. Anything mixed
("giá bn, có size L k"), about another product ("cái lúc nãy") or unclear goes to the AI.
"""
import random
import re

from app.speech_text import fold, price_short

# Teencode -> words, on the folded (no marks) text.
_TEEN = {"bn": "bao nhieu", "bnh": "bao nhieu", "bnhieu": "bao nhieu", "nhiu": "nhieu", "k": "khong", "ko": "khong",
         "kh": "khong", "hok": "khong", "hong": "khong", "sz": "size", "j": "gi", "z": "vay", "dc": "duoc", "sp": "san pham",
         "ib": "inbox", "a": "anh", "c": "chi", "e": "em", "shp": "shop", "m": "minh"}

NOISE = {"hi", "hii", "hello", "helo", "alo", "chao", "chao shop", "xin chao", "chao ca nha", "hey", "tim", "tha tim",
         "len", "len len", "follow", "fl", "ok", "oke", "okay", "uh", "um", "a", "e", "haha", "hihi", "kk", "kkk", "."}

# Keywords per intent on normalized text. A price-only question must hit PRICE and nothing else.
PRICE = ("bao nhieu", "gia", "nhieu tien", "bao tien", "may tien", "may k", "may lit", "may xi")
OTHER_TOPICS = {
    "ASK_SIZE": ("size", "kg", "ky", "can", "cao", "mac vua", "vua khong", "vua ko", "1m", "eo", "nguc", "mong"),
    "ASK_COLOR": ("mau",),
    "ASK_MATERIAL": ("chat", "vai", "cotton", "thun", "lua", "day", "mong khong", "co gian", "xu"),
    "ASK_SHIPPING": ("ship", "giao", "van chuyen", "cod", "bao lau", "may ngay"),
    "ASK_STOCK": ("con hang", "het hang", "con khong", "con size", "con mau", "may cai", "bao nhieu cai"),
    "ORDER": ("chot", "lay 1", "lay 2", "lay mot", "dat", "len don", "mua"),
    "COMPLAINT": ("loi", "doi tra", "hoan tien", "lua", "te qua", "xau", "bom hang"),
}
# References to something other than the product on air now.
OTHER_PRODUCT = ("luc nay", "mau kia", "cai kia", "mau truoc", "cai truoc", "mau dau", "ao kia", "quan kia", "hoi nay",
                 "vua nay", "mau khac", "cai khac")

PRICE_SAY = ("Mẫu này {p} thôi nha.", "Giá {p} nha.", "{p} nha, mẫu này đang đẹp lắm luôn.", "Mẫu đang lên sóng giá {p} nè.")
PRICE_TEXT = ("{p} nha", "giá {p} nè", "{p} thôi nha", "mẫu này {p} nha")


def normalize(text: str) -> str:
    """Folded, teencode expanded, single spaces ("Giá BN vậy shop???" -> "gia bao nhieu vay shop")."""
    words = re.findall(r"[a-z0-9]+", fold(text))
    return " ".join(_TEEN.get(w, w) for w in words)


def _has(text: str, terms) -> bool:
    padded = f" {text} "
    return any(f" {t} " in padded for t in terms)


def is_noise(comment: str) -> bool:
    """Greetings, hearts, single letters: not a question, nothing to answer."""
    t = normalize(comment)
    return not t or t in NOISE or (len(t) <= 2 and not t.isdigit())


def mentions_other_product(comment: str) -> bool:
    return _has(normalize(comment), OTHER_PRODUCT)


def price_only(comment: str) -> bool:
    """A short question about the price of the product on air and nothing else."""
    t = normalize(comment)
    if not _has(t, PRICE) or len(t.split()) > 8 or _has(t, OTHER_PRODUCT):
        return False
    return not any(_has(t, terms) for terms in OTHER_TOPICS.values())


def price_answer(product: dict, rng: random.Random | None = None) -> dict | None:
    """Spoken and chat reply to a price-only question, straight from the product price (no AI)."""
    if not product.get("price"):
        return None
    pick = (rng or random).choice
    p = price_short(product["price"])
    return {"intent": "ASK_PRICE", "slots": {}, "say": pick(PRICE_SAY).format(p=p), "text": pick(PRICE_TEXT).format(p=p),
            "confidence": 1.0, "needs_review": False, "other_product": False}
