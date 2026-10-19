"""Additive schema upgrade for TikTok-backed catalogs; never drops legacy data."""
from sqlalchemy import inspect,text
from app.database import engine

def upgrade():
    from app.database import Base
    if not {"products","live_sessions"}.issubset(set(inspect(engine).get_table_names())):
        Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS tiktok_shops (
            id INTEGER PRIMARY KEY,owner_id VARCHAR(64) NOT NULL,
            seller_id VARCHAR(80) NOT NULL,display_name VARCHAR(200) NOT NULL DEFAULT '',
            UNIQUE(owner_id,seller_id))"""))
        for table,columns in {
            "products":{"shop_id":"INTEGER","tiktok_product_id":"VARCHAR(80)",
                        "stock_num":"INTEGER","origin_price":"FLOAT","tiktok_category":"VARCHAR(250)"},
            "live_sessions":{"shop_id":"INTEGER"},
        }.items():
            present={c["name"] for c in inspect(conn).get_columns(table)}
            for name,kind in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))
        conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_tiktok_product_shop
                            ON products(shop_id,tiktok_product_id)
                            WHERE shop_id IS NOT NULL AND tiktok_product_id IS NOT NULL"""))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_products_owner_shop ON products(owner_id,shop_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sessions_owner_shop ON live_sessions(owner_id,shop_id)"))
