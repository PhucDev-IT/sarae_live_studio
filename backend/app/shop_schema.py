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
        conn.execute(text("""CREATE TABLE IF NOT EXISTS tiktok_accounts (
            id INTEGER PRIMARY KEY,
            user_id VARCHAR(80) NOT NULL UNIQUE,
            tt_uid VARCHAR(80) NOT NULL DEFAULT '',
            user_name VARCHAR(200) NOT NULL DEFAULT '',
            nick_name VARCHAR(200) NOT NULL DEFAULT '',
            user_type INTEGER,
            avatar_url TEXT NOT NULL DEFAULT '',
            last_verified_at VARCHAR(40) NOT NULL DEFAULT ''
        )"""))
        conn.execute(text("""CREATE TABLE IF NOT EXISTS user_tiktok_accounts (
            owner_id VARCHAR(64) NOT NULL,
            account_id INTEGER NOT NULL,
            PRIMARY KEY(owner_id, account_id)
        )"""))
        conn.execute(text("""CREATE TABLE IF NOT EXISTS live_start_claims (
            tiktok_account_id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL UNIQUE,
            agent_id VARCHAR(80) NOT NULL UNIQUE,
            acquired_at VARCHAR(40) NOT NULL
        )"""))
        # Preserve in-progress ownership when upgrading an installation with active LIVE.
        conn.execute(text("""INSERT OR IGNORE INTO live_start_claims
            (tiktok_account_id,session_id,agent_id,acquired_at)
            SELECT tiktok_account_id,id,agent_id,COALESCE(started_at,created_at)
              FROM live_sessions WHERE status='RUNNING'
                AND tiktok_account_id IS NOT NULL AND agent_id IS NOT NULL"""))
        for table,columns in {
            "products":{"shop_id":"INTEGER","tiktok_product_id":"VARCHAR(80)","tiktok_seller_id":"VARCHAR(80)",
                        "stock_num":"INTEGER","origin_price":"FLOAT","tiktok_category":"VARCHAR(250)"},
            "live_sessions":{"shop_id":"INTEGER","tiktok_account_id":"INTEGER"},
        }.items():
            present={c["name"] for c in inspect(conn).get_columns(table)}
            for name,kind in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))
        conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_tiktok_product_shop
                            ON products(shop_id,tiktok_product_id)
                            WHERE shop_id IS NOT NULL AND tiktok_product_id IS NOT NULL"""))
        conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_products_owner_seller_product
                            ON products(owner_id,tiktok_seller_id,tiktok_product_id)
                            WHERE tiktok_seller_id IS NOT NULL AND tiktok_product_id IS NOT NULL"""))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_products_owner_shop ON products(owner_id,shop_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_sessions_owner_shop ON live_sessions(owner_id,shop_id)"))
        # Backstop against duplicate RUNNING records. Pending-start command reservations
        # still require a separate atomic claim before the Agent executes playback.
        conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_live_running_broadcaster
             ON live_sessions(tiktok_account_id)
             WHERE tiktok_account_id IS NOT NULL AND status='RUNNING'"""))
        conn.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_live_running_agent
             ON live_sessions(agent_id)
             WHERE agent_id IS NOT NULL AND status='RUNNING'"""))
