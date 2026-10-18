"""Per-OWNER data isolation helpers for customer tables (products, presenters, media, LIVE sessions…).

Every customer row carries `owner_id` (the identity user id). Handlers take `owner: Owner` and must
filter every query by it. Rows of another owner are reported as 404 so their existence is not revealed.
"""
from typing import Annotated, TypeVar

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity import current_owner

Owner = Annotated[dict, Depends(current_owner)]
T = TypeVar("T")


def owner_id(owner: dict) -> str:
    return owner["id"]


def owned(db: Session, model: type[T], entity_id: int, owner: dict, message: str = "Không tìm thấy dữ liệu") -> T:
    """Load a row by primary key only if it belongs to the owner, else 404."""
    item = db.get(model, entity_id)
    if item is None or getattr(item, "owner_id", None) != owner["id"]:
        raise HTTPException(404, message)
    return item


def owned_ids(db: Session, model, ids, owner: dict) -> set[int]:
    """Subset of `ids` that exist and belong to the owner."""
    ids = list(ids)
    if not ids:
        return set()
    return set(db.scalars(select(model.id).where(model.id.in_(ids), model.owner_id == owner["id"])).all())
