"""AI LIVE replies: one answer per question, merged only by code, checked before anyone hears it, paced on the voice."""
import json
import time
import unittest
from unittest.mock import patch

from app import ai_live_batches, ai_service, live_reply_director as director, reply_rules
from app.ai_live_batches import context, run_batch, states
from app.catalog import Product
from app.live_sessions import LiveSession
from app.per_owner import live_key
from app.speech_text import (money_amounts, price_short, read_number, spoken_name,
                             safety_issues, spoken, strip_openers)
from support import SaasTestCase

AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 50}
DESCRIPTION = "Áo thun cotton. Size M 45-55kg, L 55-65kg. Freeship đơn từ 300k."


class SpeechTextTests(unittest.TestCase):
    def test_numbers_are_read_the_vietnamese_way(self):
        cases = {15: "mười lăm", 21: "hai mươi mốt", 24: "hai mươi tư", 105: "một trăm linh năm",
                 200000: "hai trăm nghìn", 1200000: "một triệu hai trăm nghìn", 1005: "một nghìn không trăm linh năm"}
        for number, words in cases.items():
            self.assertEqual(read_number(number), words)

    def test_reply_is_written_for_the_ear(self):
        self.assertEqual(spoken("Mẫu này 200k thôi nha 😍"), "Mẫu này hai trăm nghìn thôi nha.")
        self.assertEqual(spoken("55kg mặc size M là vừa"), "Năm mươi lăm ký mặc sai em là vừa.")
        self.assertEqual(spoken("Giá 1tr2, freeship HN"), "Giá một triệu hai, miễn phí ship Hà Nội.")
        self.assertEqual(spoken("Cao 1m6 lấy L nha"), "Cao một mét sáu lấy eo nha.")

    def test_robotic_openers_are_removed(self):
        self.assertEqual(strip_openers("Dạ chào anh chị, áo này 200k ạ"), "Áo này 200k ạ")
        self.assertEqual(strip_openers("Cảm ơn bạn đã hỏi, có màu đen nha"), "Có màu đen nha")
        self.assertEqual(strip_openers("Hiện còn size M"), "Hiện còn size M")

    def test_prices_links_phones_and_bad_words_are_caught(self):
        allowed = {200000}
        self.assertEqual(safety_issues("Mẫu này 200k nha", allowed), [])
        self.assertIn("Số tiền không khớp dữ liệu sản phẩm: 250k", safety_issues("Mẫu này 250k nha", allowed))
        self.assertIn("Có số điện thoại", safety_issues("Gọi 0901234567 nha", allowed))
        self.assertIn("Có đường link", safety_issues("Xem ở shopee.vn nha", allowed))
        self.assertIn("Có từ ngữ không phù hợp", safety_issues("đồ con chó", allowed))
        self.assertEqual(safety_issues("còn cho bạn nha", allowed), [], "ordinary words without marks are fine")
        self.assertEqual(money_amounts("200k, 1tr2, 199.000đ, 250000"), [200000, 1200000, 199000, 250000])

    def test_viewer_names_are_cleaned(self):
        self.assertEqual(spoken_name("Lan Anh 🌸", "lan_9x"), "Lan")
        self.assertEqual(spoken_name(None, "hoa.nguyen99"), "Hoa")
        self.assertIsNone(spoken_name("🔥🔥", "12345"))
        self.assertIsNone(spoken_name("đụ má", "abc"))
        self.assertEqual((price_short(199000), price_short(1200000), price_short(199500)), ("199k", "1tr2", "199.500đ"))


class RuleTests(unittest.TestCase):
    def test_only_plain_price_questions_skip_the_ai(self):
        for text in ("giá bn shop", "Áo này bao nhiêu vậy?", "cái này nhiêu tiền"):
            self.assertTrue(reply_rules.price_only(text), text)
        for text in ("giá bn, có size L k", "55kg mặc size gì", "cái lúc nãy giá sao", "còn bao nhiêu cái"):
            self.assertFalse(reply_rules.price_only(text), text)

    def test_noise_is_not_a_question(self):
        for text in ("hi", "❤️❤️", "chào shop", "ok"):
            self.assertTrue(reply_rules.is_noise(text), text)
        self.assertFalse(reply_rules.is_noise("có màu đen không"))


def answer(qid, intent, say, text="", slots=None, confidence=0.9, **extra):
    return {"qid": qid, "intent": intent, "slots": slots or {}, "say": say, "text": text or say, "confidence": confidence,
            "needs_review": False, "other_product": False, **extra}


class LiveReplyTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        for target in (patch.object(ai_service.settings, "openai_api_key", "sk-platform-test-0000"),
                       patch.object(ai_service.settings, "gemini_api_key", "")):
            target.start()
            self.addCleanup(target.stop)
        self.assertEqual(self.client.put("/api/ai/config", json=AI).status_code, 200)
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.agent_id = self.agent["agent_id"]
        with self.Session() as db:
            product = Product(name="Áo thun trắng", sku="A1", owner_id=self.owner_a["id"], price=200000,
                              description=DESCRIPTION)
            db.add(product)
            db.flush()
            self.product_id = product.id
            db.add(LiveSession(name="LIVE", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent_id,
                               current_product_id=product.id))
            db.commit()
        self.st = states(live_key(self.owner_a["id"], self.agent_id))
        self.prompts: list[str] = []
        self.ai_answers: list[list[dict]] = []
        self.sent: list[dict] = []
        sent = self.sent

        def fake_ai(config, text, key):
            self.prompts.append(text)
            return json.dumps({"answers": self.ai_answers.pop(0)}), 100, 40

        def fake_command(agent_id, owner_id, kind, payload=None, **_):
            sent.append({"agent_id": agent_id, "kind": kind, "payload": payload})
            return {"command_id": f"cmd-{len(sent)}", "status": "PENDING"}
        for target in (patch.object(ai_service, "call_openai", fake_ai),
                       patch("app.cloud_agents.create_command", fake_command)):
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        for timer in list(self.st.timers.values()):
            timer.cancel()
        states.discard(self.st.key)
        super().tearDown()

    def options(self, **values):
        response = self.client.put("/api/ai/live/reply-config", json={**director.ReplyOptions().model_dump(), **values})
        self.assertEqual(response.status_code, 200, response.text)

    def batch(self, *comments, ctx=None):
        ctx = ctx or context(self.owner_a["id"], self.agent_id)
        items = [{"id": i, "username": user, "nickname": nick, "comment": text, "context": ctx,
                  "asked_at": time.monotonic(), "asked_iso": "2026-10-22T00:00:00+00:00"}
                 for i, (user, nick, text) in enumerate(comments, 1)]
        self.st.sequence += 1
        run_batch(self.st, self.st.sequence, items)
        return [self.st.replies[i] for i in self.st.results[-1]["reply_ids"]]

    # --- one answer per question -------------------------------------------------------------------------

    def test_price_questions_are_answered_from_product_data_without_ai(self):
        replies = self.batch(("lan_99", "Lan", "giá bn shop"), ("hoa", "Hoa", "Áo này bao nhiêu vậy?"))
        self.assertEqual(self.prompts, [], "no AI call")
        self.assertEqual(len(replies), 1, "same intent, same slots: one answer for both")
        reply = replies[0]
        self.assertEqual((reply["source"], reply["intent"], len(reply["comments"])), ("rule", "ASK_PRICE", 2))
        self.assertIn("200k", reply["say"])
        self.assertEqual(reply["voice"], "ready", "manual mode waits for the operator")

    def test_every_question_gets_its_own_answer_and_a_left_out_one_is_asked_again(self):
        self.ai_answers = [
            [answer(1, "ASK_SIZE", "55kg mặc M là vừa nha.", slots={"weight_kg": 55}),
             answer(2, "ASK_SIZE", "70kg thì mẫu này chưa có size nha.", slots={"weight_kg": 70}),
             answer(3, "ASK_COLOR", "Màu đen để mình kiểm tra lại nha.", slots={"color": "đen"}),
             answer(9, "ASK_PRICE", "Câu không ai hỏi.")],
            [answer(4, "ASK_MATERIAL", "Vải cotton mặc mát lắm luôn.")]]  # the retry keeps the original qid
        replies = self.batch(("a", "", "55kg mặc size gì"), ("b", "", "70kg mặc size gì"),
                             ("c", "", "có màu đen không"), ("d", "", "chất vải gì vậy"))
        self.assertEqual(len(self.prompts), 2)
        self.assertIn('"text": "chất vải gì vậy"', self.prompts[1])
        self.assertNotIn("55kg mặc size gì", self.prompts[1].split("QUESTIONS:")[1], "the retry asks only the missing one")
        self.assertEqual([r["question"] for r in replies],
                         ["55kg mặc size gì", "70kg mặc size gì", "có màu đen không", "chất vải gì vậy"])
        self.assertEqual([len(r["comments"]) for r in replies], [1, 1, 1, 1], "different weights are never merged")
        self.assertEqual(replies[3]["intent"], "ASK_MATERIAL")

    def test_question_still_missing_after_the_retry_is_held_for_review(self):
        self.ai_answers = [[answer(1, "ASK_COLOR", "Có màu trắng nha.")], []]
        replies = self.batch(("a", "", "có màu trắng không"), ("b", "", "vải có nhăn không"))
        self.assertEqual(replies[1]["voice"], "review")
        self.assertIn("AI không trả lời câu này", replies[1]["review_reasons"])

    def test_code_merges_only_same_intent_and_same_details(self):
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha."), answer(2, "ASK_MATERIAL", "Cotton mát lắm."),
                            answer(3, "ASK_MATERIAL", "Chắc là cotton.", needs_review=True)]]
        replies = self.batch(("a", "", "vải gì vậy"), ("b", "", "chất liệu gì shop"), ("c", "", "vải có xù không"))
        self.assertEqual([len(r["comments"]) for r in replies], [2, 1])
        self.assertEqual(replies[1]["voice"], "review", "a reply needing review is never merged")

    def test_wrong_price_and_other_product_are_held_for_review(self):
        self.ai_answers = [[answer(1, "ASK_PRICE", "Mẫu này 250k nha.")]]
        reply = self.batch(("a", "", "cái lúc nãy giá sao"))[0]
        self.assertEqual(reply["voice"], "review")
        self.assertIn("Số tiền không khớp dữ liệu sản phẩm: 250k", reply["review_reasons"])
        self.assertIn("Hỏi về sản phẩm khác hoặc không rõ sản phẩm", reply["review_reasons"])

    def test_robotic_style_is_cleaned_and_answers_are_cached(self):
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Dạ chào anh chị, vải cotton ạ.")]]
        first = self.batch(("a", "", "vải gì vậy shop"))[0]
        self.assertEqual(first["say"], "Vải cotton ạ.")
        again = self.batch(("b", "", "Vải gì vậy shop?"))[0]
        self.assertEqual((again["source"], len(self.prompts)), ("cache", 1), "same question, same product: no new call")

    def test_noise_is_skipped_with_a_reason(self):
        self.batch(("a", "", "hi"), ("b", "", "❤️"))
        batch = self.st.results[-1]
        self.assertEqual([s["reason"] for s in batch["skipped"]], ["Không phải câu hỏi"] * 2)
        self.assertEqual((batch["reply_ids"], self.prompts), ([], []))

    # --- voice -------------------------------------------------------------------------------------------

    def test_semi_auto_speaks_after_the_cancel_window_unless_cancelled(self):
        self.options(mode="semi_auto", veto_seconds=10, rate=10)
        self.ai_answers = [[answer(1, "ASK_SIZE", "55kg mặc M là vừa nha.", slots={"weight_kg": 55}),
                            answer(2, "ASK_COLOR", "Có màu trắng nha.", slots={"color": "trắng"})]]
        first, second = self.batch(("lan_9x", "Lan 🌸", "55kg mặc size gì"), ("hoa", "Hoa", "có màu trắng k"))
        self.assertEqual((first["voice"], second["voice"]), ("scheduled", "scheduled"))
        director.cancel(self.st, second["id"])
        director.promote(self.st, first["id"])
        director.promote(self.st, second["id"])
        self.assertEqual(len(self.sent), 1)
        payload = self.sent[0]["payload"]
        self.assertEqual(payload["text"], "Lan ơi, năm mươi lăm ký mặc em là vừa nha.")
        self.assertEqual((payload["rate"], payload["duck"], payload["voice"]), (10, True, "vi-VN-HoaiMyNeural"))
        self.assertEqual((first["voice"], second["voice"], second["comment"]), ("sent", "skipped", "ready"),
                         "a cancelled reply stays answerable by comment")

    def test_auto_voice_is_paced_and_never_repeats_or_speaks_late(self):
        self.options(mode="auto", topic_cooldown_seconds=60, max_age_seconds=20)
        price = self.batch(("a", "An", "giá bn"))[0]
        self.assertEqual(price["voice"], "sent")
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.")]]
        material = self.batch(("b", "", "vải gì vậy"))[0]
        self.assertEqual(material["voice"], "queued", "the voice is still busy with the first sentence")
        repeat = self.batch(("c", "", "giá bao nhiêu"))[0]
        self.st.busy_until = 0
        director.pump(self.st)
        self.assertEqual(material["voice"], "sent")
        self.st.busy_until = 0
        director.pump(self.st)
        self.assertEqual((repeat["voice"], repeat["comment"]), ("skipped", "ready"))
        self.assertIn("Vừa nói chủ đề này", repeat["note"])
        self.ai_answers = [[answer(1, "ASK_COLOR", "Có màu trắng nha.")]]
        self.st.busy_until = time.monotonic() + 100  # the voice is busy again
        late = self.batch(("d", "", "có màu trắng k"))[0]
        self.assertEqual(late["voice"], "queued")
        late["_asked"] -= 100
        self.st.busy_until = 0
        director.pump(self.st)
        self.assertEqual(late["voice"], "skipped")
        self.assertIn("quá hạn", late["note"])
        self.assertEqual(len(self.sent), 2)

    def test_complaints_and_unsure_answers_are_never_spoken_automatically(self):
        self.options(mode="auto", min_confidence=0.8)
        self.ai_answers = [[answer(1, "COMPLAINT", "Bạn nhắn riêng shop nha."),
                            answer(2, "ASK_COLOR", "Chắc có màu đen.", confidence=0.5)]]
        complaint, unsure = self.batch(("a", "", "hàng lỗi đổi sao"), ("b", "", "có màu đen ko"))
        self.assertEqual((complaint["voice"], unsure["voice"], self.sent), ("review", "review", []))

    def test_both_channels_route_personal_questions_to_comments(self):
        self.options(mode="auto", channel="both")
        self.ai_answers = [[answer(1, "ASK_SIZE", "55kg lấy M nha.", text="55kg lấy M nha", slots={"weight_kg": 55}),
                            answer(2, "ORDER", "Chốt đơn cho bạn rồi nha.", text="chốt rồi nha")]]
        personal, order = self.batch(("lan", "Lan", "55kg mặc size gì"), ("hoa", "Hoa", "chốt 1 cái"))
        self.assertEqual((personal["route"], personal["voice"], personal["comment"]), ("comment", None, "ready"))
        self.assertEqual(personal["comment_text"], "@lan 55kg lấy M nha")
        self.assertEqual((order["route"], order["voice"], order["comment"]), ("both", "sent", "ready"))

    def test_speech_names_the_product_when_it_is_no_longer_on_air(self):
        reply = {"comments": [{"username": "lan", "nickname": "Lan"}, {"username": "hoa", "nickname": "Hoa"},
                              {"username": "an", "nickname": "An"}],
                 "product_id": 1, "product_name": "Áo thun trắng", "say": "Mẫu này 200k nha."}
        options = director.ReplyOptions()
        self.assertEqual(director.compose_speech(reply, options, 1),
                         "Lan, Hoa với mấy bạn nữa ơi, mẫu này hai trăm nghìn nha.")
        self.assertEqual(director.compose_speech(reply, options.model_copy(update={"call_names": False}), 2),
                         "Mấy bạn hỏi nè, còn Áo thun trắng lúc nãy thì mẫu này hai trăm nghìn nha.")

    # --- operator API ------------------------------------------------------------------------------------

    def test_operator_can_edit_speak_dismiss_and_stop(self):
        on = {"agent_id": self.agent_id}
        reply = self.batch(("a", "An", "giá bn"))[0]
        bad = self.client.post(f"/api/ai/live/replies/{reply['id']}/speak", params=on, json={"say": "Mẫu này 150k nha"})
        self.assertEqual(bad.status_code, 422, "a price the product does not have is refused")
        ok = self.client.post(f"/api/ai/live/replies/{reply['id']}/speak", params=on, json={"say": "Mẫu này 200k thôi nha"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.sent[-1]["payload"]["text"], "An ơi, mẫu này hai trăm nghìn thôi nha.")
        self.assertNotIn("_asked", ok.json())
        self.options(mode="semi_auto", veto_seconds=10)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.")]]
        waiting = self.batch(("b", "", "vải gì"))[0]
        stop = self.client.post("/api/ai/live/voice/stop", params=on).json()
        self.assertEqual((stop["cancelled"], stop["agent"]), (1, "sent"))
        self.assertEqual(self.sent[-1]["kind"], "tts_clear")
        self.assertEqual(waiting["voice"], "skipped")
        self.assertEqual(self.client.post(f"/api/ai/live/replies/{waiting['id']}/commented", params=on).json()["comment"], "done")
        status = self.client.get("/api/ai/live/status", params=on).json()
        self.assertEqual(status["unanswered"], 0)
        self.assertEqual({r["id"] for r in status["replies"]}, {reply["id"], waiting["id"]})
        self.assertEqual(self.client.get("/api/ai/live/status", params=on, headers=self.B).status_code, 404)

    def test_reply_options_are_per_owner_and_validated(self):
        self.options(mode="auto", persona="xưng em, gọi khách là chị")
        self.assertEqual(self.client.get("/api/ai/live/reply-config").json()["mode"], "auto")
        self.assertEqual(self.client.get("/api/ai/live/reply-config", headers=self.B).json()["mode"], "manual")
        self.assertEqual(self.client.put("/api/ai/live/reply-config", json={"mode": "loud"}).status_code, 422)
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha chị.")]]
        self.batch(("a", "", "vải gì"))
        self.assertIn("PHONG CÁCH CỦA SHOP: xưng em, gọi khách là chị", self.prompts[0])

    def test_tts_payload_options_are_validated_for_the_agent(self):
        from app.cloud_agents import validate_payload
        self.assertEqual(validate_payload("tts_speak", {"text": "hi", "rate": 5, "ttl_seconds": 20, "duck": False}),
                         {"text": "hi", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "ttl_seconds": 20.0, "duck": False})
        for bad in ({"text": "hi", "rate": 99}, {"text": "hi", "ttl_seconds": 1}, {"text": "hi", "duck": "yes"}):
            with self.assertRaises(Exception):
                validate_payload("tts_speak", bad)
        self.assertEqual(validate_payload("tts_clear", {}), {})


if __name__ == "__main__":
    unittest.main()
