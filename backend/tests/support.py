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
from app.database import get_db

PASSWORD = "OwnerSecretPassword123!"
# Modules that open sessions with SessionLocal() directly instead of the FastAPI dependency.
SESSION_MODULES = ["app.live", "app.live_sessions", "app.ai_live_batches", "app.tiktok_catalog",
                   "app.tiktok_products", "app.answer_bank", "app.ai_answers", "app.tiktok_chat"]


class SaasTestCase(unittest.TestCase):
    def setUp(self):
        # cloud_agents.connection() leaves SQLite handles open (closed by GC); Windows may still hold the files.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.identity_patch = patch.object(identity, "DB", Path(self.tmp.name) / "identity.sqlite3")
        self.identity_patch.start()
        # SaaS registries live in their own SQLite files; keep them in the temp folder too.
        self.saas_db_patches = [patch(f"app.{module}.DB", Path(self.tmp.name) / f"{module}.sqlite3")
                                for module in ("cloud_agents", "captcha_templates", "tiktok_products")]
        self.saas_db_patches.append(patch("app.captcha_templates.FILES", Path(self.tmp.name) / "captcha_templates"))
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
        # TikTok accounts, LIVE claims and other tables created outside the ORM models.
        from app.shop_schema import upgrade
        upgrade(self.engine)

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

    def heartbeat(self, agent, playback=None, hostname="PC", media_cache=None):
        """The Agent reporting in (online), optionally with its PlaybackEngine status and media cache job."""
        body = {"agent_id": agent["agent_id"], "hostname": hostname, "playback": playback, "media_cache": media_cache}
        response = self.client.post("/api/agents/heartbeat", json=body, headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        self.assertEqual(response.status_code, 200, response.text)

    def tiktok_products(self, owner, *names, seller="5001"):
        """Catalog products as a TikTok Console sync creates them (source seller + TikTok product id); returns ids."""
        from app.catalog import Product
        with self.Session() as db:
            rows = [Product(name=name, sku=f"TK-{seller}-{i}", owner_id=owner["id"], tiktok_seller_id=seller,
                            tiktok_product_id=str(9000 + i)) for i, name in enumerate(names, 1)]
            db.add_all(rows)
            db.commit()
            return [row.id for row in rows]

    def console_sync(self, agent, user_id="7494647113328657914", product_ids=(), user_name="shop.live"):
        """The Agent posting a manual LIVE Console sync: the account logged in (account_info) and the cart products.
        Returns the TikTok Account id the sync links to the Agent's owner."""
        from sqlalchemy import text
        from app.catalog import Product
        with self.Session() as db:
            products = [db.get(Product, pid) for pid in product_ids]
            rows = [{"name": p.name, "seller_id": p.tiktok_seller_id, "product_id": p.tiktok_product_id} for p in products]
        body = {"agent_id": agent["agent_id"], "complete": True, "products": rows or [{"name": "Khác", "seller_id": "1", "product_id": "1"}],
                "account": {"user_id": user_id, "user_name": user_name, "nick_name": user_name, "user_type": 1}}
        response = self.client.post("/api/tiktok-products/sync", json=body, headers={"X-Sarae-Agent-Key": agent["agent_credential"]})
        self.assertEqual(response.status_code, 200, response.text)
        with self.Session() as db:
            return db.execute(text("SELECT id FROM tiktok_accounts WHERE user_id=:u"), {"u": user_id}).scalar_one()

    def library_videos(self, owner, *filenames):
        """Videos already uploaded to the owner's Media Library; returns their ids (32-hex) in the given order."""
        from app.storage_models import MediaItem, new_id
        with self.Session() as db:
            rows = [MediaItem(id=new_id(), owner_id=owner["id"], provider="TEST", provider_file_id=new_id(), filename=name,
                              display_name=name, mime_type="video/mp4", size_bytes=1000, duration_seconds=12.5)
                    for name in filenames]
            db.add_all(rows)
            db.commit()
            return [row.id for row in rows]

    def cache_report(self, owner, session_id, state="READY", only=None, revision=None):
        """What an Agent reports after downloading the session's videos (all of them, or only `only`)."""
        from app.agent_media import session_manifest
        with self.Session() as db:
            items = session_manifest(db, owner["id"], session_id)["items"]
        ready = [{"media_id": i["media_id"], "revision": revision or i["revision"]} for i in items
                 if only is None or i["media_id"] in only]
        return {"session_id": session_id, "state": state, "total": len(items), "ready": ready, "failed": [],
                "bytes_total": sum(i["size_bytes"] for i in items), "bytes_done": sum(i["size_bytes"] for i in items)}

    def link_videos(self, product_id, media_ids, headers=None):
        """Set a product's ordered Media Library videos (what LIVE plays for it)."""
        response = self.client.put(f"/api/products/{product_id}/videos", json={"media_ids": list(media_ids)}, headers=headers or self.A)
        self.assertEqual(response.status_code, 200, response.text)

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

