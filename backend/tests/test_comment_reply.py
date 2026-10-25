"""Auto reply: two independent switches (voice, comment), one pipeline, the comment branch's pacing, duplicates, expiry
and Agent outcomes (docs/AI_AUTO_REPLY.md)."""
import json
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text

from app import ai_service, comment_reply, live_reply_director as director
from app.ai_live_batches import context, run_batch, states
from app.catalog import Product
from app.chat_payload import validate_chat_send
from app.cloud_agents import lane
from app.live_sessions import LiveSession
from app.per_owner import live_key
from support import SaasTestCase

AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 50}


class SettingsUpgradeTests(unittest.TestCase):
    def test_old_settings_keep_the_voice_and_never_start_comments(self):
        upgraded = director.ReplyOptions.model_validate({"mode": "semi_auto", "channel": "both", "veto_seconds": 6,
                                                         "persona": "xưng em", "rate": 10})
        self.assertEqual((upgraded.auto_voice_reply, upgraded.auto_comment_reply, upgraded.confirm_seconds),
                         (True, False, 6))
        self.assertEqual((upgraded.persona, upgraded.rate), ("xưng em", 10), "AI/TTS settings are kept")
        auto = director.ReplyOptions.model_validate({"mode": "auto", "channel": "comment"})
        self.assertEqual((auto.auto_voice_reply, auto.auto_comment_reply, auto.confirm_seconds), (False, False, 0))
        manual = director.ReplyOptions.model_validate({"mode": "manual", "channel": "voice"})
        self.assertFalse(manual.auto_voice_reply or manual.auto_comment_reply)
        new = director.ReplyOptions.model_validate({"auto_voice_reply": False, "auto_comment_reply": True, "mode": "auto"})
        self.assertEqual((new.auto_voice_reply, new.auto_comment_reply), (False, True), "new settings win")

    def test_chat_payload_and_lane(self):
        good = {"reply_id": 1, "session_id": 2, "expected_user_id": "123", "ttl_seconds": 30, "text": "cotton nha",
                "target": {"msg_id": "m1", "user_id": "9", "username": "lan.nguyen", "nickname": "Lan 🌸", "comment": "vải gì"}}
        self.assertEqual(validate_chat_send(good)["max_per_minute"], 5)
        for bad in ({**good, "text": ""}, {**good, "expected_user_id": "abc"}, {**good, "extra": 1},
                    {**good, "target": {**good["target"], "username": "bad name"}}, {**good, "ttl_seconds": 1}):
            with self.assertRaises(HTTPException):
                validate_chat_send(bad)
        self.assertEqual((lane("chat_send"), lane("chat_probe"), lane("tts_speak")), ("chat", "chat", "background"))


def answer(qid, intent, say, text="", slots=None, confidence=0.9):
    return {"qid": qid, "intent": intent, "slots": slots or {}, "say": say, "text": text or say, "confidence": confidence,
            "needs_review": False, "other_product": False}


class CommentBranchTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(ai_service.settings, "openai_api_key", "sk-platform-test-0000")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.put("/api/ai/config", json=AI)
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.agent_id = self.agent["agent_id"]
        with self.Session() as db:
            db.execute(text("INSERT INTO tiktok_accounts(id,user_id,user_name) VALUES(7,'6970001','shop.ao')"))
            product = Product(name="Áo thun", sku="A1", owner_id=self.owner_a["id"], price=200000, description="Cotton.")
            db.add(product)
            db.flush()
            db.add(LiveSession(name="LIVE", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent_id,
                               current_product_id=product.id, tiktok_account_id=7))
            db.commit()
        self.st = states(live_key(self.owner_a["id"], self.agent_id))
        self.sent: list[dict] = []
        self.ai_answers: list = []
        self.fail_kinds: set[str] = set()

        def fake_ai(config, prompt, key):
            return json.dumps({"answers": self.ai_answers.pop(0)}), 10, 10

        def fake_command(agent_id, owner_id, kind, payload=None, **options):
            if kind in self.fail_kinds:
                raise HTTPException(409, f"{kind} không gửi được")
            self.sent.append({"kind": kind, "payload": payload, "context": options.get("context")})
            return {"command_id": f"cmd-{len(self.sent)}", "status": "PENDING"}
        for target in (patch.object(ai_service, "call_openai", fake_ai), patch("app.cloud_agents.create_command", fake_command),
                       patch.object(comment_reply, "_schedule_check", lambda st: None, create=True)):
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        for timer in list(self.st.timers.values()):
            timer.cancel()
        states.discard(self.st.key)
        super().tearDown()

    def options(self, **values):
        body = {**director.ReplyOptions().model_dump(), "confirm_seconds": 0, **values}
        self.assertEqual(self.client.put("/api/ai/live/reply-config", json=body).status_code, 200)

    def batch(self, *comments):
        ctx = context(self.owner_a["id"], self.agent_id)
        items = [{"id": i, "username": user, "nickname": user.title(), "user_id": f"9{i}", "msg_id": f"m{i}-{user}",
                  "comment": c, "context": ctx, "asked_at": time.monotonic(), "asked_iso": "x"}
                 for i, (user, c) in enumerate(comments, 1)]
        self.st.sequence += 1
        run_batch(self.st, self.st.sequence, items)
        return [self.st.replies[i] for i in self.st.results[-1]["reply_ids"]]

    def kinds(self):
        return [c["kind"] for c in self.sent]

    def outcome(self, index, status="SUCCESS", result=None, error=None):
        command = self.sent[index]
        comment_reply.apply_outcome({"status": status, "result": result, "error": error, "completed_at": "now"},
                                    command["context"])

    def test_both_switches_send_voice_and_comment_independently(self):
        self.options(auto_voice_reply=True, auto_comment_reply=True)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton mát lắm nha.", "cotton mát lắm nha")]]
        reply = self.batch(("lan.nguyen", "vải gì vậy"))[0]
        self.assertEqual(sorted(self.kinds()), ["chat_send", "tts_speak"], reply.get("comment_delivery"))
        chat = next(c for c in self.sent if c["kind"] == "chat_send")["payload"]
        self.assertEqual((chat["expected_user_id"], chat["text"]), ("6970001", "Cotton mát lắm nha"),
                         "plain text: a typed @name is not a mention, the Agent mentions properly")
        self.assertEqual(chat["target"], {"msg_id": "m1-lan.nguyen", "user_id": "91", "username": "lan.nguyen",
                                          "nickname": "Lan.Nguyen", "comment": "vải gì vậy"})
        self.assertEqual((reply["voice"], reply["comment"]), ("sent", "sending"))
        logs = self.client.get("/api/tiktok-chat/processing", params={"agent_id": self.agent_id}).json()["logs"]
        decision = json.loads(next(l["text"] for l in logs if l["stage"] == "AI_DECISION"))
        self.assertEqual((decision["voice_enabled"], decision["comment_enabled"]), (True, True))
        self.assertEqual(next(l for l in logs if l["stage"] == "AI_DECISION")["comment_id"], "m1-lan.nguyen")

    def test_one_branch_failing_does_not_stop_the_other(self):
        self.options(auto_voice_reply=True, auto_comment_reply=True)
        self.fail_kinds = {"tts_speak"}
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha")]]
        reply = self.batch(("lan", "vải gì"))[0]
        self.assertEqual(self.kinds(), ["chat_send"])
        self.assertEqual((reply["voice"], reply["comment"]), ("skipped", "sending"))
        self.fail_kinds = {"chat_send"}
        self.ai_answers = [[answer(1, "ASK_COLOR", "Có màu trắng nha.", "có trắng nha")]]
        self.outcome(0, result={"status": "CONFIRMED", "mode": "NORMAL", "steps": []})
        self.st.comment_last_sent, self.st.comment_sent_times = None, []  # pacing satisfied
        other = self.batch(("hoa", "có màu trắng k"))[0]
        self.assertEqual(other["comment"], "failed")
        self.assertIn("không gửi được", other["comment_delivery"]["error"])

    def test_both_off_sends_nothing_and_review_is_never_sent(self):
        self.options(auto_voice_reply=False, auto_comment_reply=False)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.")]]
        reply = self.batch(("lan", "vải gì"))[0]
        self.assertEqual((self.sent, reply["voice"], reply["comment"], reply["route"]), ([], "ready", "ready", "auto"))
        self.options(auto_voice_reply=True, auto_comment_reply=True)
        self.ai_answers = [[answer(1, "COMPLAINT", "Bạn nhắn shop nha.")]]
        complaint = self.batch(("hoa", "hàng lỗi đổi sao"))[0]
        self.assertEqual((self.sent, complaint["voice"]), ([], "review"))

    def test_outcomes_record_mode_steps_and_free_the_queue(self):
        self.options(auto_comment_reply=True, comment_min_interval_seconds=4)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha"),
                            answer(2, "ASK_COLOR", "Có trắng nha.", "có trắng nha")]]
        first, second = self.batch(("lan", "vải gì"), ("hoa", "có màu trắng k"))
        self.assertEqual((first["comment"], second["comment"]), ("sending", "queued"), "one message at a time")
        steps = [{"step": "TYPING", "ms": 300, "action": "composer"}, {"step": "CONFIRMED", "ms": 1500}]
        self.st.comment_last_sent = time.monotonic() - 10
        self.outcome(0, result={"status": "CONFIRMED", "mode": "DIRECT_REPLY", "steps": steps, "error": None})
        self.assertEqual((first["comment"], first["comment_delivery"]["mode"], first["comment_delivery"]["steps"]),
                         ("sent", "DIRECT_REPLY", steps))
        self.assertEqual(second["comment"], "sending", "the queue moves on")
        self.outcome(1, result={"status": "FAILED", "mode": None, "steps": [], "error": "Không thấy khung chat"})
        self.assertEqual((second["comment"], second["comment_delivery"]["error"]), ("failed", "Không thấy khung chat"))
        logs = [l for l in self.client.get("/api/tiktok-chat/processing", params={"agent_id": self.agent_id}).json()["logs"]
                if l["stage"] == "COMMENT_SEND"]
        self.assertEqual([l["decision"] for l in logs], ["QUEUED", "CONFIRMED", "QUEUED", "FAILED"])

    def test_agent_timeout_is_a_failure(self):
        self.options(auto_comment_reply=True)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha")]]
        reply = self.batch(("lan", "vải gì"))[0]
        self.outcome(0, status="TIMEOUT")
        self.assertEqual((reply["comment"], reply["comment_delivery"]["error"]), ("failed", "Agent không phản hồi kịp"))

    def test_pacing_duplicates_and_expiry(self):
        self.options(auto_comment_reply=True, comment_min_interval_seconds=30, comment_max_per_minute=1)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha"),
                            answer(2, "ASK_COLOR", "Có trắng nha.", "có trắng nha")]]
        first, second = self.batch(("lan", "vải gì"), ("hoa", "có màu trắng k"))
        self.outcome(0, result={"status": "SUBMITTED", "mode": "NORMAL", "steps": []})
        self.assertEqual((first["comment"], second["comment"]), ("sent", "queued"), "waits for the interval")
        self.assertIn(("comment_pump",), self.st.timers)
        # Same text to the same viewer again: never twice.
        self.st.comment_last_sent, self.st.comment_sent_times = None, []
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha")]]
        second["_asked"] -= 1000  # too old by now
        again = self.batch(("lan", "chất vải gì"))[0]
        self.assertEqual(second["comment"], "expired")
        self.assertEqual((again["comment"], again["comment_delivery"]["status"]), ("failed", "DUPLICATE"))

    def test_operator_sends_edits_or_cancels(self):
        self.options(auto_comment_reply=True, confirm_seconds=10)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha")]]
        reply = self.batch(("lan", "vải gì"))[0]
        self.assertEqual((reply["comment"], self.sent), ("scheduled", []))
        on = {"agent_id": self.agent_id}
        cancelled = self.client.post(f"/api/ai/live/replies/{reply['id']}/comment/cancel", params=on).json()
        self.assertEqual(cancelled["comment"], "ready")
        bad = self.client.post(f"/api/ai/live/replies/{reply['id']}/comment", params=on, json={"text": "giá 150k nha"})
        self.assertEqual(bad.status_code, 422, "a price the product does not have is refused")
        sent = self.client.post(f"/api/ai/live/replies/{reply['id']}/comment", params=on, json={"text": "Cotton 100% nha"})
        self.assertEqual((sent.status_code, sent.json()["comment"]), (200, "sending"))
        self.assertEqual(self.sent[-1]["payload"]["text"], "Cotton 100% nha")
        self.assertEqual(self.client.post(f"/api/ai/live/replies/{reply['id']}/comment", params=on, json={}).status_code, 409)

    def test_no_verified_account_means_no_comment(self):
        with self.Session() as db:
            db.execute(text("UPDATE live_sessions SET tiktok_account_id=NULL"))
            db.commit()
        self.options(auto_comment_reply=True)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.", "cotton nha")]]
        reply = self.batch(("lan", "vải gì"))[0]
        self.assertEqual((reply["comment"], self.sent), ("failed", []))
        self.assertIn("TikTok Account", reply["comment_delivery"]["error"])


if __name__ == "__main__":
    unittest.main()
