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
SESSION_MODULES = ["app.live", "app.live_sessions", "app.product_videos", "app.ai_live_batches"]


class SaasTestCase(unittest.TestCase):
    def setUp(self):
        # cloud_agents.connection() leaves SQLite handles open (closed by GC); Windows may still hold the files.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.identity_patch = patch.object(identity, "DB", Path(self.tmp.name) / "identity.sqlite3")
        self.identity_patch.start()
        # SaaS registries live in their own SQLite files; keep them in the temp folder too.
        self.saas_db_patches = [patch(f"app.{module}.DB", Path(self.tmp.name) / f"{module}.sqlite3")
                                for module in ("cloud_agents", "agent_library", "agent_playlists")]
        for p in self.saas_db_patches:
            p.start()
        identity.bootstrap_admin("admin@example.test", "AdminSecretPassword123!")
        admin = identity.login(identity.Login(email="admin@example.test", password="AdminSecretPassword123!"))["access_token"]
        self.ADMIN = {"Authorization": "Bearer " + admin}
        self.ANON = {"Authorization": ""}
        self.owner_a = identity.create_owner(identity.CreateOwner(email="a@example.test", password=PASSWORD, max_agents=3), authorization=self.ADMIN["Authorization"])
        self.owner_b = identity.create_owner(identity.CreateOwner(email="b@example.test", password=PASSWORD, max_agents=3), authorization=self.ADMIN["Authorization"])
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
        for p in self.saas_db_patches:
            p.stop()
        self.identity_patch.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def activate_agent(self, owner, hostname="PC", online=True):
        """Register an Agent for `owner` (as SUPER_ADMIN would) and, if online, record a fresh heartbeat."""
        import json
        from datetime import datetime, timezone
        from app.cloud_agents import connection
        key = identity.issue_key(owner["id"], authorization=self.ADMIN["Authorization"])["activation_key"]
        agent = identity.activate(identity.Activate(activation_key=key, hostname=hostname))
        if online:
            with connection() as db:
                db.execute("INSERT INTO agent_status(agent_id, host, last_seen, status_json) VALUES(?,?,?,?)", (agent["agent_id"], hostname,
                           datetime.now(timezone.utc).isoformat(), json.dumps({"verification": {"state": {"running": False, "status": "idle"}}})))
                db.commit()
        return agent

    def heartbeat(self, agent, playback=None, hostname="PC"):
        """The Agent reporting in (online), optionally with its PlaybackEngine status."""
        body = {"agent_id": agent["agent_id"], "hostname": hostname, "playback": playback}
        response = self.client.post("/api/agents/heartbeat", json=body, headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        self.assertEqual(response.status_code, 200, response.text)

    def sync_videos(self, agent, *filenames):
        """The Agent syncing its library; returns the media ids (64-hex) in the given order."""
        import hashlib
        ids = [hashlib.sha256(name.encode()).hexdigest() for name in filenames]
        videos = [{"media_id": mid, "filename": name, "size_bytes": 1000, "modified_ns": 1, "duration_seconds": 12.5}
                  for mid, name in zip(ids, filenames)]
        response = self.client.post("/api/agent-library/sync", json={"agent_id": agent["agent_id"], "videos": videos},
                                    headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        self.assertEqual(response.status_code, 200, response.text)
        return ids

    def run_agent(self, agent, reply=None):
        """The Agent taking every deliverable command and acking it with reply(command) -> (success, result).
        Returns the commands it processed, in order."""
        import json
        headers = {"X-Sarae-Agent-Key": agent["agent_credential"]}
        processed = []
        while True:
            command = self.client.get(f"/api/agents/commands/{agent['agent_id']}/next", headers=headers).json()["command"]
            if command is None:
                return processed
            success, result = reply(command) if reply else (True, {"ok": True})
            response = self.client.post("/api/agents/commands/ack", headers=headers, json={
                "agent_id": agent["agent_id"], "command_id": command["command_id"], "claim_token": command["claim_token"],
                "success": success, "result": json.dumps(result) if success else str(result)})
            self.assertEqual(response.status_code, 200, response.text)
            processed.append(command)

