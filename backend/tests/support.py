"""Shared fixture for API tests in SaaS mode.

Each test gets a temporary identity DB with one SUPER_ADMIN and two OWNERs (A, B), an in-memory customer DB wired
into every module, and a TestClient that sends OWNER A's token by default. Use `headers=self.B` / `self.ADMIN` /
`self.ANON` to act as someone else.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import identity
from app.catalog import db_session
from app.database import Base
from app.main import app
from app.media import get_db

PASSWORD = "OwnerSecretPassword123!"
# Modules that open sessions with SessionLocal() directly instead of the FastAPI dependency.
SESSION_MODULES = ["app.live", "app.live_sessions", "app.product_videos", "app.local_media"]


class SaasTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.identity_patch = patch.object(identity, "DB", Path(self.tmp.name) / "identity.sqlite3")
        self.identity_patch.start()
        identity.bootstrap_admin("admin@example.test", "AdminSecretPassword123!")
        admin = identity.login(identity.Login(email="admin@example.test", password="AdminSecretPassword123!"))["access_token"]
        self.ADMIN = {"Authorization": "Bearer " + admin}
        self.ANON = {"Authorization": ""}
        self.owner_a = identity.create_owner(identity.CreateOwner(email="a@example.test", password=PASSWORD), authorization=self.ADMIN["Authorization"])
        self.owner_b = identity.create_owner(identity.CreateOwner(email="b@example.test", password=PASSWORD), authorization=self.ADMIN["Authorization"])
        self.A = {"Authorization": "Bearer " + identity.login(identity.Login(email="a@example.test", password=PASSWORD))["access_token"]}
        self.B = {"Authorization": "Bearer " + identity.login(identity.Login(email="b@example.test", password=PASSWORD))["access_token"]}

        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)

        def override():
            with self.Session() as session:
                yield session
        app.dependency_overrides[db_session] = override
        app.dependency_overrides[get_db] = override
        self.session_patches = [patch(f"{module}.SessionLocal", self.Session) for module in SESSION_MODULES]
        for p in self.session_patches:
            p.start()
        self.client = TestClient(app, headers=self.A)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        for p in self.session_patches:
            p.stop()
        self.identity_patch.stop()
        self.engine.dispose()
        self.tmp.cleanup()
