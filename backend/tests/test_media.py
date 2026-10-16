import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tempfile import TemporaryDirectory
from pathlib import Path

from app.database import Base
from app.catalog import Product, db_session as catalog_db_session
from app.media import get_db as media_db, router
from app.main import app

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_db():
    with TestingSession() as db:
        yield db


class MediaTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        app.dependency_overrides[media_db] = override_db
        app.dependency_overrides[catalog_db_session] = override_db
        self.temp = TemporaryDirectory()
        self.storage = patch("app.media.MEDIA_DIR", Path(self.temp.name))
        self.ffprobe = patch("app.media.shutil.which", return_value=None)
        self.storage.start()
        self.ffprobe.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        self.storage.stop()
        self.ffprobe.stop()
        self.temp.cleanup()

    def test_upload_metadata_edit_preview_and_delete(self):
        result = self.client.post("/api/media/upload", files={"file": ("demo.mp4", io.BytesIO(b"fake mp4 test data"), "video/mp4")})
        self.assertEqual(result.status_code, 201, result.text)
        item_id = result.json()["id"]
        self.assertEqual(result.json()["filename"], "demo.mp4")
        self.assertEqual(len(self.client.get("/api/media").json()), 1)
        self.assertEqual(self.client.get(f"/api/media/{item_id}/file").status_code, 200)
        changed = self.client.patch(f"/api/media/{item_id}", json={"tags": "demo"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["tags"], "demo")
        self.assertEqual(self.client.delete(f"/api/media/{item_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/media/{item_id}").status_code, 404)

    def test_rejected_files_and_missing_catalog_links(self):
        self.assertEqual(self.client.post("/api/media/upload", files={"file": ("test.txt", b"abc")}).status_code, 415)
        self.assertEqual(self.client.post("/api/media/upload", files={"file": ("test.mp4", b"abc")}, data={"product_id": 12345}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
