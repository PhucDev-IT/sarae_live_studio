"""Small JSON contract validators shared by the local studio service."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from uuid import UUID


class StudioError(Exception):
    """An actionable, transport-independent studio error."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code.upper()
        self.message = message
        self.details = details


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def identifier(value: object, field: str = "id") -> str:
    if not isinstance(value, str):
        raise StudioError("VALIDATION_ERROR", f"{field} must be a UUID string.", {"field": field})
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise StudioError("VALIDATION_ERROR", f"{field} must be a UUID string.", {"field": field}) from exc
    if str(parsed) != value.lower():
        raise StudioError("VALIDATION_ERROR", f"{field} must be a canonical UUID string.", {"field": field})
    return str(parsed)


def product_fields(params: dict, *, partial: bool = False) -> dict:
    result: dict = {}
    _name_and_description(params, result, partial)
    if not partial or "price" in params:
        price = params.get("price")
        try:
            valid = price is None or (isinstance(price, (float, int)) and not isinstance(price, bool)
                                     and math.isfinite(price) and price >= 0)
        except OverflowError:
            valid = False
        if not valid:
            raise StudioError("VALIDATION_ERROR", "price must be a finite non-negative number or null.",
                              {"field": "price"})
        result["price"] = float(price) if price is not None else None
    return result


def _name_and_description(params: dict, result: dict, partial: bool) -> None:
    if not partial or "name" in params:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or "\x00" in name:
            raise StudioError("VALIDATION_ERROR", "name must contain 1 to 200 characters.", {"field": "name"})
        result["name"] = name.strip()
    if not partial or "description" in params:
        description = params.get("description", "")
        if not isinstance(description, str) or len(description) > 10000 or "\x00" in description:
            raise StudioError("VALIDATION_ERROR", "description must be text of at most 10000 characters.",
                              {"field": "description"})
        result["description"] = description


def presenter_fields(params: dict, *, partial: bool = False) -> dict:
    result: dict = {}
    _name_and_description(params, result, partial)
    if not partial or "active" in params:
        active = params.get("active", True)
        if not isinstance(active, bool):
            raise StudioError("VALIDATION_ERROR", "active must be true or false.", {"field": "active"})
        result["active"] = int(active)
    return result


def identifier_list(value: object, field: str, *, limit: int = 200) -> list[str]:
    if not isinstance(value, list) or len(value) > limit:
        raise StudioError("VALIDATION_ERROR", f"{field} must be a list of at most {limit} UUID strings.",
                          {"field": field})
    return sorted({identifier(item, field) for item in value})


TIKTOK_ID_MAX = 40


def tiktok_id(value: object, field: str) -> str:
    text = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    if not text.isascii() or not text.isdigit() or len(text) > TIKTOK_ID_MAX or int(text) <= 0:
        raise StudioError("TIKTOK_SNAPSHOT_INVALID", f"TikTok {field} is missing or invalid.", {"field": field})
    return text


def tiktok_price(value: object) -> float | None:
    """TikTok sends formatted VND prices ("389.000₫", "₫1,234"); keep the digits."""
    if value is None:
        return None
    digits = "".join(char for char in str(value)[:100] if char.isascii() and char.isdigit())
    return float(digits) if digits else None


def tiktok_text(value: object, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def tiktok_snapshot(account: object, rows: object) -> tuple[dict, list[dict]]:
    """Validate the whole snapshot first, so a bad row never leaves a partial import."""
    if not isinstance(account, dict):
        raise StudioError("TIKTOK_ACCOUNT_UNVERIFIED", "The TikTok account could not be verified.")
    profile = {
        "user_id": tiktok_id(account.get("user_id"), "user_id"),
        "nick_name": tiktok_text(account.get("nick_name"), 200),
        "user_name": tiktok_text(account.get("user_name"), 200),
        "avatar_url": tiktok_text(account.get("avatar_url"), 2000),
    }
    if not isinstance(rows, list) or len(rows) > 1000:
        raise StudioError("TIKTOK_SNAPSHOT_INVALID", "The TikTok product list must contain at most 1000 rows.")
    products, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise StudioError("TIKTOK_SNAPSHOT_INVALID", "A TikTok product row is invalid.")
        key = (tiktok_id(row.get("seller_id"), "seller_id"), tiktok_id(row.get("product_id"), "product_id"))
        if key in seen:
            raise StudioError("TIKTOK_SNAPSHOT_INVALID", "The TikTok product list contains duplicate products.",
                              {"product_id": key[1]})
        seen.add(key)
        name = tiktok_text(row.get("name"), 200)
        if not name:
            raise StudioError("TIKTOK_SNAPSHOT_INVALID", "A TikTok product has no name.", {"product_id": key[1]})
        stock = row.get("stock_num")
        image = tiktok_text(row.get("image_url"), 2000)
        products.append({
            "tiktok_seller_id": key[0], "tiktok_product_id": key[1], "name": name,
            "price": tiktok_price(row.get("price_text")), "origin_price": tiktok_price(row.get("origin_price_text")),
            "stock_num": stock if isinstance(stock, int) and not isinstance(stock, bool) and 0 <= stock < 2**31 else None,
            "category": tiktok_text(row.get("category_name"), 250),
            "image_url": image if image.startswith("https://") else "",
        })
    return profile, products


FACT_KEY = re.compile(r"[a-z][a-z0-9_]{1,30}")


def _clean(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise StudioError("VALIDATION_ERROR", f"{field} must be text.", {"field": field})
    value = " ".join(value.split())
    if len(value) > limit:
        raise StudioError("VALIDATION_ERROR", f"{field} must be at most {limit} characters.", {"field": field})
    return value


def _measure(value: object, field: str, low: float, high: float) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not low <= value <= high:
        raise StudioError("VALIDATION_ERROR", f"{field} must be between {low:g} and {high:g}.", {"field": field})
    return float(value)


def product_facts(value: object) -> dict:
    """Structured facts the AI may state about a product: what is not here is never claimed."""
    if not isinstance(value, dict):
        raise StudioError("VALIDATION_ERROR", "facts must be an object.", {"field": "facts"})
    unknown = set(value) - {"sizes", "colors", "material", "shipping", "notes", "extras"}
    if unknown:
        raise StudioError("VALIDATION_ERROR", "Unknown product facts.", {"fields": sorted(map(str, unknown))})
    sizes = value.get("sizes", [])
    if not isinstance(sizes, list) or len(sizes) > 12:
        raise StudioError("VALIDATION_ERROR", "sizes must list at most 12 rows.", {"field": "sizes"})
    clean_sizes = []
    for row in sizes:
        if not isinstance(row, dict) or set(row) - {"size", "weight_min", "weight_max", "height_min", "height_max", "note"}:
            raise StudioError("VALIDATION_ERROR", "A size row is invalid.", {"field": "sizes"})
        size = _clean(row.get("size", ""), "size", 10).upper()
        if not size:
            raise StudioError("VALIDATION_ERROR", "Each size row needs a size name.", {"field": "sizes"})
        item = {"size": size, "note": _clean(row.get("note", ""), "note", 80)}
        for key, low, high in (("weight_min", 20, 200), ("weight_max", 20, 200), ("height_min", 90, 230), ("height_max", 90, 230)):
            item[key] = _measure(row.get(key), key, low, high)
        for low, high in (("weight_min", "weight_max"), ("height_min", "height_max")):
            if item[low] is not None and item[high] is not None and item[low] > item[high]:
                raise StudioError("VALIDATION_ERROR", f"Size {size}: the range starts after it ends.", {"field": "sizes"})
        clean_sizes.append(item)
    colors = value.get("colors", [])
    if not isinstance(colors, list) or len(colors) > 20:
        raise StudioError("VALIDATION_ERROR", "colors must list at most 20 colors.", {"field": "colors"})
    clean_colors: list[str] = []
    for color in colors:
        color = _clean(color, "colors", 30)
        if color and color.casefold() not in {item.casefold() for item in clean_colors}:
            clean_colors.append(color)
    extras = value.get("extras", {})
    if not isinstance(extras, dict) or len(extras) > 30:
        raise StudioError("VALIDATION_ERROR", "extras must have at most 30 entries.", {"field": "extras"})
    clean_extras = {}
    for key, text in extras.items():
        if not isinstance(key, str) or not FACT_KEY.fullmatch(key):
            raise StudioError("VALIDATION_ERROR", "Extra fact names use lowercase letters, digits and _.",
                              {"field": "extras", "key": str(key)[:40]})
        text = _clean(text, key, 200)
        if text:
            clean_extras[key] = text
    return {"sizes": clean_sizes, "colors": clean_colors,
            "material": _clean(value.get("material", ""), "material", 200),
            "shipping": _clean(value.get("shipping", ""), "shipping", 300),
            "notes": _clean(value.get("notes", ""), "notes", 500), "extras": clean_extras}


EMPTY_FACTS = {"sizes": [], "colors": [], "material": "", "shipping": "", "notes": "", "extras": {}}
