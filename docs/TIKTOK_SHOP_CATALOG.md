# TikTok Shop → Sarae catalog contract (October 2026)

## Source of truth
- TikTok product commercial properties are read from the browser-generated response
  `/api/v1/streamer_desktop/live_product/list`. Windows Agent observes the response;
  it does NOT replay transient signed TikTok URLs or store cookie/token credentials on Cloud.
- Canonical identity: `(OWNER, TikTok seller_id, TikTok product_id)`; values are strings.
- Internal relationships use `products.id` and `tiktok_shops.id`.
- Windows Agent ID is NOT a shop/product identity. A snapshot is scoped to an Agent
  and must be treated as observational data, not as a persistent product catalog.

## Database and migration
- Startup runs `shop_schema.upgrade()` idempotently (new table: `tiktok_shops`;
  additive `products.shop_id,tiktok_product_id,stock_num,origin_price,tiktok_category`
  and `live_sessions.shop_id`). It does not delete pre-existing products/sessions.
- Unique SQLite indexes: `tiktok_shops(owner_id,seller_id)`,
  `products(shop_id,tiktok_product_id)` for linked products.
- Existing legacy products/sessions without shop ID stay unchanged. They require
  deliberate assignment before using shop-scoped LIVE flows; do not infer a shop by name.

## Synchronization
1. Agent's `tiktok.products.sync` scans a fresh network response.
2. `POST /api/tiktok-products/sync` verifies the Agent, complete scan, all IDs and
   rejects duplicate IDs; all product upserts are scoped to its OWNER.
3. `tiktok_catalog.import_snapshot` creates TikTok Shop records as required and
   upserts products by shop + TikTok product ID. Name, image, price, stock, category
   are refreshed. **AI description, product videos and LIVE history are preserved**.
4. It never deletes a product if TikTok removes it from a LIVE console list.
5. `GET /api/tiktok-catalog/shops` lists the OWNER's shops.
   `PUT /api/tiktok-catalog/products/{id}/description` is the only catalog edit API.

## LIVE
- Create a draft with `shop_id` and `agent_id`.
- Every selected / added / switched / resumed product must belong to that shop.
- Newly added products must be ACTIVE with at least one linked, available local
  video on the session Agent. Starting LIVE validates the full lineup again.
- `GET /api/live/sessions/{id}/readiness` compares the session's products with
  the Agent's last TikTok snapshot by seller + product ID, never product name.
- Different shop on the Agent -> `SHOP_MISMATCH`; stale snapshot -> `STALE`.
  Neither situation deletes or deactivates catalog records.
- Session UI shows discrepancies, not quick-create shortcuts.

## Operational verification (not automated yet)
- Fresh database and migrated existing database both start without dropped rows.
- Two Agents syncing the same OWNER/shop/product must yield one product.
- One OWNER, two shops with identical product names remain separate.
- TikTok product renamed: same internal product ID and unchanged description/videos.
- Empty/DOM-only/duplicate-ID payload rejected; previous snapshot untouched.
- Session picker cannot mix shops, cannot select unavailable local video.
- Repeat sync, change Agent login shop, verify `SHOP_MISMATCH`.
- Build Python sidecar + Tauri UI after pull; deploy Backend migrations before
  releasing Agent; then deploy Sarae Web. This branch has not been run against
  a real authenticated Chrome in this tool environment.
