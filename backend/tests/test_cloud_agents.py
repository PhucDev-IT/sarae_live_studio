"""Agent command channel: lifecycle (claim, lease, ack, timeout, idempotency, lanes), long-poll, events, validation,
typed Agent errors (X-Sarae-Error) and the CAPTCHA Telegram alert sent from heartbeats.

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
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import agent_library_compat, cloud_agents, identity

MID = "a" * 32  # a Media Library id


class AgentChannelBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.saved = (identity.DB, cloud_agents.DB)
        identity.DB = root / "identity.sqlite3"
        cloud_agents.DB = root / "agents.sqlite3"
        # Heartbeats reconcile LIVE sessions and their locks: give them an in-memory customer DB with the full schema
        # (never the developer's real database).
        import app.live_sessions  # noqa: F401  (registers the session models)
        from app.database import Base
        from app.shop_schema import upgrade
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        upgrade(self.engine)
        self.session_patch = patch("app.live_sessions.SessionLocal", sessionmaker(bind=self.engine))
        self.session_patch.start()
        identity.bootstrap_admin("admin@example.test", "AdminSecretPassword123!")
        self.admin = admin = "Bearer " + identity.login(identity.Login(
            email="admin@example.test", password="AdminSecretPassword123!"))["access_token"]
        owner = identity.create_owner(identity.CreateOwner(
            email="owner@example.test", password="OwnerSecretPassword123!", max_agents=2), authorization=admin)
        self.owner_id = owner["id"]
        self.activation_key = key = identity.issue_key(self.owner_id, authorization=admin)["activation_key"]
        device = identity.activate(identity.Activate(activation_key=key, hostname="PC-1"))
        self.agent_id, self.key = device["agent_id"], device["agent_credential"]
        self.owner_token = "Bearer " + identity.login(identity.Login(
            email="owner@example.test", password="OwnerSecretPassword123!"))["access_token"]
        app = FastAPI()
        app.include_router(cloud_agents.router)
        app.include_router(agent_library_compat.router)
        app.include_router(identity.router)
        self.client = TestClient(app)
        self.headers = {"X-Sarae-Agent-Key": self.key}

    def tearDown(self):
        identity.DB, cloud_agents.DB = self.saved
        self.session_patch.stop()
        self.engine.dispose()
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

    def ack(self, command, success, result, token=None, expect=200):
        response = self.client.post("/api/agents/commands/ack", headers=self.headers, json={
            "agent_id": self.agent_id, "command_id": command["command_id"], "success": success, "result": result,
            "claim_token": command.get("claim_token", "") if token is None else token})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def create(self, kind, payload=None, **options):
        return cloud_agents.create_command(self.agent_id, self.owner_id, kind, payload, **options)

    def expire(self, column, command_id):
        """Move a command's lease or deadline into the past."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with cloud_agents._db() as db:
            db.execute(f"UPDATE agent_commands SET {column}=? WHERE command_id=?", (past, command_id))

    def status_of(self, command_id):
        return cloud_agents.get_command(command_id, self.agent_id)

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
                    "claim_token": command["claim_token"], "success": success, "result": result})
            client.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, seen


class LongPollTests(AgentChannelBase):
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
        self.create("playback_pause")
        thread.join(5)
        self.assertEqual(result["command"]["kind"], "playback_pause")
        self.assertTrue(result["command"]["claim_token"])
        self.assertLess(result["elapsed"], 3)
        response = self.client.get(f"/api/agents/commands/{self.agent_id}/next?wait=26", headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = self.client.get(f"/api/agents/commands/{self.agent_id}/next?wait=1", headers={"X-Sarae-Agent-Key": "bad"})
        self.assertEqual(response.status_code, 403)

    def test_long_poll_times_out_empty(self):
        started = time.monotonic()
        self.assertIsNone(self.poll(1))
        self.assertGreaterEqual(time.monotonic() - started, 0.9)


class LifecycleTests(AgentChannelBase):
    def test_success_records_timestamps_and_result(self):
        self.heartbeat()
        view = self.create("playback_select", {"index": 2})
        self.assertEqual((view["status"], view["attempts"]), ("PENDING", 0))
        command = self.poll(0)
        self.assertEqual(self.status_of(view["command_id"])["status"], "RUNNING")
        self.ack(command, True, json.dumps({"index": 2}))
        done = self.status_of(view["command_id"])
        self.assertEqual((done["status"], done["result"], done["attempts"]), ("SUCCESS", {"index": 2}, 1))
        self.assertTrue(done["started_at"] and done["completed_at"])

    def test_rpc_success_failure_offline_and_checks(self):
        self.heartbeat()
        thread, seen = self.fake_agent(lambda c: (True, json.dumps({"ok": True, "index": c["payload"]["index"]})))
        self.assertEqual(cloud_agents.rpc(self.agent_id, self.owner_id, "playback_select", {"index": 2}, timeout=5), {"ok": True, "index": 2})
        thread.join(5)
        thread, _ = self.fake_agent(lambda c: (False, "Chưa phát playlist"))
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_pause", {}, timeout=5)
        thread.join(5)
        self.assertEqual((ctx.exception.status_code, ctx.exception.detail), (502, "Chưa phát playlist"))
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, "someone-else", "playback_stop", {})
        self.assertEqual(ctx.exception.status_code, 404)
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_seek", {"ms": -1})
        self.assertEqual(ctx.exception.status_code, 422)
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        with cloud_agents._db() as db:
            db.execute("UPDATE agent_status SET last_seen=?", (old,))
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_stop", {})
        self.assertEqual(ctx.exception.status_code, 409)

    def test_web_wait_ends_but_command_keeps_its_lifecycle_then_expires_undelivered(self):
        self.heartbeat()
        with self.assertRaises(HTTPException) as ctx:
            cloud_agents.rpc(self.agent_id, self.owner_id, "playback_next", {}, timeout=0.3)
        self.assertEqual(ctx.exception.status_code, 504)
        command_id = cloud_agents.owner_list_commands(self.agent_id, authorization=self.owner_token)["commands"][0]["command_id"]
        self.assertEqual(self.status_of(command_id)["status"], "PENDING", "a slow Agent may still take it")
        self.expire("expires_at", command_id)
        self.assertEqual(self.status_of(command_id)["status"], "TIMEOUT")
        self.assertIsNone(self.poll(0), "an expired LIVE control is never played late")

    def test_lost_ack_of_non_retryable_command_times_out_and_late_ack_is_recorded(self):
        self.heartbeat()
        view = self.create("playback_next")
        command = self.poll(0)
        self.expire("lease_until", view["command_id"])
        self.assertEqual(self.status_of(view["command_id"])["status"], "TIMEOUT")
        self.assertIsNone(self.poll(0), "never re-delivered: the Agent may already have skipped the video")
        reply = self.ack(command, True, json.dumps({"index": 1}))
        self.assertTrue(reply["late"])
        done = self.status_of(view["command_id"])
        self.assertEqual((done["status"], done["late_ack"]), ("SUCCESS", True))

    def test_lost_ack_of_retryable_command_is_redelivered_once_done(self):
        self.heartbeat()
        view = self.create("playback_pause")
        first = self.poll(0)
        self.expire("lease_until", view["command_id"])
        second = self.poll(0)
        self.assertEqual((second["command_id"], second["attempt"]), (view["command_id"], 2))
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        # The first delivery's ack arrives late: the action happened, so the command is done.
        self.assertTrue(self.ack(first, True, "{}")["late"])
        self.assertEqual(self.ack(second, True, "{}")["duplicate"], True)
        self.assertEqual(self.status_of(view["command_id"])["status"], "SUCCESS")

    def test_retryable_command_gives_up_after_max_attempts(self):
        self.heartbeat()
        view = self.create("playback_stop")
        for _ in range(cloud_agents.MAX_ATTEMPTS):
            self.assertIsNotNone(self.poll(0))
            self.expire("lease_until", view["command_id"])
        self.assertEqual(self.status_of(view["command_id"])["status"], "TIMEOUT")

    def test_ack_must_match_agent_command_and_token(self):
        self.heartbeat()
        view = self.create("playback_pause")
        command = self.poll(0)
        self.ack(command, True, "{}", token="forged", expect=409)
        other = self.client.post("/api/agents/commands/ack", headers=self.headers, json={
            "agent_id": self.agent_id, "command_id": "nope", "claim_token": command["claim_token"], "success": True})
        self.assertEqual(other.status_code, 404)
        self.ack(command, True, "{}")
        self.assertTrue(self.ack(command, False, "again")["duplicate"], "first outcome stands")
        self.assertEqual(self.status_of(view["command_id"])["status"], "SUCCESS")

    def test_idempotency_key_returns_the_same_command(self):
        self.heartbeat()
        first = self.create("playback_next", idempotency_key="click-1")
        again = self.create("playback_next", idempotency_key="click-1")
        self.assertEqual(first["command_id"], again["command_id"])
        with self.assertRaises(HTTPException) as ctx:
            self.create("playback_previous", idempotency_key="click-1")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIsNotNone(self.poll(0))
        self.assertIsNone(self.poll(0), "only one command exists")

    def test_one_running_command_per_lane(self):
        self.heartbeat()
        first = self.create("playback_next")
        second = self.create("playback_previous")
        speech = self.create("tts_speak", {"text": "Xin chào"})
        live = self.poll(0)
        self.assertEqual(live["command_id"], first["command_id"])
        background = self.poll(0)
        self.assertEqual(background["command_id"], speech["command_id"], "TTS does not wait for the LIVE lane")
        self.assertIsNone(self.poll(0), "the next LIVE command waits for the running one")
        self.ack(live, True, "{}")
        self.assertEqual(self.poll(0)["command_id"], second["command_id"])

    def test_two_workers_never_claim_the_same_command(self):
        self.heartbeat()
        for _ in range(4):
            self.create("tts_speak", {"text": "a"})
        claimed, errors = [], []

        def worker():
            try:
                for _ in range(4):
                    command = cloud_agents._claim(self.agent_id)
                    if command:
                        claimed.append(command["command_id"])
                        cloud_agents.ack_command(cloud_agents.AckBody(agent_id=self.agent_id, command_id=command["command_id"],
                                                                      claim_token=command["claim_token"], success=True), self.key)
            except Exception as exc:  # pragma: no cover - surfaced by the assertion below
                errors.append(exc)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(claimed), len(set(claimed)), "no command handed out twice")

    def test_cancel_only_undelivered_commands(self):
        self.heartbeat()
        view = self.create("playback_next", session_id=7)
        self.assertEqual(cloud_agents.cancel_commands(self.agent_id, session_id=7), 1)
        self.assertEqual(self.status_of(view["command_id"])["status"], "CANCELLED")
        self.assertIsNone(self.poll(0))
        running = self.create("playback_pause", session_id=7)
        self.poll(0)
        self.assertEqual(cloud_agents.cancel_commands(self.agent_id, session_id=7), 0, "a delivered command is not cancelled")
        self.assertEqual(self.status_of(running["command_id"])["status"], "RUNNING")

    def test_password_kept_until_final_then_masked(self):
        self.heartbeat()
        response = self.client.post("/api/agents/commands", headers={"Authorization": self.owner_token}, json={
            "agent_id": self.agent_id, "kind": "obs_configure",
            "payload": {"host": "127.0.0.1", "port": 4455, "password": "s3cret", "input_name": "Sarae Video",
                        "dual_mode": True, "source_a": "A", "source_b": "B"}})
        self.assertEqual(response.status_code, 200, response.text)
        command = self.poll(0)
        self.assertEqual(command["payload"]["password"], "s3cret")
        self.ack(command, True, json.dumps({"connected": True}))
        with cloud_agents._db() as db:
            stored = json.loads(db.execute("SELECT payload FROM agent_commands").fetchone()[0])
        self.assertEqual(stored["password"], "***")
        listed = self.client.get(f"/api/agents/commands?agent_id={self.agent_id}", headers={"Authorization": self.owner_token}).json()["commands"]
        self.assertEqual((listed[0]["status"], listed[0]["state"]), ("SUCCESS", "success"))

    def test_owner_command_api(self):
        self.heartbeat()
        auth = {"Authorization": self.owner_token}
        created = self.client.post(f"/api/agents/{self.agent_id}/commands", headers={**auth, "Idempotency-Key": "k1"},
                                   json={"kind": "playback_pause"})
        self.assertEqual(created.status_code, 202)
        again = self.client.post(f"/api/agents/{self.agent_id}/commands", headers={**auth, "Idempotency-Key": "k1"},
                                 json={"kind": "playback_pause"})
        self.assertEqual(again.json()["command_id"], created.json()["command_id"])
        command_id = created.json()["command_id"]
        self.assertEqual(self.client.get(f"/api/agents/{self.agent_id}/commands/{command_id}", headers=auth).json()["status"], "PENDING")
        cancelled = self.client.post(f"/api/agents/{self.agent_id}/commands/{command_id}/cancel", headers=auth)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(self.client.get(f"/api/agents/{self.agent_id}/commands/{command_id}").status_code, 401)

    def test_outcome_applied_exactly_once_per_final_status(self):
        from unittest import mock
        self.heartbeat()
        applied = []
        with mock.patch("app.live_sessions.apply_command_outcome", side_effect=lambda view, ctx: applied.append(view["status"])):
            view = self.create("playback_next", context={"module": "test"})
            command = self.poll(0)
            self.expire("lease_until", view["command_id"])
            cloud_agents.apply_outcomes(self.agent_id)
            cloud_agents.apply_outcomes(self.agent_id)
            self.ack(command, True, "{}")  # late ack turns TIMEOUT into SUCCESS
            cloud_agents.apply_outcomes(self.agent_id)
        self.assertEqual(applied, ["TIMEOUT", "SUCCESS"])


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

    def test_overlay_preview_iteration(self):
        good = {"session_id": 7, "timeline_id": 3, "media_id": MID, "position_ms": 1500}
        self.assertEqual(cloud_agents.validate_payload("overlay_preview", good)["iteration"], 0, "optional, first loop")
        self.assertEqual(cloud_agents.validate_payload("overlay_preview", {**good, "iteration": 12})["iteration"], 12)
        self.bad("overlay_preview", {**good, "iteration": -1})
        self.bad("overlay_preview", {**good, "iteration": "1"})
        self.bad("overlay_preview", {**good, "iteration": True})
        self.assertEqual(cloud_agents.validate_payload("overlay_preview", {"session_id": 7, "stop": True}),
                         {"session_id": 7, "stop": True})

    def test_select_seek_and_no_param_kinds(self):
        self.assertEqual(cloud_agents.validate_payload("playback_select", {"index": 0}), {"index": 0})
        self.bad("playback_select", {"index": -1})
        self.bad("playback_select", {"index": "1"})
        self.assertEqual(cloud_agents.validate_payload("playback_seek", {"ms": 1500}), {"ms": 1500})
        self.bad("playback_seek", {})
        for kind in ("playback_cancel_pending", "playback_pause", "playback_resume", "playback_next",
                     "playback_previous", "playback_stop", "obs_pause"):
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
        for kind in ("product_play", "playback_seek", "obs_configure", "obs_inputs"):
            cloud_agents.CommandBody(agent_id="agent_1", kind=kind)
        with self.assertRaises(Exception):
            cloud_agents.CommandBody(agent_id="agent_1", kind="shell")


class LegacyLibrarySyncTests(AgentChannelBase):
    """Older Agents still post their local MP4 inventory; the Cloud accepts it without storing anything."""

    def sync(self, videos, headers=None):
        return self.client.post("/api/agent-library/sync", headers=headers or self.headers,
                                json={"agent_id": self.agent_id, "videos": videos})

    def test_inventory_is_accepted_and_dropped(self):
        video = {"media_id": "b" * 64, "filename": "a.mp4", "size_bytes": 10, "modified_ns": 1,
                 "thumb_jpeg_base64": base64.b64encode(bytes([255, 216, 255])).decode()}
        reply = self.sync([video])
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["thumbs_missing"], [], "an old Agent must not be asked for thumbnails")
        self.assertFalse(reply.json()["stored"])
        self.assertEqual(self.client.get(f"/api/agent-library/{self.agent_id}", headers={"Authorization": self.owner_token}).status_code, 404)

    def test_unknown_agent_is_still_refused(self):
        self.assertEqual(self.sync([], headers={"X-Sarae-Agent-Key": "wrong"}).status_code, 403)


class AgentErrorCodeTests(AgentChannelBase):
    """Agent-facing errors keep their status and detail and add X-Sarae-Error (agent-core cloud/client.py)."""

    def assertError(self, response, status, code, detail):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.headers.get("X-Sarae-Error"), code)
        self.assertEqual(response.json()["detail"], detail)

    def license(self, **changes):
        body = {"max_agents": 2, "expires_at": None, "enabled": True, **changes}
        identity.update_license(self.owner_id, identity.LicenseUpdate(**body), authorization=self.admin)

    def beat(self, key):
        headers = {} if key is None else {"X-Sarae-Agent-Key": key}
        return self.client.post("/api/agents/heartbeat", headers=headers,
                                json={"agent_id": self.agent_id, "hostname": "PC-1"})

    def activate(self, key):
        return self.client.post("/api/identity/activate", json={"activation_key": key, "hostname": "PC-2"})

    def test_verify_agent_codes(self):
        invalid = "Agent không có license hợp lệ"
        self.assertError(self.beat(None), 401, "AGENT_NOT_ACTIVATED", "Agent chưa kích hoạt")
        self.assertError(self.beat("wrong-key"), 403, "AGENT_UNAUTHORIZED", invalid)
        self.license(expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.assertError(self.beat(self.key), 403, "LICENSE_EXPIRED", invalid)
        self.license(enabled=False)
        self.assertError(self.beat(self.key), 403, "LICENSE_EXPIRED", invalid)
        self.license()
        self.assertEqual(self.beat(self.key).status_code, 200)
        identity.revoke_agent(self.agent_id, authorization=self.admin)
        self.assertError(self.beat(self.key), 403, "AGENT_REVOKED", invalid)
        # Long-poll and events go through the same check.
        self.assertError(self.client.get(f"/api/agents/commands/{self.agent_id}/next", headers=self.headers),
                         403, "AGENT_REVOKED", invalid)
        self.assertError(self.client.post("/api/agents/events", headers=self.headers,
                                          json={"agent_id": self.agent_id, "events": []}),
                         403, "AGENT_REVOKED", invalid)
        # A wrong key for a revoked Agent says nothing about that Agent.
        self.assertError(self.beat("wrong-key"), 403, "AGENT_UNAUTHORIZED", invalid)

    def test_verify_agent_success_shape_unchanged(self):
        self.assertEqual(set(identity.verify_agent(self.agent_id, self.key)), {"id", "owner_id", "enabled", "expires_at"})

    def test_activation_codes(self):
        invalid = "Mã kích hoạt không hợp lệ hoặc đã sử dụng"
        self.assertError(self.activate("SARAE-ACT-" + "x" * 30), 403, "ACTIVATION_KEY_INVALID", invalid)
        self.assertError(self.activate(self.activation_key), 403, "ACTIVATION_KEY_USED", invalid)
        second = identity.issue_key(self.owner_id, authorization=self.admin)["activation_key"]
        self.license(expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.assertError(self.activate(second), 403, "LICENSE_EXPIRED", "License đã hết hạn hoặc bị khóa")
        self.license()
        self.assertEqual(self.activate(second).status_code, 200)
        third = identity.issue_key(self.owner_id, authorization=self.admin)["activation_key"]
        self.assertError(self.activate(third), 409, "AGENT_LIMIT_REACHED", "Đã đạt giới hạn Agent của license")
        # The failed attempts did not use up the key.
        self.license(max_agents=3)
        self.assertEqual(self.activate(third).status_code, 200)


JPEG = b"\xff\xd8\xff\xe0 jpeg"


class CaptchaAlertTests(AgentChannelBase):
    def setUp(self):
        super().setUp()
        self.sent = []
        cloud_agents.save_telegram_config(self.owner_id,self.agent_id,True,"-100777","123:TEST")
        self.sender = patch.object(cloud_agents, "send_telegram",
                                   side_effect=lambda chat, text, token: self.sent.append((chat, text)))
        self.sender.start()
        self.photos = []
        self.capture_patches = [
            patch.object(cloud_agents, "CAPTURE_DIR", Path(self.tmp.name) / "captures"),
            patch.object(cloud_agents, "CAPTURE_WAIT_SECONDS", 0.3),
            patch.object(cloud_agents, "send_telegram_photo",
                         side_effect=lambda chat, caption, photo, token: self.photos.append((chat, caption, photo)))]
        for item in self.capture_patches:
            item.start()

    def tearDown(self):
        for item in self.capture_patches:
            item.stop()
        self.sender.stop()
        super().tearDown()

    def upload(self, at, photo=JPEG, headers=None):
        return self.client.post("/api/agents/verification-capture", params={"agent_id": self.agent_id, "at": at},
                                content=photo, headers={**(self.headers if headers is None else headers),
                                                        "Content-Type": "image/jpeg"})

    @staticmethod
    def wait_alerts():
        for thread in threading.enumerate():
            if thread.name == "sarae-captcha-alert":
                thread.join(5)

    def post_beat(self, status, enabled=True, chat="-100777", running=True, events=None):
        cloud_agents.save_telegram_config(self.owner_id,self.agent_id,enabled,chat,None)
        verification = {"state": {"running": running, "status": status, "match_score": 0.91},
                        "config": {"telegram_enabled": enabled, "telegram_chat_id": chat},
                        "events": events or []}
        return self.client.post("/api/agents/heartbeat", headers=self.headers, json={
            "agent_id": self.agent_id, "hostname": "PC-1", "verification": verification})

    def beat(self, status, **options):
        response = self.post_beat(status, **options)
        self.assertEqual(response.status_code, 200, response.text)
        self.wait_alerts()

    def kinds(self):
        return [text.splitlines()[0] for _, text in self.sent]

    def test_alert_once_per_change(self):
        self.beat("watching")
        self.assertEqual(self.sent, [])
        self.beat("captcha_detected", events=[{"at": "t", "kind": "captcha_detected", "detail": "Khớp mẫu, điểm 0.91"}])
        self.assertEqual(len(self.sent), 1)
        chat, text = self.sent[0]
        self.assertEqual(chat, "-100777")
        self.assertIn("Sarae LIVE | captcha_detected", text)
        self.assertIn("PC-1", text)
        self.assertIn("Khớp mẫu, điểm 0.91", text)
        self.assertIn("Độ tin cậy: 0.910", text)
        self.assertIn("Trạng thái: Nghi vấn CAPTCHA", text)
        self.beat("captcha_detected")
        self.beat("error")                   # a transient scan error while the CAPTCHA is still up
        self.beat("window_unavailable")
        self.beat("captcha_detected")
        self.assertEqual(self.kinds(), ["Sarae LIVE | captcha_detected"])
        self.beat("watching")
        self.beat("watching")
        self.assertEqual(self.kinds(), ["Sarae LIVE | captcha_detected", "Sarae LIVE | captcha_cleared"])
        self.beat("captcha_detected")
        self.assertEqual(self.kinds()[-1], "Sarae LIVE | captcha_detected")
        self.assertEqual(len(self.sent), 3)

    def test_first_heartbeat_already_detected_alerts(self):
        self.beat("captcha_detected")
        self.assertEqual(self.kinds(), ["Sarae LIVE | captcha_detected"])

    def test_warning_and_challenge_have_distinct_alerts_without_duplicates(self):
        self.beat("inactivity_warning", events=[{"at": "t", "kind": "verification_detected", "detail": "Yêu cầu xác minh"}])
        self.assertIn("Sự kiện: INACTIVITY_WARNING", self.sent[-1][1])
        self.assertIn("Trạng thái: Cảnh báo LIVE không hoạt động", self.sent[-1][1])
        self.beat("inactivity_warning")
        self.beat("error")
        self.beat("inactivity_warning")
        self.assertEqual(len(self.sent), 1)
        self.beat("captcha_challenge")
        self.beat("captcha_challenge")
        self.assertEqual(len(self.sent), 1)
        self.beat("watching")
        self.assertEqual(self.kinds()[-1], "Sarae LIVE | verification_cleared")
        self.beat("captcha_challenge")
        self.assertIn("Sự kiện: CAPTCHA_CHALLENGE", self.sent[-1][1])

    def test_stopped_monitor_forgets_the_alert(self):
        self.beat("captcha_detected")
        self.beat("stopped", running=False)
        self.assertEqual(len(self.sent), 1)     # stopping is not "cleared"
        self.beat("captcha_detected")
        self.assertEqual(len(self.sent), 2)

    def test_no_alert_without_owner_telegram_config_or_token(self):
        self.beat("captcha_detected", enabled=False)
        self.beat("watching", enabled=False)
        self.beat("captcha_detected", chat="  ")
        self.beat("watching", chat="  ")
        cloud_agents.save_telegram_config(self.owner_id,self.agent_id,True,"-100777",None,clear_token=True)
        self.beat("captcha_detected")
        self.assertEqual(self.sent, [])
        # The change was seen while alerts were off: a later heartbeat does not re-send it.
        self.beat("captcha_detected")
        self.assertEqual(self.sent, [])

    def test_alert_is_non_blocking_and_best_effort(self):
        release = threading.Event()
        with patch.object(cloud_agents, "send_telegram", side_effect=lambda *_: release.wait(10)):
            started = time.monotonic()
            self.assertEqual(self.post_beat("captcha_detected").status_code, 200)
            self.assertLess(time.monotonic() - started, 5)
            release.set()
            self.wait_alerts()
        with patch.object(cloud_agents, "send_telegram", side_effect=OSError("telegram down")), \
                self.assertLogs("app.cloud_agents", "WARNING") as logs:
            self.beat("watching")
        self.assertIn("telegram down", logs.output[0])

    def test_detection_alert_carries_the_uploaded_screenshot(self):
        event = {"at": "2026-11-04T01:00:00+00:00", "kind": "verification_detected",
                 "detail": "Cảnh báo LIVE không hoạt động, TikTok yêu cầu xác minh"}
        self.assertEqual(self.upload(event["at"]).status_code, 200)
        self.beat("inactivity_warning", events=[event])
        self.assertEqual(self.sent, [])                      # sent as a photo, not as text
        chat, caption, photo = self.photos[0]
        self.assertEqual((chat, photo), ("-100777", JPEG))
        self.assertIn("Sự kiện: INACTIVITY_WARNING", caption)
        self.assertFalse(any((Path(self.tmp.name) / "captures").iterdir()))   # removed once sent
        self.beat("watching")                                 # cleared: plain text, no picture
        self.assertEqual(self.kinds(), ["Sarae LIVE | verification_cleared"])
        self.assertEqual(len(self.photos), 1)

    def test_screenshot_arriving_after_the_heartbeat_is_awaited(self):
        event = {"at": "t2", "kind": "verification_detected", "detail": "Hộp thoại CAPTCHA"}
        with patch.object(cloud_agents, "CAPTURE_WAIT_SECONDS", 5):
            self.assertEqual(self.post_beat("captcha_challenge", events=[event]).status_code, 200)
            time.sleep(0.3)
            self.assertEqual(self.upload("t2").status_code, 200)
            self.wait_alerts()
        self.assertEqual(len(self.photos), 1)

    def test_missing_or_stale_screenshot_falls_back_to_text(self):
        self.assertEqual(self.upload("an-older-detection").status_code, 200)
        self.beat("captcha_challenge", events=[{"at": "t3", "kind": "verification_detected", "detail": "x"}])
        self.assertEqual(self.photos, [])
        self.assertIn("(Agent chưa gửi ảnh màn hình)", self.sent[0][1])

    def test_failed_photo_send_still_sends_text(self):
        self.upload("t4")
        with patch.object(cloud_agents, "send_telegram_photo", side_effect=OSError("too big")):
            self.beat("captcha_challenge", events=[{"at": "t4", "kind": "verification_detected", "detail": "x"}])
        self.assertIn("(Không gửi được ảnh màn hình)", self.sent[0][1])

    def test_capture_upload_is_validated(self):
        self.assertIn(self.upload("t", headers={"X-Sarae-Agent-Key": "wrong"}).status_code, (401, 403))
        self.assertEqual(self.upload("t", photo=b"\x89PNG not jpeg").status_code, 422)
        with patch.object(cloud_agents, "CAPTURE_MAX_BYTES", 10):
            self.assertEqual(self.upload("t", photo=JPEG + b"0" * 20).status_code, 413)

    def test_send_telegram_photo_request(self):
        self.capture_patches[2].stop()
        try:
            with patch.object(cloud_agents.request, "urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
                cloud_agents.send_telegram_photo("-100777", "x" * 2000, JPEG, "123:TEST")
        finally:
            self.capture_patches[2].start()
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bot123:TEST/sendPhoto")
        self.assertIn("multipart/form-data; boundary=", req.headers["Content-type"])
        self.assertIn(b'name="chat_id"\r\n\r\n-100777\r\n', req.data)
        self.assertIn(b"x" * 1024 + b"\r\n", req.data)
        self.assertNotIn(b"x" * 1025, req.data)
        self.assertIn(b"Content-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n--", req.data)

    def test_send_telegram_request(self):
        self.sender.stop()
        try:
            with patch.object(cloud_agents.request, "urlopen") as urlopen:
                cloud_agents.send_telegram("-100777", "Sarae LIVE | captcha_detected", "123:TEST")
        finally:
            self.sender.start()
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bot123:TEST/sendMessage")
        self.assertIn(b"chat_id=-100777", req.data)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 8)

    def test_telegram_api_error_keeps_token_out_of_message(self):
        from io import BytesIO
        from urllib.error import HTTPError
        self.sender.stop()
        try:
            failure = HTTPError("https://api.telegram.org/bot123:TEST/sendMessage", 400,
                                "Bad Request", {}, BytesIO(b'{"description":"Bad chat ID 123:TEST"}'))
            with patch.object(cloud_agents.request, "urlopen", side_effect=failure):
                with self.assertRaises(HTTPError) as raised:
                    cloud_agents.send_telegram("-100777", "test", "123:TEST")
            message = cloud_agents.telegram_error(raised.exception, "123:TEST")
            self.assertIn("Telegram HTTP 400", message)
            self.assertNotIn("123:TEST", message)
        finally:
            self.sender.start()

    def test_old_agent_status_table_gets_the_alert_column(self):
        import sqlite3
        cloud_agents.DB = Path(self.tmp.name) / "old_agents.sqlite3"
        old = sqlite3.connect(cloud_agents.DB)
        old.execute("CREATE TABLE agent_status (agent_id TEXT PRIMARY KEY, host TEXT NOT NULL, "
                    "last_seen TEXT NOT NULL, status_json TEXT NOT NULL)")
        old.execute("INSERT INTO agent_status VALUES(?,?,?,?)",
                    (self.agent_id, "PC-1", "2026-01-01T00:00:00+00:00", "{}"))
        old.commit()
        old.close()
        cloud_agents.save_telegram_config(self.owner_id,self.agent_id,True,"-100777","123:TEST")
        self.beat("captcha_detected")
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
