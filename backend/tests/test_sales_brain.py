"""Sales brain: templates written once answer every product; shop policies; the AI's answers become templates;
JSONL import/export for backups and bulk training."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ai_service, knowledge_cli, sales_brain
from app.catalog import Product
from support import SaasTestCase

AI = {"provider": "openai", "model": "gpt-test", "enabled": True, "max_output_tokens": 300, "max_requests_per_session": 50}
SIZE_TEMPLATE = {"intent": "ASK_SIZE", "patterns": ["{ask.weight} mặc size gì", "nặng {ask.weight} mặc gì"],
                 "say": ["{ask.weight} mặc {calc.size_for_weight} là vừa nha."], "text": ["{ask.weight} lấy {calc.size_for_weight} nha"]}


class TemplateValidationTests(unittest.TestCase):
    def test_bad_templates_are_explained(self):
        cases = {
            "200k": ({"intent": "ASK_PRICE", "patterns": ["giá bn"], "say": ["Mẫu này 200k nha"]}, "số tiền cố định"),
            "unknown space": ({"intent": "ASK_PRICE", "patterns": ["giá bn"], "say": ["{price} nha"]}, "sai cú pháp"),
            "ask without pattern": ({"intent": "ASK_SIZE", "patterns": ["size sao"], "say": ["{ask.weight} vừa"]},
                                    "không câu hỏi mẫu nào"),
            "bad intent": ({"intent": "LOUD", "patterns": ["x y"], "say": ["ok"]}, "intent"),
            "link": ({"intent": "GENERAL", "patterns": ["mua ở đâu"], "say": ["Vào shopee.vn nha"]}, "link"),
            "no say": ({"intent": "GENERAL", "patterns": ["a b"], "say": []}, "say"),
        }
        for name, (raw, reason) in cases.items():
            with self.assertRaises(ValueError, msg=name) as caught:
                sales_brain.validate_template(raw)
            self.assertIn(reason, str(caught.exception), name)
        clean = sales_brain.validate_template({**SIZE_TEMPLATE, "say": ["Dạ {ask.weight} mặc {calc.size_for_weight} nha"]})
        self.assertEqual(clean["say"], ["{ask.weight} mặc {calc.size_for_weight} nha"], "robotic opener cut")

    def test_questions_match_patterns_whatever_the_values_and_spelling(self):
        pattern = sales_brain.question_tokens("{ask.weight} mặc size gì")
        for question in ("55kg mặc size gì", "nặng 60 mặc sz j vậy shop", "48 ký mặc size gì ạ"):
            self.assertGreaterEqual(sales_brain.score(sales_brain.question_tokens(question), pattern), 0.6, question)
        for question in ("giá bn, có size L k", "mặc size gì", "khi nào đến"):
            self.assertLess(sales_brain.score(sales_brain.question_tokens(question), pattern), 0.6, question)


class SalesBrainTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(sales_brain, "_builtin", [])  # tests use their own templates only
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(ai_service.settings, "openai_api_key", "sk-platform-test-0000")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.put("/api/ai/config", json=AI)
        facts = {"category": "áo", "colors": ["trắng", "be"], "material": "cotton",
                 "sizes": [{"size": "M", "weight_min": 45, "weight_max": 55}, {"size": "L", "weight_min": 56, "weight_max": 65}],
                 "extras": {"care": "giặt máy nhẹ"}}
        with self.Session() as db:
            self.shirt = Product(name="Áo sơ mi", sku="SM1", owner_id=self.owner_a["id"], price=179000, origin_price=229000,
                                 ai_facts=json.dumps(facts, ensure_ascii=False))
            self.dress = Product(name="Váy hoa", sku="VH1", owner_id=self.owner_a["id"], price=250000,
                                 ai_facts=json.dumps({"category": "váy", "colors": ["đỏ"]}, ensure_ascii=False))
            db.add_all([self.shirt, self.dress])
            db.commit()
            self.shirt_id, self.dress_id = self.shirt.id, self.dress.id
        self.prompts, self.ai_answers = [], []

        def fake_ai(config, prompt, key):
            self.prompts.append(prompt)
            return json.dumps({"answers": self.ai_answers.pop(0)}), 60, 30
        patcher = patch.object(ai_service, "call_openai", fake_ai)
        patcher.start()
        self.addCleanup(patcher.stop)

    def template(self, body, status=201):
        response = self.client.post("/api/ai/knowledge/templates", json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def play(self, product_id, *comments):
        response = self.client.post("/api/ai/playground", json={"product_id": product_id,
                                                                 "comments": [{"comment": c} for c in comments]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["replies"]

    def test_one_template_answers_every_product_with_its_own_data(self):
        self.template({"intent": "ASK_MATERIAL", "patterns": ["chất liệu là gì vậy", "áo làm bằng chất gì"],
                       "say": ["Chất {product.material} mặc mát lắm luôn."], "text": ["chất {product.material} nha"]})
        self.template({"intent": "ASK_CARE", "patterns": ["giặt thế nào", "giặt máy được không"],
                       "say": ["Cái này {product.care} là ổn nha."]})
        reply = self.play(self.shirt_id, "áo làm bằng chất gì vậy shop")[0]
        self.assertEqual((reply["source"], reply["say"]), ("template", "Chất cotton mặc mát lắm luôn."))
        self.assertTrue(reply["template_id"])
        care = self.play(self.shirt_id, "giặt máy được không")[0]
        self.assertEqual(care["say"], "Cái này giặt máy nhẹ là ổn nha.")
        self.ai_answers = [[{"qid": 1, "intent": "ASK_CARE", "say": "Để mình kiểm tra lại nha.", "text": "check lại nha",
                             "confidence": 0.5, "needs_review": True}]]
        missing = self.play(self.dress_id, "giặt máy được không")[0]
        self.assertEqual(missing["source"], "ai", "the dress has no care fact: the template is not used")

    def test_size_by_weight_conditions_category_and_policies(self):
        self.template(SIZE_TEMPLATE)
        self.template({"intent": "ASK_COLOR", "when": "asked_color_unavailable", "patterns": ["có màu {ask.color} không"],
                       "say": ["Màu {ask.color} thì chưa có, đang có {product.colors} nha."]})
        self.template({"intent": "ASK_STYLE", "category": "váy", "patterns": ["váy dài tới đâu", "váy dài không"],
                       "say": ["Váy dài qua gối nha."]})
        self.client.put("/api/ai/knowledge/policies", json={"policies": [
            {"key": "freeship_min", "value": "đơn từ 300k"}, {"key": "ship_time", "value": "2-4 ngày"}]})
        self.template({"intent": "ASK_SHIPPING", "patterns": ["ship mấy ngày", "bao lâu nhận được hàng"],
                       "say": ["Giao {shop.ship_time} thôi, freeship {shop.freeship_min} nha."]})
        size, color, ship = self.play(self.shirt_id, "nặng 60 mặc gì", "có màu xanh không", "ship mấy ngày vậy")
        self.assertEqual(size["say"], "60kg mặc L là vừa nha.")
        self.assertIn("Sáu mươi ký mặc Lờ", size["spoken_text"])
        self.assertEqual(color["say"], "Màu xanh thì chưa có, đang có trắng với be nha.")
        self.assertEqual((ship["source"], ship["say"]), ("template", "Giao 2-4 ngày thôi, freeship đơn từ 300k nha."),
                         "a policy amount may be said")
        self.ai_answers = [[{"qid": 1, "intent": "ASK_STYLE", "say": "Áo dài vừa phải nha.", "text": "",
                             "confidence": 0.9}]]
        self.assertEqual(self.play(self.shirt_id, "váy dài không")[0]["source"], "ai", "category váy only")
        self.assertEqual(self.play(self.dress_id, "váy dài không")[0]["source"], "template")

    def test_different_templates_of_one_intent_are_never_merged(self):
        self.template({"intent": "ASK_PRICE", "patterns": ["giá bao nhiêu", "nhiêu tiền"], "say": ["Mẫu này {product.price} nha."]})
        self.template({"intent": "ASK_PRICE", "when": "has_discount", "patterns": ["có giảm giá không"],
                       "say": ["Từ {product.old_price} giảm còn {product.price} nha."]})
        replies = self.play(self.shirt_id, "áo này nhiêu tiền", "có giảm giá không")
        self.assertEqual([r["say"] for r in replies], ["Mẫu này 179k nha.", "Từ 229k giảm còn 179k nha."])

    def test_ai_answers_become_templates_for_the_next_products(self):
        self.ai_answers = [[{"qid": 1, "intent": "ASK_PROMO", "say": "Đang giảm từ 229k còn 179k nha.",
                             "text": "giảm còn 179k nha", "confidence": 0.9,
                             "template": {"say": "Đang giảm từ {product.old_price} còn {product.price} nha.",
                                          "text": "giảm còn {product.price} nha"}}]]
        from app.ai_live_batches import answer_comments, snapshot
        from app.live_reply_director import reply_options
        with self.Session() as db:
            ctx = snapshot(db.get(Product, self.shirt_id))
        ids = iter(range(1, 99))
        items = [{"id": 1, "username": "a", "nickname": "", "comment": "có đang giảm giá không shop", "context": ctx,
                  "asked_at": 0.0, "asked_iso": "x"}]
        answer_comments(self.owner_a["id"], ctx, items, reply_options(self.owner_a["id"]), log_key="t", batch_id=1,
                        next_id=lambda: next(ids))
        self.assertIn("PLACEHOLDERS:", self.prompts[0])
        learned = self.client.get("/api/ai/knowledge/templates", params={"source": "ai"}).json()["templates"]
        self.assertEqual(len(learned), 1)
        self.assertEqual((learned[0]["approved"], learned[0]["say"]),
                         (False, ["Đang giảm từ {product.old_price} còn {product.price} nha."]))
        with self.Session() as db:
            db.get(Product, self.dress_id).origin_price = 300000
            db.commit()
        reply = self.play(self.dress_id, "có đang giảm giá không shop")[0]
        self.assertEqual((reply["source"], reply["say"]), ("template", "Đang giảm từ 300k còn 250k nha."))
        self.assertEqual(len(self.prompts), 1, "the second product cost no AI call")
        approved = self.client.post(f"/api/ai/knowledge/templates/{learned[0]['id']}/approve").json()
        self.assertTrue(approved["approved"])

    def test_ai_template_with_a_fixed_price_is_not_learned(self):
        self.ai_answers = [[{"qid": 1, "intent": "ASK_PROMO", "say": "Giảm còn 179k nha.", "text": "", "confidence": 0.9,
                             "template": {"say": "Giảm còn 179k nha."}}]]
        self.play(self.shirt_id, "có giảm giá không")
        self.assertEqual(self.client.get("/api/ai/knowledge/templates", params={"source": "ai"}).json()["templates"], [])

    def test_shop_can_edit_disable_and_delete(self):
        created = self.template(SIZE_TEMPLATE)
        self.assertEqual(created["source"], "manual")
        edited = self.client.put(f"/api/ai/knowledge/templates/{created['id']}", json={
            **SIZE_TEMPLATE, "say": ["Nặng {ask.weight} thì lấy {calc.size_for_weight} nha."]}).json()
        self.assertEqual(edited["say"], ["Nặng {ask.weight} thì lấy {calc.size_for_weight} nha."])
        self.client.post(f"/api/ai/knowledge/templates/{created['id']}/toggle", json={"enabled": False})
        self.ai_answers = [[{"qid": 1, "intent": "ASK_SIZE", "say": "M nha.", "text": "", "confidence": 0.9}]]
        self.assertEqual(self.play(self.shirt_id, "50kg mặc size gì")[0]["source"], "rule",
                         "disabled template: the built-in size rule answers")
        self.assertEqual(self.client.post("/api/ai/knowledge/templates", json={**SIZE_TEMPLATE, "say": ["Giá 100k"]}).status_code, 422)
        self.assertEqual(self.client.delete(f"/api/ai/knowledge/templates/{created['id']}").json(), {"ok": True})
        self.assertEqual(self.client.get("/api/ai/knowledge/templates", headers=self.B).json()["templates"], [],
                         "another shop sees none of it")

    def test_preview_shows_rendering_and_missing_data(self):
        body = {"intent": "ASK_CARE", "patterns": ["giặt sao"], "say": ["{product.care}, form {product.form} nha."]}
        result = self.client.post("/api/ai/knowledge/templates/preview",
                                  json={**body, "product_id": self.shirt_id, "comment": "giặt sao shop"}).json()
        self.assertEqual((result["say"], result["missing"], result["matched"]), ([], ["product.form"], True))

    def test_policies_are_validated(self):
        self.assertEqual(self.client.put("/api/ai/knowledge/policies", json={"policies": [
            {"key": "Ship Time", "value": "2 ngày"}]}).status_code, 422)
        saved = self.client.put("/api/ai/knowledge/policies", json={"policies": [{"key": "cod", "value": "được kiểm hàng"}]})
        self.assertEqual(saved.json()["policies"], [{"key": "cod", "value": "được kiểm hàng", "note": ""}])
        self.assertTrue(any(s["key"] == "ship_time" for s in saved.json()["suggested"]))

    def test_jsonl_import_export_round_trip(self):
        lines = [
            '{"type":"meta","format":"sarae-knowledge","version":1}',
            '// policies',
            '{"type":"policy","key":"ship_time","value":"2-4 ngày"}',
            json.dumps({"type": "template", "id": "size.weight.basic", **SIZE_TEMPLATE}, ensure_ascii=False),
            '{"type":"template","intent":"ASK_PRICE","patterns":["giá"],"say":["300k nha"]}',
            '{"type":"product_facts","product":{"sku":"VH1"},"facts":{"material":"voan","extras":{"form":"xoè"}}}',
            '{"type":"product_qa","product":{"name":"Váy hoa"},"question":"có lót không","say":["Có lót trong nha."]}',
            '{"type":"product_qa","product":{"sku":"NOPE"},"question":"x","say":["y"]}',
            '{not json',
        ]
        text = "\n".join(lines)
        dry = self.client.post("/api/ai/knowledge/import", params={"dry_run": True}, content=text.encode(),
                               headers={**self.A, "Content-Type": "text/plain"}).json()
        self.assertEqual((dry["added"], dry["skipped"]), ({"template": 1, "policy": 1, "product_facts": 0, "product_qa": 1}, 3))
        self.assertEqual([e["line"] for e in dry["errors"]], [5, 8, 9])
        self.assertEqual(self.client.get("/api/ai/knowledge/templates").json()["templates"], [], "dry run wrote nothing")
        self.client.post("/api/ai/knowledge/import", params={"dry_run": False}, content=text.encode(),
                         headers={**self.A, "Content-Type": "text/plain"})
        self.assertEqual(self.play(self.dress_id, "có lót không")[0]["say"], "Có lót trong nha.")
        exported = self.client.get("/api/ai/knowledge/export").text
        kinds = [json.loads(line)["type"] for line in exported.splitlines()]
        self.assertEqual(kinds.count("template"), 1)
        self.assertIn("product_facts", kinds)
        self.assertIn('"form": "xoè"', exported)
        # The export imports again into another shop (backup / restore).
        with self.Session() as db:
            db.add(Product(name="Váy hoa", sku="VH1", owner_id=self.owner_b["id"], price=1))
            db.commit()
        restored = self.client.post("/api/ai/knowledge/import", params={"dry_run": False}, content=exported.encode(),
                                    headers={**self.B, "Content-Type": "text/plain"}).json()
        self.assertTrue(restored["errors"] and all("SM1" in e["error"] for e in restored["errors"]),
                        "only the product shop B does not have is skipped")
        self.assertEqual(restored["added"]["template"], 1)

    def test_cli_validates_files_without_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = Path(tmp) / "good.jsonl", Path(tmp) / "bad.jsonl"
            good.write_text(json.dumps({"type": "template", "id": "a.b.c", **SIZE_TEMPLATE}, ensure_ascii=False), encoding="utf-8")
            bad.write_text('{"type":"template","intent":"ASK_PRICE","patterns":["x"],"say":["99k"]}', encoding="utf-8")
            self.assertEqual(knowledge_cli.main(["validate", str(good)]), 0)
            self.assertEqual(knowledge_cli.main(["validate", str(bad)]), 1)


class BuiltinLibraryTests(unittest.TestCase):
    def test_the_shipped_library_is_valid(self):
        if not sales_brain.BUILTIN_FILE.is_file():
            self.skipTest("built-in library not written yet")
        records, errors = sales_brain.parse_jsonl(sales_brain.BUILTIN_FILE.read_text(encoding="utf-8"))
        self.assertEqual(errors, [])
        ids = [d["id"] for _l, kind, d in records if kind == "template"]
        self.assertEqual(len(ids), len(set(ids)), "unique ids")
        self.assertTrue(all(ids), "every built-in template has an id")


if __name__ == "__main__":
    unittest.main()
