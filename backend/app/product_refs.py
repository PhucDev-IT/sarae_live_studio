"""Which product a LIVE comment asks about when it names one: "mẫu số 5", "sp 5", "#5" (number in the TikTok LIVE
cart, as the Agent last read the Console) or the product's name / SKU ("quần jean ống suông giá sao").

Only real products of the owner are returned: a cart number is mapped through the Console snapshot to the catalog by
TikTok seller and product id (app/tiktok_products.comparison), a name or SKU must clearly match one catalog product.
Anything unsure returns None, and the question stays about the product on air (or goes to review as before).
Answering about another product never changes what is pinned: the pin timeline always wins.
"""
from __future__ import annotations

import json
import re

from sqlalchemy import select

from app.catalog import Product
from app.reply_rules import normalize

# Words viewers put right before a cart number ("mẫu 5", "sp 5", "link 5", "số 5"), after normalize().
NUMBER_WORDS = {"mau", "sp", "link", "so", "item", "stt"}
# These only with "số" ("cái số 5", "mã số 5"): alone they are everyday words ("mà 2 cái" folds to "ma 2 cai").
NUMBER_WORDS_WITH_SO = {"cai", "bo", "mon", "ma", "san"}
# A number followed by one of these is a measure or a count, not a cart number ("55 kg", "2 màu", "2 cái").
UNITS = {"kg", "ky", "can", "cm", "m", "met", "k", "tr", "trieu", "nghin", "ngan", "tuoi", "phan",
         "mau", "cai", "bo", "chiec", "ao", "quan", "dam", "vay"}
NAME_MATCH = 0.67  # share of the product name's words said in the comment


def cart_number(comment: str) -> int | None:
    """The cart number a comment refers to, if it clearly says one."""
    hashed = re.search(r"#\s*(\d{1,3})\b", comment)
    if hashed:
        return int(hashed.group(1))
    words = normalize(comment).split()
    for i, word in enumerate(words):
        if word not in NUMBER_WORDS and word not in NUMBER_WORDS_WITH_SO:
            continue
        j = i + 1
        has_so = j < len(words) and words[j] in ("so", "pham")  # "mẫu số 5", "sản phẩm 5"
        if has_so:
            j += 1
        elif word in NUMBER_WORDS_WITH_SO:
            continue
        if j < len(words) and words[j].isdigit() and len(words[j]) <= 3:
            if j + 1 < len(words) and words[j + 1] in UNITS:
                continue
            number = int(words[j])
            return number if number > 0 else None
    return None


def cart_rows(owner_id: str, agent_id: str | None) -> list[dict]:
    """The LIVE cart as the session's Agent last read it, each row with its Sarae product (or None)."""
    from app.tiktok_products import comparison, connection
    if not agent_id:
        return []
    with connection() as db:
        snap = db.execute("SELECT products_json FROM snapshots WHERE agent_id=? AND owner_id=?", (agent_id, owner_id)).fetchone()
    if snap is None:
        return []
    rows = comparison(owner_id, json.loads(snap["products_json"]))["products"]
    # The Console numbers the cart from 1; older snapshots without a position follow the list order.
    return [{**row, "position": row.get("position") or i + 1} for i, row in enumerate(rows)]


def by_name(db, owner_id: str, comment: str, lineup: list[int]) -> Product | None:
    """The one product whose name (or SKU) the comment clearly says; the session's products win a tie with others."""
    from app.timeline_ai.pipeline import lexical_score
    products = db.scalars(select(Product).where(Product.owner_id == owner_id, Product.status == "ACTIVE")).all()
    scored = sorted(((lexical_score(comment, {"name": p.name, "sku": p.sku or ""}), p.id in lineup, p) for p in products),
                    key=lambda x: (x[0], x[1]), reverse=True)
    if not scored or scored[0][0] < NAME_MATCH:
        return None
    best = scored[0]
    if len(scored) > 1 and (scored[1][0], scored[1][1]) == (best[0], best[1]):
        return None  # two products match as well: do not guess
    return best[2]


def short_name(name: str, words: int = 5) -> str:
    parts = re.sub(r"\([^)]*\)", " ", name).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def resolve(db, owner_id: str, session, comment: str) -> dict | None:
    """{"product": Product, "label": how to call it when answering, "how": "cart_number" | "name"} or None."""
    number = cart_number(comment)
    if number is not None:
        row = next((r for r in cart_rows(owner_id, session.agent_id) if r["position"] == number), None)
        product = db.get(Product, row["sarae_product_id"]) if row and row.get("sarae_product_id") else None
        if product is not None and product.owner_id == owner_id and product.status == "ACTIVE":
            return {"product": product, "label": f"Mẫu số {number}", "how": "cart_number"}
        return None  # a number we cannot map: never answer about the wrong product
    from app.live_sessions import ids_for
    product = by_name(db, owner_id, comment, ids_for(db, session.id))
    if product is not None:
        return {"product": product, "label": short_name(product.name), "how": "name"}
    return None
