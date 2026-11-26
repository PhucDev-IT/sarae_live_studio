"""Shared helpers for local Studio tests: products only enter Studio through a TikTok snapshot."""
from __future__ import annotations

import itertools

ACCOUNT = {"user_id": "7000000000000000001", "nick_name": "Shop Việt", "user_name": "shopviet", "avatar_url": ""}
SELLER = "7495000000000000001"
_ids = itertools.count(1729000000000000001)


def tiktok_row(name="Áo thun Việt Nam", *, product_id=None, seller_id=SELLER, **fields):
    row = {"seller_id": seller_id, "product_id": str(product_id or next(_ids)), "name": name,
           "price_text": "125.000₫", "origin_price_text": "150.000₫", "stock_num": 12,
           "category_name": "Áo thun", "image_url": ""}
    row.update(fields)
    return row


def create_product(service, name="Áo thun Việt Nam", **fields):
    row = tiktok_row(name, **fields)
    service.import_tiktok_snapshot(ACCOUNT, [row])
    products = service.dispatch("studio.products.list", {})["products"]
    return next(product for product in products if product["tiktok_product_id"] == row["product_id"])
