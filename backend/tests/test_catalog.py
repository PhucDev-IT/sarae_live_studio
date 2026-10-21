import unittest

from support import SaasTestCase


class CatalogTests(SaasTestCase):
    def test_products_come_only_from_tiktok_sync(self):
        """The catalog mirrors TikTok LIVE Console: products are created by a sync, only the AI description is edited."""
        self.assertEqual(self.client.post("/api/products", json={"sku": "S1", "name": "Áo"}).status_code, 405)
        agent = self.activate_agent(self.owner_a, "PC-A")
        (pid,) = self.tiktok_products(self.owner_a, "Áo trễ vai")
        self.console_sync(agent, product_ids=(pid,))
        self.assertEqual(self.client.put(f"/api/products/{pid}", json={"name": "Đổi tên"}).status_code, 405)
        self.assertEqual(self.client.delete(f"/api/products/{pid}").status_code, 405)
        described = self.client.put(f"/api/tiktok-catalog/products/{pid}/description", json={"description": "Chất cotton"})
        self.assertEqual((described.status_code, described.json()["description"]), (200, "Chất cotton"))
        listed = self.client.get(f"/api/products/{pid}").json()
        self.assertEqual((listed["name"], listed["description"]), ("Áo trễ vai", "Chất cotton"))
        self.assertEqual(self.client.put(f"/api/tiktok-catalog/products/{pid}/description", json={"description": "x"},
                                         headers=self.B).status_code, 404, "not B's product")

    def test_sync_imports_new_cart_products_by_seller_and_product_id(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        body = {"agent_id": agent["agent_id"], "complete": True, "account": {"user_id": "7495962653689350865", "user_type": 3},
                "products": [{"name": "Áo của Seller 1", "seller_id": "111", "product_id": "1"},
                             {"name": "Áo của Seller 2", "seller_id": "222", "product_id": "1"}]}
        headers = {"X-Sarae-Agent-Key": agent["agent_credential"]}
        first = self.client.post("/api/tiktok-products/sync", json=body, headers=headers).json()
        self.assertEqual(first["imported"]["created"], 2, "an affiliate cart mixes products of several sellers")
        body["products"][0]["name"] = "Áo mới tên"
        again = self.client.post("/api/tiktok-products/sync", json=body, headers=headers).json()
        self.assertEqual((again["imported"]["created"], again["imported"]["updated"]), (0, 2), "matched by IDs, never by name")
        accounts = self.client.get("/api/tiktok-catalog/accounts").json()
        self.assertEqual([(a["user_id"], a["user_type"]) for a in accounts], [("7495962653689350865", 3)])
        self.assertEqual(self.client.get("/api/tiktok-catalog/accounts", headers=self.B).json(), [])

    def test_incomplete_or_unidentified_cart_is_refused(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        headers = {"X-Sarae-Agent-Key": agent["agent_credential"]}
        base = {"agent_id": agent["agent_id"], "account": {"user_id": "1"}}
        self.assertEqual(self.client.post("/api/tiktok-products/sync", headers=headers,
                                          json={**base, "complete": False, "products": [{"name": "A", "seller_id": "1", "product_id": "1"}]}).status_code, 422)
        self.assertEqual(self.client.post("/api/tiktok-products/sync", headers=headers,
                                          json={**base, "complete": True, "products": [{"name": "A"}]}).status_code, 422)
        self.assertEqual(self.client.post("/api/tiktok-products/sync", headers=headers,
                                          json={"agent_id": agent["agent_id"], "complete": True,
                                                "products": [{"name": "A", "seller_id": "1", "product_id": "1"}]}).status_code, 422,
                         "the logged-in account (account_info) is required")

    def test_presenters_crud(self):
        result = self.client.post("/api/presenters", json={"name": "Người giới thiệu A"})
        self.assertEqual(result.status_code, 201, result.text)
        presenter_id = result.json()["id"]
        self.assertEqual(len(self.client.get("/api/presenters?q=giới").json()), 1)
        self.assertEqual(self.client.put(f"/api/presenters/{presenter_id}", json={"name": "Người giới thiệu B", "status": "INACTIVE"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/presenters/{presenter_id}").status_code, 204)

    def test_validation(self):
        self.assertEqual(self.client.post("/api/presenters", json={"name": " "}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
