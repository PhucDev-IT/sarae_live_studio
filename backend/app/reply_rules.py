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


# ----- answers from the product's structured facts (app/product_facts.py) ------------------------------

SIZE_ASK = ("size", "mac gi", "mac sao", "mac vua", "vua khong", "lay size", "mac size", "mac duoc khong", "lay gi")
COLOR_ASK = ("mau",)
MATERIAL_ASK = ("chat gi", "vai gi", "chat lieu", "chat vai", "chat sao", "vai sao", "vai the nao", "chat the nao")


def body_measures(comment: str) -> tuple[float | None, float | None]:
    """(weight kg, height cm) a viewer states: "55kg", "55 ký", "nặng 55", "1m6", "1m65", "160cm", "cao 160"."""
    t = normalize(comment)
    weight = height = None
    m = re.search(r"\b(\d{2,3})(?: (?:kg|ky|ki|can))|\b(\d{2,3})kg\b|\bnang (\d{2,3})\b|\b(\d{2,3}) (?:can)\b", t)
    if m:
        weight = float(next(g for g in m.groups() if g))
    m = re.search(r"\b1m(\d{1,2})\b|\b1 m (\d{1,2})\b|\b(1\d\d) ?cm\b|\bcao (1\d\d)\b", t)
    if m:
        a, b, c, d = m.groups()
        height = 100 + (int(a or b) * (10 if len(a or b) == 1 else 1)) if (a or b) else float(c or d)
    if weight is not None and not 20 <= weight <= 200:
        weight = None
    if height is not None and not 90 <= height <= 230:
        height = None
    return weight, (float(height) if height is not None else None)


def _only(t: str, own: str) -> bool:
    """The comment asks about `own` topic and no other (price, other topics, another product)."""
    if _has(t, OTHER_PRODUCT) or _has(t, PRICE):
        return False
    return not any(_has(t, terms) for name, terms in OTHER_TOPICS.items() if name != own)


def _answer(intent: str, say: str, text: str, slots: dict | None = None) -> dict:
    return {"intent": intent, "slots": slots or {}, "say": say, "text": text, "confidence": 1.0,
            "needs_review": False, "other_product": False}


def _kg(value: float) -> str:
    return f"{value:g}kg"


def size_answer(comment: str, facts: dict) -> dict | None:
    """"55kg mặc size gì" answered from the size chart; None when the chart cannot settle it."""
    t = normalize(comment)
    if not facts.get("sizes") or not (_has(t, SIZE_ASK) or "kg" in t or " ky" in f" {t}") or not _only(t, "ASK_SIZE"):
        return None
    if len(t.split()) > 12:
        return None
    from app.product_facts import sizes_for
    weight, height = body_measures(comment)
    if weight is None:
        return None
    sizes = sizes_for(facts, weight, height)
    slots = {"weight_kg": weight, **({"height_cm": height} if height is not None else {})}
    who = _kg(weight) + (f" cao {int(height)}cm" if height is not None else "")
    if len(sizes) == 1:
        return _answer("ASK_SIZE", f"{who} mặc {sizes[0]} là vừa nha.", f"{who} lấy size {sizes[0]} vừa đẹp nha", slots)
    if len(sizes) == 2:
        return _answer("ASK_SIZE", f"{who} mặc {sizes[0]} hay {sizes[1]} đều được, thích rộng thì lấy {sizes[1]} nha.",
                       f"{who} lấy {sizes[0]} hoặc {sizes[1]}, thích rộng lấy {sizes[1]} nha", slots)
    return None  # outside the chart or ambiguous: the AI (or the operator) answers


def color_answer(comment: str, facts: dict) -> dict | None:
    """"có màu đen không" / "có mấy màu" answered from the color list."""
    colors = facts.get("colors") or []
    t = normalize(comment)
    if not colors or not _has(t, COLOR_ASK) or not _only(t, "ASK_COLOR") or len(t.split()) > 10:
        return None
    asked = [c for c in colors if f" {normalize(c)} " in f" {t} "]
    listing = ", ".join(colors[:-1]) + (f" với {colors[-1]}" if len(colors) > 1 else colors[0])
    if asked:
        return _answer("ASK_COLOR", f"Có màu {asked[0]} nha.", f"có màu {asked[0]} nha", {"color": normalize(asked[0])})
    words = t.split()
    after = words[words.index("mau") + 1:] if "mau" in words else []
    named = [w for w in after if w not in ("gi", "nao", "khong", "ko", "vay", "shop", "nha", "the", "ha", "a", "co", "may")]
    if named:
        # A specific color not in the list: say what there is instead of guessing.
        return _answer("ASK_COLOR", f"Màu đó thì không có, chỉ có {listing} thôi nha.", f"mẫu này có {listing} nha",
                       {"color": " ".join(named[:2])})
    return _answer("ASK_COLOR", f"Mẫu này có {listing} nha.", f"có {listing} nha")


def material_answer(comment: str, facts: dict) -> dict | None:
    """"vải gì vậy" answered from the material; yes/no questions about it ("có xù không") go to the AI."""
    material = facts.get("material") or ""
    t = normalize(comment)
    if not material or not _has(t, MATERIAL_ASK) or _has(t, ("khong",)) or not _only(t, "ASK_MATERIAL"):
        return None
    return _answer("ASK_MATERIAL", f"Chất {material} nha.", f"chất {material} nha")


def fact_answer(comment: str, facts: dict) -> dict | None:
    for rule in (size_answer, color_answer, material_answer):
        found = rule(comment, facts)
        if found:
            return found
    return None
