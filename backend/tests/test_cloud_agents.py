"""Agent command channel: lifecycle (claim, lease, ack, timeout, idempotency, lanes), long-poll, events, validation.

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
        # The Cloud only checks the JPEG signature and size; OpenCV is an Agent-side dependency.
        return bytes.fromhex("ffd8ffe0") + bytes(200) + bytes.fromhex("ffd9")

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
        name = first["thumb_url"].split("?")[0].rsplit("/", 1)[-1]
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
        self.assertTrue(first["thumb_url"].split("?")[0].endswith(name))
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
