"""The shop's Q&A per product, the AI playground (no LIVE needed) and the chat that follows its LIVE session."""
import json
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app import ai_service, tiktok_chat
from app.ai_live_batches import context, states
from app.catalog import Product
from app.live_sessions import LiveSession, apply_command_outcome
from app.per_owner import live_key
from support import SaasTestCase

AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 50}


def answer(qid, intent, say, text="", slots=None, confidence=0.9):
    return {"qid": qid, "intent": intent, "slots": slots or {}, "say": say, "text": text or say, "confidence": confidence,
            "needs_review": False, "other_product": False}


class AnswersAndPlaygroundTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(ai_service.settings, "openai_api_key", "sk-platform-test-0000")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.put("/api/ai/config", json=AI)
        with self.Session() as db:
            product = Product(name="Áo thun trắng", sku="A1", owner_id=self.owner_a["id"], price=200000,
                              description="Áo thun cotton.")
            db.add(product)
            db.commit()
            self.product_id = product.id
        self.prompts, self.ai_answers = [], []

        def fake_ai(config, prompt, key):
            self.prompts.append(prompt)
            return json.dumps({"answers": self.ai_answers.pop(0)}), 50, 20
        patcher = patch.object(ai_service, "call_openai", fake_ai)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, question, say, text=(), intent="GENERAL", headers=None, product_id=None):
        return self.client.post("/api/ai/answers", headers=headers or self.A, json={
            "product_id": product_id or self.product_id, "question": question, "intent": intent,
            "say": list(say), "text": list(text)})

    def test_shop_adds_edits_and_removes_answers(self):
        created = self.add("giặt máy được không", ["Dạ, giặt máy thoải mái nha."], ["giặt máy ok nha"])
        self.assertEqual(created.status_code, 201, created.text)
        entry = created.json()
        self.assertEqual((entry["say"], entry["scope"], entry["source"], entry["approved"]),
                         (["Giặt máy thoải mái nha."], "shop", "manual", True), "robotic opener cut")
        self.assertEqual(self.add("giá sao", ["Mẫu này 150k nha"]).status_code, 422, "a price the product does not have")
        self.assertEqual(self.add("x", []).status_code, 422)
        self.assertEqual(self.add("x", ["ok"], intent="LOUD").status_code, 422)
        edited = self.client.put(f"/api/ai/answers/{entry['id']}", json={"question": "giặt máy được ko",
                                                                         "intent": "GENERAL", "say": ["Giặt máy được nha."]})
        self.assertEqual(edited.json()["say"], ["Giặt máy được nha."])
        listed = self.client.get("/api/ai/answers", params={"product_id": self.product_id}).json()
        self.assertEqual((listed["product"]["name"], [a["question"] for a in listed["answers"]]),
                         ("Áo thun trắng", ["giặt máy được ko"]))
        self.assertEqual(self.client.get("/api/ai/answers", params={"product_id": self.product_id}, headers=self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/ai/answers/{entry['id']}", headers=self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/ai/answers/{entry['id']}").json(), {"ok": True})

    def test_shop_answers_survive_product_edits_are_used_and_guide_the_ai(self):
        self.add("giặt máy được không", ["Giặt máy thoải mái nha."], ["giặt máy ok nha"])
        self.client.put(f"/api/tiktok-catalog/products/{self.product_id}/ai-facts", json={"material": "cotton"})
        result = self.client.post("/api/ai/playground", json={"product_id": self.product_id, "comments": [
            {"nickname": "Lan", "username": "lan", "comment": "giặt máy được không shop"}]}).json()
        reply = result["replies"][0]
        self.assertEqual((reply["source"], reply["say"], reply["route"]), ("bank", "Giặt máy thoải mái nha.", "manual"),
                         "an approved shop answer is not held for review even for a GENERAL question")
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton mát nha.")]]
        self.client.post("/api/ai/playground", json={"product_id": self.product_id, "comments": [{"comment": "vải mặc nóng không"}]})
        self.assertIn('"shop_qa": [{"q": "giặt máy được không", "a": "Giặt máy thoải mái nha."}]', self.prompts[0])

    def test_shop_answer_with_an_old_price_is_not_said_after_a_price_change(self):
        self.add("có giảm giá không", ["Đang giảm còn 200k nha."])
        with self.Session() as db:
            db.get(Product, self.product_id).price = 250000
            db.commit()
        self.ai_answers = [[answer(1, "GENERAL", "Hiện chưa có giảm giá nha.")]]
        reply = self.client.post("/api/ai/playground", json={"product_id": self.product_id,
                                                              "comments": [{"comment": "có giảm giá không"}]}).json()["replies"][0]
        self.assertEqual(reply["source"], "ai", "the stale 200k answer is skipped")

    def test_playground_shows_voice_and_comment_without_live_or_learning(self):
        self.ai_answers = [[answer(2, "ASK_MATERIAL", "Vải cotton mặc mát lắm luôn.", "cotton mát lắm nha")]]
        result = self.client.post("/api/ai/playground", json={"product_id": self.product_id, "comments": [
            {"nickname": "Lan", "username": "lan", "comment": "giá bn shop"},
            {"nickname": "Hoa", "username": "hoa", "comment": "vải gì vậy"},
            {"comment": "hi"}]})
        self.assertEqual(result.status_code, 200, result.text)
        data = result.json()
        price, material = data["replies"]
        self.assertEqual((price["source"], price["intro"]), ("rule", None))
        self.assertIn("hai trăm nghìn", price["body"])
        self.assertEqual(price["spoken_text"], price["body"], "straight to the answer")
        self.assertEqual((material["source"], material["comment_text"]), ("ai", "@hoa Cotton mát lắm nha"))
        self.assertEqual(data["skipped"][0]["comment"], "hi")
        self.assertEqual(self.client.get("/api/ai/answers", params={"product_id": self.product_id}).json()["answers"], [],
                         "the playground never learns")
        self.assertEqual(self.client.post("/api/ai/playground", headers=self.B, json={
            "product_id": self.product_id, "comments": [{"comment": "giá"}]}).status_code, 404)
        self.assertEqual(self.client.post("/api/ai/playground", json={"product_id": self.product_id, "comments": []}).status_code, 422)

    def test_playground_reports_ai_errors_but_still_answers_rules(self):
        self.client.put("/api/ai/config", json={**AI, "enabled": False})
        data = self.client.post("/api/ai/playground", json={"product_id": self.product_id, "comments": [
            {"comment": "giá bn"}, {"comment": "vải có nhăn không"}]}).json()
        self.assertEqual([r["source"] for r in data["replies"]], ["rule", "ai"])
        self.assertIn("AI chưa được bật", data["error"])
        self.assertEqual(data["replies"][1]["route"], "review")


class SessionChatTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        with self.Session() as db:
            db.execute(text("INSERT INTO tiktok_accounts(id,user_id,user_name) VALUES(7,'111','shop.ao_dep')"))
            db.execute(text("INSERT INTO tiktok_accounts(id,user_id,user_name) VALUES(8,'222','')"))
            session = LiveSession(name="LIVE", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent["agent_id"],
                                  tiktok_account_id=7)
            draft = LiveSession(name="Nháp", status="DRAFT", owner_id=self.owner_a["id"], tiktok_account_id=8)
            db.add_all([session, draft])
            db.commit()
            self.sid, self.draft = session.id, draft.id
        self.started: list[str] = []
        patcher = patch.object(tiktok_chat.Reader, "start", lambda reader, username: self.started.append(username) or
                               setattr(reader, "status", "connected") or setattr(reader, "username", username))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.key = live_key(self.owner_a["id"], self.agent["agent_id"])

    def tearDown(self):
        tiktok_chat.readers.discard(self.key)
        states.discard(self.key)
        super().tearDown()

    def test_session_reads_its_own_account_chat(self):
        view = self.client.get(f"/api/live/sessions/{self.sid}/chat").json()
        self.assertEqual((view["username"], view["status"]), ("shop.ao_dep", "idle"))
        connected = self.client.post(f"/api/live/sessions/{self.sid}/chat/connect").json()
        self.assertEqual((self.started, connected["status"]), (["shop.ao_dep"], "connected"))
        self.client.post(f"/api/live/sessions/{self.sid}/chat/connect")
        self.assertEqual(self.started, ["shop.ao_dep"], "a chat already read is not restarted")
        self.assertEqual(self.client.post(f"/api/live/sessions/{self.draft}/chat/connect").status_code, 409)
        self.assertEqual(self.client.get(f"/api/live/sessions/{self.sid}/chat", headers=self.B).status_code, 404)

    def test_account_without_username_is_explained(self):
        with self.Session() as db:
            db.get(LiveSession, self.sid).tiktok_account_id = 8
            db.commit()
        response = self.client.post(f"/api/live/sessions/{self.sid}/chat/connect")
        self.assertEqual(response.status_code, 422)
        self.assertIn("username", response.json()["detail"])

    def test_chat_follows_the_session_start_and_end(self):
        with self.Session() as db:
            db.get(LiveSession, self.sid).status = "DRAFT"
            db.commit()
        apply_command_outcome({"status": "SUCCESS", "agent_id": self.agent["agent_id"], "command_id": "c1", "error": None,
                               "late_ack": False}, {"module": "live_sessions", "action": "start", "session_id": self.sid,
                                                    "product_id": None})
        self.assertEqual(self.started, ["shop.ao_dep"], "starting the session opens its chat")
        with patch.object(tiktok_chat.Reader, "stop") as stop:
            self.client.post(f"/api/live/sessions/{self.sid}/finish")
        stop.assert_called()


if __name__ == "__main__":
    unittest.main()
