"""AI replies on a Single Video LIVE: the product on air is the one the timeline pins, and a comment naming another
product ("mẫu số 5", its name or SKU) is answered about that real product, introduced with how the viewer called it,
without changing any pin."""
import json
import unittest
from unittest.mock import patch

from app import ai_service
from app import live_reply_director as director
from app.ai_live_batches import asked_context, context, states
from app.catalog import Product
from app.live_sessions import LiveSession
from app.product_refs import cart_number
from app.tiktok_chat import live_key
from support import SaasTestCase
from test_ai_live_reply import AI


class CartNumberTests(unittest.TestCase):
    def test_cart_numbers_viewers_write(self):
        cases = {"mẫu số 5 giá sao shop": 5, "sp 12 còn không": 12, "#3 bn vậy": 3, "link 7 nha": 7, "số 5 size gì": 5,
                 "cái số 4 còn màu đen ko": 4, "sản phẩm 9 giá bn": 9, "mau so 6": 6, "Mẫu 2 giá sao": 2}
        for text, number in cases.items():
            self.assertEqual(cart_number(text), number, text)

    def test_measures_counts_and_everyday_words_are_not_cart_numbers(self):
        for text in ("mẫu này 55 kg mặc được không", "mà 2 cái giá sao", "mẫu 2 màu hả shop", "size 2 còn không",
                     "mẫu số 0", "lấy 3 cái", "giá bao nhiêu", "mẫu này đẹp quá"):
            self.assertIsNone(cart_number(text), text)


class ProductRefTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        for target in (patch.object(ai_service.settings, "openai_api_key", "sk-platform-test-0000"),
                       patch.object(ai_service.settings, "gemini_api_key", "")):
            target.start()
            self.addCleanup(target.stop)
        self.assertEqual(self.client.put("/api/ai/config", json=AI).status_code, 200)
        self.ao, self.quan, self.vay = self.tiktok_products(self.owner_a, "Áo thun cotton basic", "Quần jean ống suông",
                                                           "Váy hoa nhí cổ vuông")
        with self.Session() as db:
            for pid, price in ((self.ao, 199000), (self.quan, 349000), (self.vay, 289000)):
                db.get(Product, pid).price = price
            db.commit()
        self.agent = self.activate_agent(self.owner_a, "PC-A")
        self.agent_id = self.agent["agent_id"]
        # The LIVE cart as the Agent read it: 1 Áo, 2 Quần, 3 Váy.
        account = self.console_sync(self.agent, product_ids=(self.ao, self.quan, self.vay))
        with self.Session() as db:
            session = LiveSession(name="Single", status="RUNNING", owner_id=self.owner_a["id"], agent_id=self.agent_id,
                                  tiktok_account_id=account, playback_mode="SINGLE_VIDEO_TIMELINE", current_product_id=None)
            db.add(session)
            db.commit()
            self.sid = session.id
        self.st = states(live_key(self.owner_a["id"], self.agent_id))
        self.prompts, self.ai_answers = [], []

        def fake_ai(config, text, key):
            self.prompts.append(text)
            return json.dumps({"answers": self.ai_answers.pop(0)}), 100, 40
        for target in (patch.object(ai_service, "call_openai", fake_ai),
                       patch("app.cloud_agents.create_command", lambda *a, **k: {"command_id": "c", "status": "PENDING"})):
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        for timer in list(self.st.timers.values()):
            timer.cancel()
        states.discard(self.st.key)
        super().tearDown()

    def pinning(self, product_id):
        """The Agent's heartbeat: the timeline pins `product_id` at the current position."""
        self.heartbeat(self.agent, {"running": True, "session_id": self.sid, "session_mode": "video", "connected": True,
                                    "timeline": {"session_id": self.sid, "version": None, "desired_product_id": product_id}})

    def test_the_product_on_air_is_the_one_the_timeline_pins(self):
        self.assertIsNone(context(self.owner_a["id"], self.agent_id), "nothing pinned yet: no product on air")
        self.pinning(self.ao)
        self.assertEqual(context(self.owner_a["id"], self.agent_id)["product"]["id"], self.ao)
        self.pinning(self.quan)
        self.assertEqual(context(self.owner_a["id"], self.agent_id)["product"]["id"], self.quan)
        self.pinning(None)  # an unpinned stretch keeps the last product as context
        self.assertEqual(context(self.owner_a["id"], self.agent_id)["product"]["id"], self.quan)

    def test_a_cart_number_or_a_name_points_to_a_real_product(self):
        self.pinning(self.ao)
        owner = self.owner_a["id"]
        by_number = asked_context(owner, self.agent_id, "mẫu số 2 giá bao nhiêu shop")
        self.assertEqual((by_number["product"]["id"], by_number["ref"]), (self.quan, {"label": "Mẫu số 2", "how": "cart_number"}))
        by_name = asked_context(owner, self.agent_id, "váy hoa nhí cổ vuông còn size M không")
        self.assertEqual((by_name["product"]["id"], by_name["ref"]["how"]), (self.vay, "name"))
        self.assertIsNone(asked_context(owner, self.agent_id, "mẫu số 9 giá sao"), "not in the cart: never another product")
        self.assertIsNone(asked_context(owner, self.agent_id, "áo thun cotton basic giá sao"), "the product on air: normal reply")
        self.assertIsNone(asked_context(owner, self.agent_id, "mẫu này đẹp quá"))

    def test_the_answer_is_about_the_named_product_and_says_which(self):
        self.pinning(self.ao)
        ctx = asked_context(self.owner_a["id"], self.agent_id, "mẫu số 2 chất vải gì vậy")
        self.ai_answers.append([{"qid": 1, "intent": "ASK_MATERIAL", "slots": {}, "say": "Mẫu này vải jean dày dặn nha.",
                                 "text": "Vải jean dày dặn nha", "confidence": 0.9, "needs_review": False, "other_product": True}])
        items = [{"id": 1, "username": "lan", "nickname": "Lan", "comment": "mẫu số 2 chất vải gì vậy", "context": ctx,
                  "asked_at": 0.0, "asked_iso": "2026-10-29T00:00:00+00:00"}]
        from app.ai_live_batches import run_batch
        self.st.sequence += 1
        run_batch(self.st, self.st.sequence, items)
        reply = self.st.replies[self.st.results[-1]["reply_ids"][0]]
        self.assertEqual((reply["product_id"], reply["ref_label"]), (self.quan, "Mẫu số 2"))
        self.assertEqual(reply["review_reasons"], [], "a resolved product is not 'another product' to review")
        self.assertIn("Quần jean ống suông", self.prompts[-1])
        self.assertIn("CÂU HỎI VỀ SẢN PHẨM", self.prompts[-1])
        intro, _body = director.compose_speech(reply, director.ReplyOptions(), self.ao)
        self.assertEqual(intro, "Mẫu số hai thì", "read aloud: numbers as words")
        self.assertIn("Mẫu số 2: Vải jean", director.comment_text(reply))

    def test_asking_never_changes_the_pin(self):
        self.pinning(self.ao)
        asked_context(self.owner_a["id"], self.agent_id, "mẫu số 3 giá sao")
        with self.Session() as db:
            self.assertEqual(db.get(LiveSession, self.sid).current_product_id, self.ao)


if __name__ == "__main__":
    unittest.main()
