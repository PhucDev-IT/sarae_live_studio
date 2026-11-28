"""Where the server keeps its SQLite files (identity.sqlite3, studio_gateway.sqlite3): SARAE_DATA_DIR or backend/data."""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SARAE_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
