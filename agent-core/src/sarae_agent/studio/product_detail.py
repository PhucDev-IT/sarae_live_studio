"""TikTok product detail parser adapted from sarae-ai-live-studio/platform_sync.

Reads only the requested product's page data; never persists cookies or raw responses.
"""
from __future__ import annotations
import json
import re
from collections import deque
from typing import Any, Iterator

from .models import StudioError, timestamp

PAGE_JS = r"""
(productId) => {
  const meta = (name) => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || null;
  const images = [...document.images].filter(i => i.naturalWidth >= 200 && i.naturalHeight >= 200).map(i => i.currentSrc || i.src);
  const scripts = productId === null ? [] : [...document.querySelectorAll('script:not([src])')]
    .filter(s => s.textContent.includes(productId)).map(s => ({id: s.id || null, type: s.type || null, text: s.textContent}));
  return {title: document.title, url: location.href, h1: document.querySelector('h1')?.innerText?.trim() || null,
          og_title: meta('og:title'), og_description: meta('og:description') || meta('description'), og_image: meta('og:image'),
          text: (document.body?.innerText || '').slice(0, 30000), images: [...new Set(images)].slice(0, 60), scripts};
}
"""


def parse_vnd(value: Any) -> int | None:
    """"179.000₫" or "₫179,000" gives 179000. For a range, the first price."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    match = re.search(r"\d[\d.,]*", str(value))
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    return int(digits) if digits and int(digits) > 0 else None


TITLE_KEYS = ("title", "product_name", "productName")
DESC_KEYS = ("desc_blocks", "desc_detail", "description", "product_description", "desc")
IMAGE_KEYS = ("images", "main_images", "product_images")
PROP_KEYS = ("sale_properties", "sale_props", "sale_prop_list", "skuProps")
PRICE_PAIRS = (("format_available_price", "format_origin_price"), ("real_price", "original_price"), ("sale_price", "origin_price"))
COLOR_WORDS = ("màu", "mau", "color", "colour")
SIZE_WORDS = ("size", "kích", "kich", "cỡ")


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    stack = deque([node])
    while stack:
        item = stack.popleft()
        if isinstance(item, dict):
            yield item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def _own_data(product_id: str, payloads: list[Any]) -> list[Any]:
    """The parts about this product, biggest first. A page also carries other products (recommendations), which must not leak in."""
    owned = []
    for payload in payloads:
        for node in _walk(payload):
            if (str(node.get("product_id", node.get("productId", ""))) == product_id
                    or str(node.get("id", "")) == product_id and any(k in node for k in TITLE_KEYS + DESC_KEYS)):
                owned.append(_prune_other_products(node, product_id))
    return sorted(owned, key=lambda n: len(json.dumps(n, ensure_ascii=False, default=str)), reverse=True)


def _prune_other_products(node: Any, product_id: str) -> Any:
    if isinstance(node, dict):
        other_id = node.get("product_id", node.get("productId"))
        if other_id is None and any(k in node for k in TITLE_KEYS + DESC_KEYS):
            other_id = node.get("id")
        if other_id is not None and str(other_id) != product_id:
            return {}
        return {k: _prune_other_products(v, product_id) for k, v in node.items()}
    if isinstance(node, list):
        return [_prune_other_products(v, product_id) for v in node]
    return node


def _first_text(nodes: list[Any], keys: tuple[str, ...]) -> Any:
    for root in nodes:
        for node in _walk(root):
            for key in keys:
                if node.get(key) not in (None, "", [], {}):
                    return node[key]
    return None


def _flatten_description(value: Any) -> str | None:
    """A description can be plain text, or a JSON list of text and image blocks."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value.strip() or None
    if isinstance(value, (int, float)):
        return None
    # top-level blocks only: a block's formatting repeats its text further down
    blocks = value if isinstance(value, list) else [value]
    parts = [b["text"].strip() for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"].strip()]
    return "\n".join(parts) or None


def _description_images(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return _image_urls([b["image"] for b in value if isinstance(b, dict) and isinstance(b.get("image"), dict)]) if isinstance(value, list) else []


def _image_urls(value: Any) -> list[str]:
    urls: list[str] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict):
            listed = item.get("url_list") or item.get("urlList") or []
            found = listed[0] if listed else item.get("url")
            if isinstance(found, str):
                urls.append(found)
    return [u for u in dict.fromkeys(urls) if u.startswith("http")]


def _clean_size(value: str | None) -> str | None:
    """"Size M" and "Size M (40 - 51 Kg)" are said and stored as "M"."""
    return re.sub(r"\s*\(.*\)\s*$", "", re.sub(r"^size\s+", "", value, flags=re.IGNORECASE)) if value else value


def _option_values(nodes: list[Any], words: tuple[str, ...]) -> list[str]:
    for root in nodes:
        for node in _walk(root):
            for key in PROP_KEYS:
                for prop in node.get(key) or [] if isinstance(node.get(key), list) else []:
                    if not isinstance(prop, dict):
                        continue
                    name = str(prop.get("prop_name") or prop.get("name") or "").lower()
                    if any(w in name for w in words):
                        values = next((prop[k] for k in ("sale_prop_values", "values", "prop_values") if isinstance(prop.get(k), list)), [])
                        texts = [str(v.get("prop_value") or v.get("value") or v.get("name") or "").strip() if isinstance(v, dict) else str(v).strip()
                                 for v in values]
                        return [t for t in dict.fromkeys(texts) if t]
    return []


def _prices(nodes: list[Any]) -> tuple[int | None, int | None]:
    """(listed, on sale). Only a named pair counts: taking any number called price would read a voucher or another product."""
    for root in nodes:
        for node in _walk(root):
            for sale_key, list_key in PRICE_PAIRS:
                if sale_key in node or list_key in node:
                    sale, listed = parse_vnd(node.get(sale_key)), parse_vnd(node.get(list_key))
                    if sale or listed:
                        return listed or sale, sale if listed and sale and sale < listed else None
    return None, None


def _attributes(nodes: list[Any]) -> list[dict[str, str]]:
    """The seller's form on the platform: material, fit, season and so on."""
    specs = _first_text(nodes, ("specifications",))
    rows = [{"name": str(r.get("name") or "").strip(), "value": str(r.get("value") or "").strip()} for r in specs if isinstance(r, dict)] if isinstance(specs, list) else []
    return [r for r in rows if r["name"] and r["value"]]


def _variants(nodes: list[Any]) -> list[dict[str, Any]]:
    """What can really be bought: a colour shown on the page can have no stock at all."""
    rows = []
    skus = _first_text(nodes, ("skus",))
    for sku in skus if isinstance(skus, list) else []:
        if not isinstance(sku, dict):
            continue
        options = {str(p.get("prop_name") or "").lower(): str(p.get("prop_value") or "").strip() for p in sku.get("sku_sale_props") or [] if isinstance(p, dict)}
        pick = lambda words: next((v for k, v in options.items() if any(w in k for w in words)), None)
        money = sku.get("price") if isinstance(sku.get("price"), dict) else {}
        stock = sku.get("stock")
        rows.append({"color": pick(COLOR_WORDS), "size": _clean_size(pick(SIZE_WORDS)), "stock": stock if isinstance(stock, int) else None,
                     "sale_price": parse_vnd(money.get("sale_price_decimal")), "price": parse_vnd(money.get("origin_price_decimal"))})
    return rows


def _size_guide_image(nodes: list[Any]) -> str | None:
    guide = _first_text(nodes, ("size_guide",))
    urls = _image_urls([guide.get("image")]) if isinstance(guide, dict) else []
    return urls[0] if urls else None


def inline_json(script: dict[str, Any]) -> Any:
    text = (script.get("text") or "").strip()
    for candidate in (text, text[text.find("{"):text.rfind("}") + 1] if "{" in text else ""):
        try:
            return json.loads(candidate) if candidate else None
        except ValueError:
            continue
    return None


def extract_detail(product_id: str, page: dict[str, Any], payloads: list[Any]) -> dict[str, Any]:
    own = _own_data(product_id, payloads)
    title = _first_text(own, TITLE_KEYS)
    raw_description = _first_text(own, DESC_KEYS)
    description = _flatten_description(raw_description)
    seller = _first_text(own, ("seller",))
    variants = _variants(own)
    images = _image_urls(_first_text(own, IMAGE_KEYS))
    price, sale_price = _prices(own)
    record = {"product_id": product_id,
              "title": title if isinstance(title, str) else page.get("h1") or page.get("og_title"),
              "description": description,      # never the page's meta text: it is a search-engine line ("Buy ... on TikTok Shop")
              "price": price, "sale_price": sale_price,
              "images": images,
              "colors": _option_values(own, COLOR_WORDS), "sizes": [_clean_size(s) for s in _option_values(own, SIZE_WORDS)],
              "variants": variants,
              "in_stock_colors": list(dict.fromkeys(v["color"] for v in variants if v["color"] and v["stock"] != 0)),
              "attributes": _attributes(own), "size_guide_image": _size_guide_image(own), "description_images": _description_images(raw_description),
              "seller": seller.get("name") if isinstance(seller, dict) else None, "sold_count": _first_text(own, ("sold_count",)),
              "has_video": bool(_first_text(own, ("header_video", "desc_video")))}
    record["found"] = [k for k in ("title", "description", "price", "sale_price", "images", "colors", "sizes", "variants", "attributes", "size_guide_image") if record[k]]
    # with variants read, a product simply not sold by colour or by size is not something missing
    optional = ("colors", "sizes") if variants else ()
    record["missing"] = [k for k in ("title", "description", "price", "images", "colors", "sizes", "attributes", "size_guide_image") if not record[k] and k not in optional]
    record["from_page_data"] = bool(own)
    # Keep the normalized snapshot small enough for native JSON Lines IPC and the editor.
    for key, limit in (("title", 200), ("description", 10000), ("seller", 200)):
        if isinstance(record.get(key), str):
            record[key] = record[key][:limit]
    for key in ("images", "description_images"):
        record[key] = [url for url in record[key] if len(url) <= 2048][:20]
    record["colors"] = record["colors"][:20]
    record["sizes"] = record["sizes"][:12]
    record["attributes"] = [{"name": row["name"][:100], "value": row["value"][:1000]} for row in record["attributes"][:40]]
    record["variants"] = record["variants"][:100]
    if record["sold_count"] is not None:
        record["sold_count"] = str(record["sold_count"])[:100]
    return record

def read_detail_page(context, product_id: str) -> dict:
    """Run on the existing Chrome worker, in a separate tab so LIVE stays open."""
    from urllib.parse import urlsplit
    if not re.fullmatch(r"[0-9]{1,40}", product_id):
        raise StudioError("VALIDATION_ERROR", "Mã sản phẩm TikTok không hợp lệ.")
    page = context.new_page()
    responses = []
    def listen(response):
        host = urlsplit(response.url).hostname or ""
        if len(responses) < 80 and host.endswith((".tiktok.com", ".tiktokshop.com", ".tiktokglobalshop.com")) and response.status == 200 and "json" in response.headers.get("content-type", ""):
            responses.append(response)
    page.on("response", listen)
    try:
        for _ in range(2):
            responses.clear()
            page.goto(f"https://shop.tiktok.com/view/product/{product_id}?source=liveManager&region=vn", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)
            for step in range(1, 6):
                page.evaluate("fraction => window.scrollTo(0, document.body.scrollHeight * fraction)", step / 5)
                page.wait_for_timeout(500)
            seen = page.evaluate(PAGE_JS, product_id)
            text = (seen.get("text") or "").lower()
            if any(word in text[:2000] for word in ("verify to continue", "drag the puzzle", "security check", "xác minh để tiếp tục", "kéo mảnh ghép")) or page.locator('#captcha_container, #captcha-verify-container, [id^="secsdk-captcha"]').count():
                raise StudioError("TIKTOK_VERIFICATION", "TikTok yêu cầu xác minh. Hãy đăng nhập/xác minh lại trong Chrome của Studio rồi thử lại.")
            if any(word in urlsplit(page.url).path.lower() for word in ("login", "signin", "passport")):
                raise StudioError("TIKTOK_LOGIN_REQUIRED", "Phiên TikTok đã hết hạn. Hãy đăng nhập lại.")
            payloads = []
            kept = 0
            for response in responses:
                try:
                    body = response.text()
                    if product_id in body and len(body) <= 3_000_000 and kept + len(body) <= 9_000_000:
                        kept += len(body)
                        payloads.append(json.loads(body))
                except Exception:
                    continue
            for script in (seen.get("scripts") or [])[:20]:
                size = len(script.get("text") or "")
                if size <= 3_000_000 and kept + size <= 12_000_000:
                    kept += size
                    parsed = inline_json(script)
                    if parsed is not None:
                        payloads.append(parsed)
            record = extract_detail(product_id, seen, payloads)
            record["incomplete"] = not record["from_page_data"] or not any(record.get(k) for k in ("description", "variants", "attributes", "sizes", "colors", "size_guide_image"))
            record["page_url"] = f"https://shop.tiktok.com/view/product/{product_id}"
            record["read_at"] = timestamp()
            if not record["incomplete"]:
                return record
        return record
    finally:
        page.remove_listener("response", listen)
        page.close()
