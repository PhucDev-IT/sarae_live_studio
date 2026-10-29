"""The reset is scoped, backed up and previewable; no real customer data is touched."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from tools.reset_cloud_media import reset


class ResetTest(unittest.TestCase):
    def test_reset_preserves_business_and_local_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE media_items(id TEXT PRIMARY KEY, provider TEXT);
                    CREATE TABLE product_media(media_id TEXT REFERENCES media_items(id));
                    CREATE TABLE media_uploads(provider TEXT);
                    CREATE TABLE storage_credentials(provider TEXT);
                    CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE agents(id TEXT PRIMARY KEY);
                    CREATE TABLE live_sessions(id INTEGER PRIMARY KEY);
                    CREATE TABLE ai_config(value TEXT);
                    INSERT INTO media_items VALUES ('cloud','R2'),('drive','GOOGLE_DRIVE'),('local','LOCAL_LEGACY');
                    INSERT INTO product_media VALUES ('cloud'),('local');
                    INSERT INTO media_uploads VALUES ('R2'),('LOCAL_LEGACY');
                    INSERT INTO storage_credentials VALUES ('GOOGLE_DRIVE'),('R2');
                    INSERT INTO products VALUES (1,'Keep');
                    INSERT INTO agents VALUES ('agent');
                    INSERT INTO live_sessions VALUES (7);
                    INSERT INTO ai_config VALUES ('Keep AI');
                """)
            counts = reset(path)
            self.assertEqual(counts["media_items"], 2)
            self.assertFalse(list(Path(tmp).glob("*.bak")))
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 3)
            reset(path, apply=True)
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute("SELECT * FROM media_items").fetchall(), [("local", "LOCAL_LEGACY")])
                self.assertEqual(db.execute("SELECT * FROM product_media").fetchall(), [("local",)])
                for table in ("products", "agents", "live_sessions", "ai_config"):
                    self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1)
            with closing(sqlite3.connect(next(Path(tmp).glob("*.bak")))) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 3)
