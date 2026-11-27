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
    # LIVE sessions: an ordered product lineup played through OBS, with a playback policy.
    (5, (
        """CREATE TABLE live_sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200),
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'live', 'ended')),
            policy TEXT NOT NULL DEFAULT '{}',
            end_reason TEXT,
            started_at TEXT,
            ended_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE live_session_products (
            session_id TEXT NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position >= 0),
            PRIMARY KEY (session_id, product_id)
        ) WITHOUT ROWID""",
        "CREATE INDEX live_session_products_product ON live_session_products(product_id)",
        """CREATE TABLE live_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES live_sessions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            product_id TEXT,
            detail TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""",
        "CREATE INDEX live_events_session ON live_events(session_id, id)",
    )),
    # Metadata is filled on import or explicit inspection, never during migration/listing.
    (6, (
        "ALTER TABLE media ADD COLUMN duration_seconds REAL CHECK(duration_seconds IS NULL OR duration_seconds > 0)",
        "ALTER TABLE media ADD COLUMN width INTEGER CHECK(width IS NULL OR width > 0)",
        "ALTER TABLE media ADD COLUMN height INTEGER CHECK(height IS NULL OR height > 0)",
        "ALTER TABLE media ADD COLUMN metadata_probed INTEGER NOT NULL DEFAULT 0 CHECK(metadata_probed IN (0, 1))",
    )),
    # Single Video Timeline (dev docs/SINGLE_VIDEO_TIMELINE.md): a session plays videos in order, and products are
    # pinned at time markers of each video. Markers belong to the video, so every session reuses them. The product
    # lineup, rotation policy and sales scripts of earlier Studio builds are dropped (clean app, user decision).
    (7, (
        """CREATE TABLE media_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            at_ms INTEGER NOT NULL CHECK(at_ms >= 0),
            action TEXT NOT NULL CHECK(action IN ('PIN_PRODUCT', 'UNPIN_PRODUCT')),
            product_id TEXT REFERENCES products(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL DEFAULT 0,
            CHECK((action = 'PIN_PRODUCT') = (product_id IS NOT NULL))
        )""",
        "CREATE INDEX media_markers_media ON media_markers(media_id, at_ms, seq)",
        "CREATE INDEX media_markers_product ON media_markers(product_id)",
        "ALTER TABLE live_sessions ADD COLUMN video_ids TEXT NOT NULL DEFAULT '[]'",
        """ALTER TABLE live_sessions ADD COLUMN video_order TEXT NOT NULL DEFAULT 'sequential'
            CHECK(video_order IN ('sequential', 'random', 'shuffle'))""",
        "ALTER TABLE live_sessions ADD COLUMN video_loop INTEGER NOT NULL DEFAULT 1 CHECK(video_loop IN (0, 1))",
        "DROP TABLE live_session_products",
        "DROP TABLE sales_scripts",
        "DELETE FROM settings WHERE key = 'live_automation'",
    )),
    # LIVE overlays (dev docs/LIVE_OVERLAYS.md), kept on the library video like the pin markers. `data` is the
    # validated overlay (studio/overlays.py); overlay images are files in overlay_assets/ named by their id.
    (8, (
        """CREATE TABLE media_overlays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position >= 0),
            data TEXT NOT NULL
        )""",
        "CREATE INDEX media_overlays_media ON media_overlays(media_id, position)",
        """CREATE TABLE overlay_assets (
            id TEXT PRIMARY KEY CHECK(length(id) = 32),
            original_name TEXT NOT NULL,
            mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png', 'image/jpeg', 'image/webp')),
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            width INTEGER NOT NULL CHECK(width > 0),
            height INTEGER NOT NULL CHECK(height > 0),
            size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
            created_at TEXT NOT NULL
        )""",
    )),
    # Overlay schedule per LIVE session (dev LIVE_OVERLAYS.md §13–14: one switch for all the videos of a version):
    # fixed | window | random, with the dev Backend's defaults and limits (studio/overlays.py).
    (9, (
        """ALTER TABLE live_sessions ADD COLUMN overlay_mode TEXT NOT NULL DEFAULT 'fixed'
            CHECK(overlay_mode IN ('fixed', 'window', 'random'))""",
        """ALTER TABLE live_sessions ADD COLUMN overlay_every_ms INTEGER NOT NULL DEFAULT 120000
            CHECK(overlay_every_ms BETWEEN 10000 AND 3600000)""",
        """ALTER TABLE live_sessions ADD COLUMN overlay_min_gap_ms INTEGER NOT NULL DEFAULT 20000
            CHECK(overlay_min_gap_ms BETWEEN 0 AND 600000)""",
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
