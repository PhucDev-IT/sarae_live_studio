"""Agent command channel: long-poll, rpc(), events, payload validation, library thumbnails.

Run: python -m unittest discover -s tests -p test_*.py
"""
import base64
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import agent_library, cloud_agents, identity

MID = "a" * 64


class AgentChannelBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.saved = (identity.DB, cloud_agents.DB, agent_library.DB, agent_library.THUMB_DIR)
        identity.DB = root / "identity.sqlite3"
        cloud_agents.DB = root / "agents.sqlite3"
        agent_library.DB = root / "agent_library.sqlite3"
        agent_library.THUMB_DIR = root / "media_thumbs"
        identity.bootstrap_admin("admin@example.test", "AdminSecretPassword123!")
        admin = "Bearer " + identity.login(identity.Login(
            email="admin@example.test", password="AdminSecretPassword123!"))["access_token"]
        owner = identity.create_owner(identity.CreateOwner(
            email="owner@example.test", password="OwnerSecretPassword123!", max_agents=2), authorization=admin)
        self.owner_id = owner["id"]
        key = identity.issue_key(self.owner_id, authorization=admin)["activation_key"]
        device = identity.activate(identity.Activate(activation_key=key, hostname="PC-1"))
        self.agent_id, self.key = device["agent_id"], device["agent_credential"]
        self.owner_token = "Bearer " + identity.login(identity.Login(
            email="owner@example.test", password="OwnerSecretPassword123!"))["access_token"]
        app = FastAPI()
        app.include_router(cloud_agents.router)
        app.include_router(agent_library.router)
        self.client = TestClient(app)
        self.headers = {"X-Sarae-Agent-Key": self.key}

    def tearDown(self):
        identity.DB, cloud_agents.DB, agent_library.DB, agent_library.THUMB_DIR = self.saved
        self.client.close()
        self.tmp.cleanup()

    def heartbeat(self, playback=None):
        response = self.client.post("/api/agents/heartbeat", headers=self.headers, json={
            "agent_id": self.agent_id, "hostname": "PC-1", "playback": playback or {"running": False}})
        self.assertEqual(response.status_code, 200, response.text)

    def poll(self, wait=0):
        response = self.client.get(f"/api/agents/commands/{self.agent_id}/next?wait={wait}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["command"]

    def ack(self, command, success, result):
        response = self.client.post("/api/agents/commands/ack", headers=self.headers, json={
            "agent_id": self.agent_id, "command_id": command["command_id"], "success": success, "result": result})
        self.assertEqual(response.status_code, 200, response.text)

    def fake_agent(self, reply):
        """Background Agent: long-poll once, then ack with reply(command) -> (success, result)."""
        seen = {}

        def run():
            client = TestClient(self.client.app)
            response = client.get(f"/api/agents/commands/{self.agent_id}/next?wait=5", headers=self.headers)
            command = response.json()["command"]
            seen["command"] = command
            if command:
                success, result = reply(command)
                client.post("/api/agents/commands/ack", headers=self.headers, json={
                    "agent_id": self.agent_id, "command_id": command["command_id"],
                    "success": success, "result": result})
            client.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, seen


class LongPollAndRpcTests(AgentChannelBase):
    def test_long_poll_wakes_when_command_is_queued(self):
        self.heartbeat()
        self.assertIsNone(self.poll(0))
        result = {}

        def wait():
            started = time.monotonic()
            result["command"] = self.poll(10)
            result["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.4)
        cloud_agents._enqueue(self.agent_id, "playback_pause", {})
        thread.join(5)
        self.assertEqual(result["command"]["kind"], "playback_pause")
        self.assertLess(result["elapsed"], 3)
        response = self.client.get(f"/api/agents/commands/{self.agent_id}/next?wait=26", headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = self.client.get(f"/api/agents/commands/{self.agent_id}/next?wait=1", headers={"X-Sarae-Agent-Key": "bad"})
        self.assertEqual(response.status_code, 403)

    def test_long_poll_times_out_empty(self):
        started = time.monotonic()
        self.assertIsNone(self.poll(1))
        self.assertGreaterEqual(time.monotonic() - started, 0.9)

    def test_rpc_success_returns_agent_result(self):
        self.heartbeat()
        thread, seen = self.fake_agent(lambda c: (True, json.dumps({"ok": True, "index": c["payload"]["index"]})))
        result = cloud_agents.rpc(self.agent_id, self.owner_id, "playback_select", {"index": 2}, timeout=5)
        thread.join(5)
        self.assertEqual(result, {"ok": True, "index": 2})
        self.assertEqual(seen["command"]["payload"], {"index": 2})

    def test_rpc_offline_409(self):
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_stop", {})
        self.assertEqual(ctx.exception.status_code, 409)
        self.heartbeat()
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        with cloud_agents._db() as db:
            db.execute("UPDATE agent_status SET last_seen=?", (old,))
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_stop", {})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_rpc_timeout_504_cancels_pending_command(self):
        self.heartbeat()
        started = time.monotonic()
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_next", {}, timeout=0.6)
        self.assertEqual(ctx.exception.status_code, 504)
        self.assertLess(time.monotonic() - started, 2)
        # The late Agent must not run a command the caller already gave up on.
        self.assertIsNone(self.poll(0))

    def test_rpc_failure_502_carries_agent_message(self):
        self.heartbeat()
        thread, _ = self.fake_agent(lambda c: (False, "Chưa phát playlist"))
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_pause", {}, timeout=5)
        thread.join(5)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(ctx.exception.detail, "Chưa phát playlist")

    def test_rpc_checks_owner_and_payload(self):
        self.heartbeat()
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, "someone-else", "playback_stop", {})
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_seek", {"ms": -1})
        self.assertEqual(ctx.exception.status_code, 422)

    def test_queue_endpoint_and_password_redaction(self):
        self.heartbeat()
        response = self.client.post("/api/agents/commands", headers={"Authorization": self.owner_token}, json={
            "agent_id": self.agent_id, "kind": "obs_configure",
            "payload": {"host": "127.0.0.1", "port": 4455, "password": "s3cret", "input_name": "Sarae Video",
                        "dual_mode": True, "source_a": "A", "source_b": "B"}})
        self.assertEqual(response.status_code, 200, response.text)
        command = self.poll(0)
        self.assertEqual(command["payload"]["password"], "s3cret")
        with cloud_agents._db() as db:
            stored = json.loads(db.execute("SELECT payload FROM agent_commands").fetchone()[0])
        self.assertEqual(stored["password"], "***")
        self.ack(command, True, json.dumps({"connected": True}))
        listed = self.client.get(f"/api/agents/commands?agent_id={self.agent_id}",
                                 headers={"Authorization": self.owner_token}).json()["commands"]
        self.assertEqual(listed[0]["state"], "completed")
        status = self.client.get("/api/agents/status", headers={"Authorization": self.owner_token}).json()
        self.assertEqual(status["agents"][0]["playback"], {"running": False})


class EventTests(AgentChannelBase):
    def post(self, events):
        return self.client.post("/api/agents/events", headers=self.headers,
                                json={"agent_id": self.agent_id, "events": events})

    def test_events_are_stored_deduplicated_and_popped_once(self):
        switched = {"seq": 11, "type": "PRODUCT_SWITCHED", "session_id": 7, "product_id": 12,
                    "detail": {"trigger": "video_end"}}
        ended = {"seq": 12, "type": "PLAYBACK_ENDED", "session_id": 7, "product_id": 12, "detail": {}}
        self.assertEqual(self.post([ended, switched]).json(), {"accepted": 2, "last_seq": 12})
        self.assertEqual(self.post([switched]).json()["accepted"], 0)
        events = cloud_agents.pop_events(self.agent_id)
        self.assertEqual([e["seq"] for e in events], [11, 12])
        self.assertEqual(events[0]["type"], "PRODUCT_SWITCHED")
        self.assertEqual(events[0]["detail"], {"trigger": "video_end"})
        self.assertIn("created_at", events[0])
        self.assertEqual(cloud_agents.pop_events(self.agent_id), [])

    def test_events_validation_and_auth(self):
        self.assertEqual(self.post([{"seq": 1, "type": "HACK"}]).status_code, 422)
        response = self.client.post("/api/agents/events", headers={"X-Sarae-Agent-Key": "bad"},
                                    json={"agent_id": self.agent_id, "events": []})
        self.assertEqual(response.status_code, 403)


class PayloadValidationTests(unittest.TestCase):
    def bad(self, kind, payload):
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.validate_payload(kind, payload)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_product_play(self):
        good = {"session_id": 7, "product_id": 3, "media_ids": [MID]}
        self.assertEqual(cloud_agents.validate_payload("product_play", good),
                         {**good, "mode": "immediate"})
        self.assertEqual(cloud_agents.validate_payload("product_play", {**good, "mode": "after_current"})["mode"],
                         "after_current")
        self.bad("product_play", {**good, "mode": "later"})
        self.bad("product_play", {**good, "session_id": True})
        self.bad("product_play", {**good, "product_id": 0})
        self.bad("product_play", {**good, "media_ids": []})
        self.bad("product_play", {**good, "media_ids": ["A" * 64]})
        self.bad("product_play", {**good, "media_ids": [MID] * 301})

    def test_select_seek_and_no_param_kinds(self):
        self.assertEqual(cloud_agents.validate_payload("playback_select", {"index": 0}), {"index": 0})
        self.bad("playback_select", {"index": -1})
        self.bad("playback_select", {"index": "1"})
        self.assertEqual(cloud_agents.validate_payload("playback_seek", {"ms": 1500}), {"ms": 1500})
        self.bad("playback_seek", {})
        for kind in ("playback_cancel_pending", "playback_pause", "playback_resume", "playback_next",
                     "playback_previous", "playback_stop", "media_rescan", "obs_pause"):
            self.assertEqual(cloud_agents.validate_payload(kind, {}), {})
            self.bad(kind, {"x": 1})
        self.bad("format_disk", {})

    def test_obs_configure_and_inputs(self):
        out = cloud_agents.validate_payload("obs_configure", {"host": "127.0.0.1", "port": 4455, "input_name": " Sarae "})
        self.assertEqual(out, {"host": "127.0.0.1", "port": 4455, "password": "", "input_name": "Sarae",
                               "dual_mode": False, "source_a": "", "source_b": ""})
        self.bad("obs_configure", {"port": 70000})
        self.bad("obs_configure", {"dual_mode": "yes"})
        self.bad("obs_configure", {"input_name": ""})
        self.assertEqual(cloud_agents.validate_payload("obs_inputs", {"password": None}),
                         {"host": "127.0.0.1", "port": 4455, "password": ""})
        self.bad("obs_inputs", {"password": 5})

    def test_command_body_accepts_new_kinds(self):
        for kind in ("product_play", "playback_seek", "obs_configure", "obs_inputs", "media_rescan"):
            cloud_agents.CommandBody(agent_id="agent_1", kind=kind)
        with self.assertRaises(Exception):
            cloud_agents.CommandBody(agent_id="agent_1", kind="shell")


class LibraryThumbnailTests(AgentChannelBase):
    def jpeg(self):
        import cv2
        import numpy as np
        ok, data = cv2.imencode(".jpg", np.full((90, 160, 3), 128, dtype=np.uint8))
        return data.tobytes()

    def sync(self, videos):
        return self.client.post("/api/agent-library/sync", headers=self.headers,
                                json={"agent_id": self.agent_id, "videos": videos})

    def test_sync_stores_duration_and_serves_thumbnail(self):
        base = {"media_id": MID, "filename": "a.mp4", "size_bytes": 10, "modified_ns": 1}
        other = {"media_id": "b" * 64, "filename": "b.mp4", "size_bytes": 10, "modified_ns": 1}
        reply = self.sync([{**base, "duration_seconds": 12.5,
                            "thumb_jpeg_base64": base64.b64encode(self.jpeg()).decode()}, other])
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["thumbs_missing"], ["b" * 64])
        videos = self.client.get(f"/api/agent-library/{self.agent_id}",
                                 headers={"Authorization": self.owner_token}).json()["videos"]
        first = next(v for v in videos if v["media_id"] == MID)
        self.assertEqual(first["duration_seconds"], 12.5)
        name = first["thumb_url"].rsplit("/", 1)[-1]
        self.assertRegex(name, r"^[0-9a-f]{32}$")
        image = self.client.get(first["thumb_url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        # A plain re-sync (no thumbnail, no duration) keeps both.
        self.sync([base, other])
        videos = self.client.get(f"/api/agent-library/{self.agent_id}",
                                 headers={"Authorization": self.owner_token}).json()["videos"]
        first = next(v for v in videos if v["media_id"] == MID)
        self.assertEqual(first["duration_seconds"], 12.5)
        self.assertTrue(first["thumb_url"].endswith(name))
        # A changed file drops its thumbnail until the Agent sends a new one.
        self.assertIn(MID, self.sync([{**base, "modified_ns": 2}]).json()["thumbs_missing"])
        self.assertEqual(self.client.get(f"/api/agent-library/thumbs/{name}").status_code, 404)

    def test_sync_rejects_bad_thumbnail_and_old_payload_still_works(self):
        base = {"media_id": MID, "filename": "a.mp4", "size_bytes": 10, "modified_ns": 1}
        self.assertEqual(self.sync([{**base, "thumb_jpeg_base64": base64.b64encode(b"GIF89a").decode()}]).status_code, 422)
        self.assertEqual(self.sync([{**base, "thumb_jpeg_base64": "%%%"}]).status_code, 422)
        self.assertEqual(self.sync([base]).json()["synced"], 1)
        self.assertEqual(self.client.get("/api/agent-library/thumbs/../../x").status_code, 404)


if __name__ == "__main__":
    unittest.main()
