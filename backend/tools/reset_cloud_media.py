"""Offline, metadata-only Cloud Media reset. Never contacts R2/Drive or Agent files."""
import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def reset(path: Path, apply: bool = False) -> dict[str, int]:
    if not path.is_file():
        raise ValueError("Database does not exist")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        ids = "SELECT id FROM media_items WHERE provider IN ('R2','GOOGLE_DRIVE')"
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        scopes = {
            "product_media": f"media_id IN ({ids})",
            "media_thumbnails": f"media_id IN ({ids})",
            "media_item_presenters": f"media_id IN ({ids})",
            "media_storage_objects": f"media_id IN ({ids})",
            "media_uploads": "provider IN ('R2','GOOGLE_DRIVE')",
            "media_items": "provider IN ('R2','GOOGLE_DRIVE')",
            "storage_owner_containers": "provider IN ('R2','GOOGLE_DRIVE')",
            "storage_credentials": "provider IN ('R2','GOOGLE_DRIVE')",
            "storage_oauth_states": "1=1",
        }
        counts = {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
                  for table, where in scopes.items() if table in tables}
        print(counts)
        if not apply:
            db.rollback()
            return counts
        # Copy a transactionally consistent source before mutation (separate read connection avoids backup lock).
        backup = path.with_name(path.name + ".cloud-reset-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup)) as dest:
            source.backup(dest)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed; reset aborted")
        print(f"Backup: {backup}")
        for table, where in scopes.items():
            if table in counts:
                db.execute(f"DELETE FROM {table} WHERE {where}")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Foreign key check failed; reset rolled back")
        db.commit()
        return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--apply", action="store_true", help="Backend must be stopped. Back up, then reset cloud metadata.")
    args = parser.parse_args()
    reset(args.database.resolve(), args.apply)
