"""SQLite persistence for the offline studio, with atomic numbered migrations."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import StudioError, timestamp


MIGRATIONS = (
    (1, (
        """CREATE TABLE products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
            description TEXT NOT NULL DEFAULT '',
            price REAL CHECK(price IS NULL OR price >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE media (
            id TEXT PRIMARY KEY,
            product_id TEXT REFERENCES products(id) ON DELETE SET NULL,
            original_name TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            storage_mode TEXT NOT NULL DEFAULT 'managed' CHECK(storage_mode = 'managed'),
            mime_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX media_product_id ON media(product_id)",
    )),
    (2, (
        """CREATE TABLE presenters (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 200),
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE media_presenters (
            media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            presenter_id TEXT NOT NULL REFERENCES presenters(id) ON DELETE CASCADE,
            PRIMARY KEY (media_id, presenter_id)
        ) WITHOUT ROWID""",
        "CREATE INDEX media_presenters_presenter_id ON media_presenters(presenter_id)",
    )),
    # Products come from TikTok. Rows created by hand before this version keep
    # NULL TikTok IDs; they stay usable and are never matched by name.
    (3, (
        "ALTER TABLE products ADD COLUMN tiktok_seller_id TEXT",
        "ALTER TABLE products ADD COLUMN tiktok_product_id TEXT",
        "ALTER TABLE products ADD COLUMN origin_price REAL CHECK(origin_price IS NULL OR origin_price >= 0)",
        "ALTER TABLE products ADD COLUMN stock_num INTEGER CHECK(stock_num IS NULL OR stock_num >= 0)",
        "ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE products ADD COLUMN image_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE products ADD COLUMN thumbnail TEXT",
        "ALTER TABLE products ADD COLUMN last_synced_at TEXT",
        """CREATE UNIQUE INDEX products_tiktok_identity ON products(tiktok_seller_id, tiktok_product_id)
            WHERE tiktok_product_id IS NOT NULL""",
        """CREATE TABLE tiktok_accounts (
            user_id TEXT PRIMARY KEY,
            nick_name TEXT NOT NULL DEFAULT '',
            user_name TEXT NOT NULL DEFAULT '',
            avatar_url TEXT NOT NULL DEFAULT '',
            product_count INTEGER NOT NULL DEFAULT 0,
            last_synced_at TEXT NOT NULL
        )""",
    )),
    # AI: structured product facts, non-secret settings (API keys live in DPAPI files)
    # and sales scripts per product.
    (4, (
        "ALTER TABLE products ADD COLUMN facts TEXT NOT NULL DEFAULT '{}'",
        """CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE sales_scripts (
            id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200),
            body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 20000),
            source TEXT NOT NULL CHECK(source IN ('ai', 'manual')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        "CREATE INDEX sales_scripts_product_id ON sales_scripts(product_id)",
    )),
)


def data_root(data_dir: Path | None = None) -> Path:
    if data_dir is not None:
        root = Path(data_dir)
    elif os.environ.get("SARAE_STUDIO_DATA_DIR"):
        root = Path(os.environ["SARAE_STUDIO_DATA_DIR"])
    else:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        root = base / "SaraeLiveStudio"
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class StudioStorage:
    """Connection is serialized by StudioService's reentrant lock."""

    def __init__(self, root: Path):
        self.database_path = root / "studio.sqlite3"
        if self.database_path.is_symlink():
            raise StudioError("UNSAFE_PATH", "The studio database must not be a symbolic link.")
        self.connection = sqlite3.connect(self.database_path, timeout=15, isolation_level=None,
                                          check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
            self._migrate()
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _migrate(self) -> None:
        # Avoid executescript(): sqlite3 executescript implicitly commits an open transaction.
        with self.transaction() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            )""")
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            supported = {version for version, _ in MIGRATIONS}
            if applied - supported:
                raise StudioError("STORAGE_ERROR", "This database requires a newer version of Studio.")
            for version, statements in MIGRATIONS:
                if version not in applied:
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                                       (version, timestamp()))

    def close(self) -> None:
        self.connection.close()
