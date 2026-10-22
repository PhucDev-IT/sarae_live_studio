# Sarae Sales Brain — knowledge format (contract)

The AI that answers LIVE viewers does not need to be "trained" per product. Knowledge has three layers:

| Layer | What | Written once for | Example |
|---|---|---|---|
| **Shop policies** | Facts true for the whole shop | the shop | `ship_time` = "2-4 ngày" |
| **Templates** | A kind of question + natural answers with `{placeholders}` | all products (optionally a category) | "Mẫu này {product.price} thôi nha." |
| **Product data** | Price (TikTok), size chart, colors, material, category, extra facts | each product (data only, no sentences) | `material` = "cotton" |

Answer = template + product data (+ shop policies). A new product only needs its data; every template that has the
data it needs works for it at once. Questions no template covers go to the AI; the AI's answer is turned into a new
template (placeholders instead of values), so the next product gets it for free.

Order in which a viewer question is answered: templates (the shop's own first, then the built-in library) →
built-in rules (price, size chart, color, material) as a fallback → answers written for this product only
(Hỏi đáp AI › Kho hỏi đáp) → AI (once per batch).

## 1. Placeholders

Written `{namespace.key}`. A template is used for a product only when **every** placeholder it contains has a value.

| Placeholder | Value (written as digits/short form; the voice reads numbers as words) |
|---|---|
| `{product.name}` | Product name |
| `{product.price}` | Price, e.g. `179k`, `1tr2` |
| `{product.old_price}` | Original price (only when higher than the price) |
| `{product.discount_percent}` | e.g. `20%` (only when there is an original price) |
| `{product.colors}` | e.g. `trắng, đen với be` |
| `{product.sizes}` | e.g. `M, L với XL` |
| `{product.material}` | e.g. `cotton 100%` |
| `{product.stock}` | Stock count from TikTok |
| `{product.category}` | Category name the shop set (e.g. `áo`, `váy`) |
| `{product.<key>}` | Any extra fact of the product (`extras` in product data), e.g. `{product.form}` = `rộng` |
| `{ask.weight}` | Weight in the question, e.g. `55kg` |
| `{ask.height}` | Height in the question, e.g. `160cm` |
| `{ask.color}` | Color named in the question |
| `{ask.size}` | Size named in the question |
| `{calc.size_for_weight}` | Size(s) from the size chart for `{ask.weight}` (and height if given), e.g. `M` or `M hoặc L` |
| `{calc.color_count}` | Number of colors |
| `{shop.<key>}` | A shop policy, e.g. `{shop.ship_time}`, `{shop.return_policy}` |

Keys are lowercase ASCII: `[a-z][a-z0-9_]{1,30}`.

## 2. Conditions (`when`, optional)

Only one per template. When given, the template is used only if it holds.

| `when` | Holds when |
|---|---|
| `asked_color_available` | `{ask.color}` is one of the product's colors |
| `asked_color_unavailable` | a color is asked and the product does not have it |
| `asked_size_available` / `asked_size_unavailable` | same for sizes |
| `in_stock` / `out_of_stock` | product stock > 0 / = 0 |
| `has_discount` | there is an original price higher than the price |

## 3. Intents

`ASK_PRICE, ASK_SIZE, ASK_COLOR, ASK_MATERIAL, ASK_STOCK, ASK_SHIPPING, ASK_RETURN, ASK_PAYMENT, ASK_PROMO,
ASK_CARE, ASK_STYLE, ASK_QUALITY, SHOW_PRODUCT, ORDER, GENERAL`

## 4. JSONL file (import / export / backup)

One JSON object per line, UTF-8. Lines starting with `//` and empty lines are ignored. `type` says what the line is.

```jsonl
{"type":"meta","format":"sarae-knowledge","version":1}
{"type":"policy","key":"ship_time","value":"2-4 ngày","note":"Toàn quốc"}
{"type":"template","id":"size.weight.basic","intent":"ASK_SIZE","category":null,"when":null,"patterns":["{ask.weight} mặc size gì","nặng {ask.weight} mặc gì vừa","{ask.weight} lấy size nào"],"say":["{ask.weight} mặc {calc.size_for_weight} là vừa nha.","Nặng {ask.weight} thì lấy {calc.size_for_weight} nha."],"text":["{ask.weight} lấy {calc.size_for_weight} vừa nha"]}
{"type":"product_facts","product":{"sku":"TK-123"},"facts":{"category":"áo","material":"cotton","colors":["trắng","be"],"sizes":[{"size":"M","weight_min":45,"weight_max":55}],"extras":{"form":"rộng"}}}
{"type":"product_qa","product":{"name":"Áo sơ mi trắng"},"question":"có túi không","intent":"GENERAL","say":["Có một túi ngực nhỏ nha."],"text":["có 1 túi ngực nha"]}
```

### Fields

**template**
- `id` (optional, `[a-z0-9_.-]{3,80}`): same id = update. Missing = new id.
- `intent` (required), `category` (optional; only products of this category use it), `when` (optional).
- `patterns` (1–30): how viewers type the question, short, teencode welcome (`bn`, `sz`, `k`). Placeholders `{ask.*}` mark
  where a value goes. More patterns = more questions matched.
- `say` (1–8): spoken sentences, max 18 words, natural seller voice. No greeting ("Dạ", "Chào"), no viewer name, no
  emoji, numbers as digits.
- `text` (0–8): comment versions, max 90 characters.

**policy**: `key`, `value` (≤300), `note` (optional).

**product_facts** / **product_qa**: `product` identifies the product by `sku`, `tiktok_product_id` or exact `name`
(first that matches). product_facts are merged into the product's data; product_qa adds an answer for that product only.

### Rules checked on import (a line that fails is reported with its line number and skipped)
- Unknown placeholder or namespace → error. `{ask.*}` used in `say`/`text` must appear in at least one pattern.
- Prices written in a template must be placeholders, never a fixed amount.
- Links, phone numbers, bad words → error.
- Over-long or empty sentences → error.

## 5. Tools
- Web: **Hỏi đáp AI** › tabs *Mẫu chung*, *Chính sách shop*, *Sao lưu & nhập*.
- API: `GET /api/ai/knowledge/export` (JSONL), `POST /api/ai/knowledge/import?dry_run=true` (body = JSONL text).
- Command line (from `backend/`):
  - `python -m app.knowledge_cli validate FILE.jsonl` — check a file, no database needed.
  - `python -m app.knowledge_cli import --owner EMAIL FILE.jsonl [--dry-run]`
  - `python -m app.knowledge_cli export --owner EMAIL OUT.jsonl`
