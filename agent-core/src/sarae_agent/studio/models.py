"""Small JSON contract validators shared by the local studio service."""
from __future__ import annotations

import math
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
