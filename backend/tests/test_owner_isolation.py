"""Customer data is private to its OWNER: another OWNER gets 404, anonymous 401, SUPER_ADMIN 403."""
import unittest

from fastapi.routing import APIRoute

from app.identity import current_owner
from app.main import app
from support import SaasTestCase

# Modules holding customer data or controlling a customer's LIVE; all their routes must require an OWNER.
CUSTOMER_MODULES = {"app.catalog", "app.media", "app.owner_agents", "app.live", "app.product_videos", "app.media_presenters",
                    "app.live_sessions", "app.tiktok_chat", "app.ai_service", "app.ai_live_batches", "app.ai_voice",
                    "app.verification_monitor", "app.agent_playlists"}
# <img> cannot send a token; these check a signed, expiring URL instead (app/media_access.py).
PUBLIC_ROUTES = {"product_image", "presenter_image"}


def dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from dependency_calls(dep)


class RouteGuardTests(unittest.TestCase):
    def test_every_customer_route_requires_an_owner(self):
        unguarded = [f"{','.join(r.methods)} {r.path}" for r in app.routes
                     if isinstance(r, APIRoute) and r.endpoint.__module__ in CUSTOMER_MODULES
                     and r.name not in PUBLIC_ROUTES and current_owner not in set(dependency_calls(r.dependant))]
        self.assertEqual(unguarded, [])


class SaasGuardTests(SaasTestCase):
    def test_owner_scoped_apis_answer_in_saas_mode(self):
        from unittest.mock import patch
        from app.config import settings
        agent = self.activate_agent(self.owner_a, "PC-A")
        on = {"agent_id": agent["agent_id"]}
        with patch.object(settings, "sarae_saas_mode", True):
            for path in ("/api/media", "/api/live/status", "/api/live/products", "/api/agent-playlists", "/api/live/sessions"):
                self.assertEqual(self.client.get(path, params=on).status_code, 200, path)
            self.assertEqual(self.client.get("/api/media", params=on, headers=self.B).status_code, 404, "still owner-checked")
            self.assertEqual(self.client.get("/api/not-scoped-yet").status_code, 403, "unknown APIs fail closed")


class OwnerIsolationTests(SaasTestCase):
    def test_products_and_presenters_are_private(self):
        product = self.client.post("/api/products", json={"name": "Áo của A", "sku": "AO-1"}).json()
        presenter = self.client.post("/api/presenters", json={"name": "Lan"}).json()
        for path, item in (("/api/products", product), ("/api/presenters", presenter)):
            self.assertEqual(self.client.get(path, headers=self.B).json(), [])
            url = f"{path}/{item['id']}"
            self.assertEqual(self.client.get(url, headers=self.B).status_code, 404)
            self.assertEqual(self.client.put(url, json={"name": "chiếm"}, headers=self.B).status_code, 404)
            self.assertEqual(self.client.delete(url, headers=self.B).status_code, 404)
            self.assertEqual(self.client.get(url).json()["name"], item["name"], "A's row is untouched")

    def test_sku_is_unique_per_owner_only(self):
        self.assertEqual(self.client.post("/api/products", json={"name": "A", "sku": "SAME"}).status_code, 201)
        self.assertEqual(self.client.post("/api/products", json={"name": "B", "sku": "SAME"}, headers=self.B).status_code, 201)
        self.assertEqual(self.client.post("/api/products", json={"name": "A2", "sku": "SAME"}).status_code, 409)

    def test_sessions_cannot_use_or_touch_another_owners_data(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        theirs = self.client.post("/api/products", json={"name": "Áo của A"}).json()["id"]
        session = self.client.post("/api/live/sessions", json={"name": "LIVE của A", "product_ids": [theirs], "agent_id": agent["agent_id"]}).json()
        self.assertEqual(self.client.get("/api/live/sessions", headers=self.B).json(), [])
        for method, suffix, body in (("get", "", None), ("patch", "", {"name": "x"}), ("delete", "", None),
                                     ("get", "/events", None), ("post", "/duplicate", None), ("put", "/products", {"product_ids": []})):
            response = self.client.request(method.upper(), f"/api/live/sessions/{session['id']}{suffix}", json=body, headers=self.B)
            self.assertEqual(response.status_code, 404, f"{method} {suffix}")
        # B cannot put A's product into B's own session either.
        mine = self.activate_agent(self.owner_b, "PC-B")
        created = self.client.post("/api/live/sessions", json={"name": "LIVE của B", "product_ids": [theirs], "agent_id": mine["agent_id"]}, headers=self.B)
        self.assertEqual(created.status_code, 422)
        # ...nor create a session on A's Agent.
        on_a = self.client.post("/api/live/sessions", json={"name": "x", "agent_id": agent["agent_id"]}, headers=self.B)
        self.assertEqual(on_a.status_code, 404)

    def test_video_links_stay_within_owner(self):
        agent = self.activate_agent(self.owner_b, "PC-B")
        on = {"agent_id": agent["agent_id"]}
        theirs = self.client.post("/api/products", json={"name": "Áo"}).json()["id"]
        self.assertEqual(self.client.get(f"/api/product-videos/{theirs}", headers=self.B, params=on).status_code, 404)
        self.assertEqual(self.client.put(f"/api/product-videos/{theirs}", json={"media_ids": []}, headers=self.B, params=on).status_code, 404)
        self.assertEqual(self.client.get("/api/live/products", headers=self.B, params=on).json(), [])

    def test_anonymous_and_super_admin_are_refused(self):
        for path in ("/api/products", "/api/presenters", "/api/media?agent_id=x", "/api/live/sessions", "/api/live/products?agent_id=x"):
            self.assertEqual(self.client.get(path, headers=self.ANON).status_code, 401, path)
            self.assertEqual(self.client.get(path, headers=self.ADMIN).status_code, 403, path)


if __name__ == "__main__":
    unittest.main()
