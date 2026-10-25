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
        self.assertEqual(spoken("55kg mặc size M là vừa"), "Năm mươi lăm ký mặc size Mờ là vừa.")
        self.assertEqual(spoken("Giá 1tr2, freeship HN"), "Giá một triệu hai, miễn phí ship Hà Nội.")
        self.assertEqual(spoken("Cao 1m6 lấy L nha"), "Cao một mét sáu lấy Lờ nha.")

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
        for text in ("giá bn, có size L k", "55kg mặc size gì", "cái lúc nãy giá sao", "còn bao nhiêu cái",
                     "có giảm giá không", "giá sale bao nhiêu"):
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
        self.assertEqual((again["source"], len(self.prompts)), ("bank", 1), "same question, same product: no new call")

    def test_noise_is_skipped_with_a_reason(self):
        self.batch(("a", "", "hi"), ("b", "", "❤️"))
        batch = self.st.results[-1]
        self.assertEqual([s["reason"] for s in batch["skipped"]], ["Không phải câu hỏi"] * 2)
        self.assertEqual((batch["reply_ids"], self.prompts), ([], []))

    # --- voice -------------------------------------------------------------------------------------------

    def test_live_reply_uses_saved_elevenlabs_voice_without_key_in_command(self):
        response = self.client.put("/api/ai/voice/config", json={"provider": "elevenlabs",
            "model": "eleven_flash_v2_5", "voice": "voice123", "rate": 0,
            "settings": {"stability": 0.5, "speed": 1.0}})
        self.assertEqual(response.status_code, 200, response.text)
        self.options(mode="auto")
        self.batch(("lan", "Lan", "giá bn shop"))
        payload = next(c["payload"] for c in self.sent if c["kind"] == "tts_speak")
        self.assertEqual((payload["provider"], payload["model"], payload["voice"]),
                         ("elevenlabs", "eleven_flash_v2_5", "voice123"))
        self.assertEqual(payload["settings"], {"stability": 0.5, "speed": 1.0})
        self.assertNotIn("api_key", payload)

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
        self.assertEqual(payload["text"], "Năm mươi lăm ký mặc Mờ là vừa nha.")
        self.assertNotIn("intro", payload, "no 'Lan ơi,': the voice goes straight to the answer")
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

    def test_speech_goes_straight_to_the_answer_and_names_a_product_no_longer_on_air(self):
        reply = {"comments": [{"username": "lan", "nickname": "Lan"}, {"username": "hoa", "nickname": "Hoa"}],
                 "product_id": 1, "product_name": "Áo thun trắng", "say": "Mẫu này 200k nha."}
        options = director.ReplyOptions()
        body = "Mẫu này hai trăm nghìn nha."
        self.assertEqual(director.compose_speech(reply, options, 1), (None, body), "no names, no 'mấy bạn hỏi nè'")
        self.assertEqual(director.compose_speech(reply, options, 2), ("Còn Áo thun trắng lúc nãy thì", body))
        self.assertEqual(director.spoken_text("Còn Áo thun trắng lúc nãy thì", body),
                         "Còn Áo thun trắng lúc nãy thì mẫu này hai trăm nghìn nha.")

    # --- operator API ------------------------------------------------------------------------------------

    def test_operator_can_edit_speak_dismiss_and_stop(self):
        on = {"agent_id": self.agent_id}
        reply = self.batch(("a", "An", "giá bn"))[0]
        bad = self.client.post(f"/api/ai/live/replies/{reply['id']}/speak", params=on, json={"say": "Mẫu này 150k nha"})
        self.assertEqual(bad.status_code, 422, "a price the product does not have is refused")
        ok = self.client.post(f"/api/ai/live/replies/{reply['id']}/speak", params=on, json={"say": "Mẫu này 200k thôi nha"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.sent[-1]["payload"]["text"], "Mẫu này hai trăm nghìn thôi nha.")
        self.assertNotIn("intro", self.sent[-1]["payload"], "straight to the answer, no viewer name")
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

    # --- structured facts, answer bank, preparation -------------------------------------------------------

    def set_facts(self, facts):
        response = self.client.put(f"/api/tiktok-catalog/products/{self.product_id}/ai-facts", json=facts)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["ai_facts"]

    def test_size_color_material_are_answered_from_product_facts(self):
        facts = self.set_facts({"sizes": [{"size": "m", "weight_min": 45, "weight_max": 55},
                                          {"size": "L", "weight_min": 56, "weight_max": 65}],
                                "colors": ["trắng", "đen"], "material": "cotton 100%"})
        self.assertEqual(facts["sizes"][0]["size"], "M")
        self.assertEqual(self.client.get(f"/api/products/{self.product_id}").json()["ai_facts"]["colors"], ["trắng", "đen"])
        replies = self.batch(("a", "", "50kg mặc size gì"), ("b", "", "có màu đen không"), ("c", "", "vải gì vậy shop"),
                             ("d", "", "có màu xanh ko"))
        self.assertEqual(self.prompts, [], "all four answered without the AI")
        self.assertEqual([r["source"] for r in replies], ["rule"] * 4)
        self.assertIn("M", replies[0]["say"])
        self.assertEqual(replies[0]["slots"], {"weight_kg": 50.0})
        self.assertIn("trắng", replies[3]["say"], "a color not in the list: says what there is")
        self.assertEqual(self.client.put(f"/api/tiktok-catalog/products/{self.product_id}/ai-facts",
                                         json={"sizes": [{"size": "M", "weight_min": 60, "weight_max": 50}]}).status_code, 422)
        self.assertEqual(self.client.put(f"/api/tiktok-catalog/products/{self.product_id}/ai-facts", json={},
                                         headers=self.B).status_code, 404)

    def test_facts_change_the_product_version(self):
        before = context(self.owner_a["id"], self.agent_id)["version"]
        self.set_facts({"material": "lụa"})
        after = context(self.owner_a["id"], self.agent_id)
        self.assertNotEqual(before, after["version"])
        self.assertEqual(after["product"]["facts"]["material"], "lụa")

    def test_answer_bank_survives_and_matches_close_wording_but_never_other_numbers(self):
        from app import answer_bank
        version = context(self.owner_a["id"], self.agent_id)["version"]
        answer_bank.save(self.owner_a["id"], self.product_id, version, "vải có nhăn không shop", "ASK_MATERIAL", {},
                         ["Vải ít nhăn lắm nha."], ["ít nhăn nha"], "ai")
        answer_bank.save(self.owner_a["id"], self.product_id, version, "70kg mặc vừa không", "ASK_SIZE",
                         {"weight_kg": 70}, ["70kg thì hơi chật nha."], [""], "ai")
        self.ai_answers = [[answer(2, "ASK_SIZE", "55kg mặc vừa nha.", slots={"weight_kg": 55})]]
        close, other = self.batch(("a", "", "vải này có nhăn không vậy"), ("b", "", "55kg mặc vừa không"))
        self.assertEqual((close["source"], close["say"]), ("bank", "Vải ít nhăn lắm nha."))
        self.assertEqual(other["source"], "ai", "55kg never reuses the 70kg answer")
        self.assertFalse(answer_bank.similar("vải có nhăn không", "vải không nhăn"), "negation must match")
        listed = self.client.get("/api/ai/live/answers", params={"agent_id": self.agent_id}).json()
        self.assertEqual(listed["product_id"], self.product_id)
        questions = {a["question"]: a for a in listed["answers"]}
        self.assertEqual(questions["vải có nhăn không shop"]["uses"], 1)
        self.assertIn("55kg mặc vừa không", questions, "a checked AI answer is saved for next time")
        entry = questions["70kg mặc vừa không"]["id"]
        self.assertEqual(self.client.delete(f"/api/ai/live/answers/{entry}", params={"agent_id": self.agent_id}, headers=self.B).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/ai/live/answers/{entry}", params={"agent_id": self.agent_id}).json(), {"ok": True})

    def test_operator_answer_is_approved_and_wins(self):
        from app import answer_bank
        self.ai_answers = [[answer(1, "ASK_MATERIAL", "Vải cotton nha.")]]
        reply = self.batch(("a", "", "vải gì vậy"))[0]
        ok = self.client.post(f"/api/ai/live/replies/{reply['id']}/speak", params={"agent_id": self.agent_id},
                              json={"say": "Cotton mặc mát lắm luôn á"})
        self.assertEqual(ok.status_code, 200, ok.text)
        version = context(self.owner_a["id"], self.agent_id)["version"]
        entry = [a for a in answer_bank.list_product(self.owner_a["id"], self.product_id, version)
                 if a["question"] == "vải gì vậy" and a["scope"] == "shop"][0]
        self.assertEqual((entry["source"], entry["approved"], entry["say"]), ("operator", True, ["Cotton mặc mát lắm luôn á"]))
        answer_bank.save(self.owner_a["id"], self.product_id, version, "vải gì vậy", "ASK_MATERIAL", {}, ["khác"], [], "ai")
        again = self.batch(("b", "", "vải gì vậy"))[0]
        self.assertEqual(again["say"], "Cotton mặc mát lắm luôn á", "the AI never overwrites the operator")

    def test_product_on_air_is_prepared_once_with_audio(self):
        from app.ai_live_batches import prepare
        self.options(mode="manual")
        self.ai_answers = [[]]
        prepared_items = [{"question": "áo này bn", "intent": "ASK_PRICE", "slots": {},
                           "say": ["Mẫu này 200k thôi nha.", "Dạ chào anh chị, giá 200k ạ.", "Giá 250k nha."],
                           "text": ["200k nha"]},
                          {"question": "vải gì", "intent": "ASK_MATERIAL", "say": ["Vải cotton mát nha."], "text": []},
                          {"question": "hàng lỗi đổi sao", "intent": "COMPLAINT", "say": ["Nhắn shop nha."]}]

        def fake_prepare_ai(config, text, key):
            self.prompts.append(text)
            return json.dumps({"items": prepared_items}), 300, 200
        ctx = context(self.owner_a["id"], self.agent_id)
        state = {"state": "running", "count": 0, "version": ctx["version"]}
        with patch.object(ai_service, "call_openai", fake_prepare_ai):
            prepare(self.st, ctx, False, state)
            prepare(self.st, ctx, False, state)
        self.assertEqual(len(self.prompts), 1, "a version is prepared once")
        self.assertEqual((state["state"], state["count"]), ("ready", 2), "complaints are never prepared")
        listed = self.client.get("/api/ai/live/answers", params={"agent_id": self.agent_id}).json()["answers"]
        price = [a for a in listed if a["intent"] == "ASK_PRICE"][0]
        self.assertEqual(price["say"], ["Mẫu này 200k thôi nha.", "Giá 200k ạ."], "openers cut, wrong price dropped")
        audio = [c for c in self.sent if c["kind"] == "tts_prepare"]
        self.assertEqual(len(audio), 2)
        self.assertIn("Mẫu này hai trăm nghìn thôi nha.", audio[0]["payload"]["texts"])
        found = self.batch(("x", "", "áo này bn vậy"))[0]
        self.assertEqual(found["source"], "rule", "plain price questions still use the rule")
        material = self.batch(("y", "", "vải gì"))[0]
        self.assertEqual((material["source"], material["say"]), ("bank", "Vải cotton mát nha."))

    def test_status_reports_preparation_and_agent_voice(self):
        self.client.put("/api/ai/live/config", json={"enabled": True, "window_seconds": 2, "max_comments": 8})
        with patch("app.ai_live_batches.executor.submit") as submit:
            status = self.client.get("/api/ai/live/status", params={"agent_id": self.agent_id}).json()
            self.client.get("/api/ai/live/status", params={"agent_id": self.agent_id})
        self.assertEqual(submit.call_count, 1, "started once per product version")
        self.assertEqual((status["prepared"]["state"], status["prepared"]["product_id"]), ("running", self.product_id))
        self.assertIsNone(status["agent_voice"], "no voice report from this Agent yet")

    def test_tts_payload_options_are_validated_for_the_agent(self):
        from app.cloud_agents import validate_payload
        self.assertEqual(validate_payload("tts_speak", {"text": "hi", "rate": 5, "ttl_seconds": 20, "duck": False}),
                         {"text": "hi", "voice": "vi-VN-HoaiMyNeural", "rate": 5, "ttl_seconds": 20.0, "duck": False})
        for bad in ({"text": "hi", "rate": 99}, {"text": "hi", "ttl_seconds": 1}, {"text": "hi", "duck": "yes"}):
            with self.assertRaises(Exception):
                validate_payload("tts_speak", bad)
        self.assertEqual(validate_payload("tts_clear", {}), {})
        self.assertEqual(validate_payload("tts_speak", {"text": "hi", "intro": "Lan ơi,"})["intro"], "Lan ơi,")
        self.assertEqual(validate_payload("tts_prepare", {"texts": ["a", "b"], "rate": 5}),
                         {"texts": ["a", "b"], "voice": "vi-VN-HoaiMyNeural", "rate": 5})
        for bad in ({"texts": []}, {"texts": ["x" * 451]}, {"texts": ["a"] * 41}):
            with self.assertRaises(Exception):
                validate_payload("tts_prepare", bad)


if __name__ == "__main__":
    unittest.main()
