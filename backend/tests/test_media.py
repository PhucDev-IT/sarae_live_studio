"""The video library is the OWNER's active Agent library; tags and links are per (agent, video)."""
import unittest
from unittest.mock import patch

from app.catalog import Presenter, Product
from support import SaasTestCase


class MediaTests(SaasTestCase):
    def setUp(self):
        super().setUp()
        with self.Session() as db:
            db.add_all([Product(sku="A", name="Áo", owner_id=self.owner_a["id"]),
                        Product(sku="B", name="Váy", owner_id=self.owner_a["id"]),
                        Presenter(name="Lan", owner_id=self.owner_a["id"]),
                        Presenter(name="Mai", owner_id=self.owner_a["id"])])
            db.commit()

    def test_library_calls_must_name_an_owned_agent(self):
        self.assertEqual(self.client.get("/api/media").status_code, 422, "agent_id is required")
        self.assertEqual(self.client.get("/api/media?agent_id=agt_unknown").status_code, 404)
        self.assertEqual(self.client.put("/api/product-videos/1?agent_id=agt_unknown", json={"media_ids": []}).status_code, 404)

    def test_library_tags_and_metadata_come_from_the_agent(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        first, second = self.sync_videos(agent, "ao-1.mp4", "vay-1.mp4")
        videos = {v["id"]: v for v in self.client.get("/api/media", params={"agent_id": agent["agent_id"]}).json()}
        self.assertEqual(set(videos), {first, second})
        self.assertEqual((videos[first]["filename"], videos[first]["duration_seconds"], videos[first]["available"]), ("ao-1.mp4", 12.5, True))
        tagged = self.client.patch(f"/api/media/{first}", json={"tags": "mặt trước"}, params={"agent_id": agent["agent_id"]})
        self.assertEqual(tagged.json()["tags"], "mặt trước")
        self.assertEqual(self.client.get(f"/api/media?agent_id={agent['agent_id']}&q=trước").json()[0]["id"], first)
        self.assertEqual(self.client.get("/api/media/" + "0" * 64, params={"agent_id": agent["agent_id"]}).status_code, 404)

    def test_products_many_to_many_and_order(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        first, second = self.sync_videos(agent, "a.mp4", "b.mp4")
        self.assertEqual(self.client.put("/api/product-videos/1", json={"media_ids": [second]}, params={"agent_id": agent["agent_id"]}).status_code, 200)
        linked = self.client.put(f"/api/media/{first}/products", json={"product_ids": [1, 2]}, params={"agent_id": agent["agent_id"]})
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(linked.json()["product_ids"], [1, 2])
        self.assertEqual(self.client.get("/api/product-videos/1", params={"agent_id": agent["agent_id"]}).json()["media_ids"], [second, first], "new link is appended")
        self.assertEqual(self.client.put(f"/api/media/{first}/products", json={"product_ids": [2]}, params={"agent_id": agent["agent_id"]}).json()["product_ids"], [2])
        self.assertEqual(self.client.put(f"/api/media/{first}/products", json={"product_ids": [999]}, params={"agent_id": agent["agent_id"]}).status_code, 422)
        self.assertEqual(self.client.put("/api/product-videos/1", json={"media_ids": ["f" * 64]}, params={"agent_id": agent["agent_id"]}).status_code, 422)
        counts = {p["id"]: p["video_count"] for p in self.client.get("/api/live/products", params={"agent_id": agent["agent_id"]}).json()}
        self.assertEqual(counts, {1: 1, 2: 1})

    def test_presenters_many_to_many(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        (video,) = self.sync_videos(agent, "p.mp4")
        both = self.client.put(f"/api/media/{video}/presenters", json={"presenter_ids": [2, 1, 2]}, params={"agent_id": agent["agent_id"]})
        self.assertEqual(both.json()["presenter_ids"], [1, 2])
        self.assertEqual(self.client.put("/api/presenter-videos/1", json={"media_ids": []}, params={"agent_id": agent["agent_id"]}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/media/{video}", params={"agent_id": agent["agent_id"]}).json()["presenter_ids"], [2])
        self.assertEqual(self.client.get("/api/presenter-videos/2", params={"agent_id": agent["agent_id"]}).json()["media_ids"], [video])
        self.assertEqual(self.client.get("/api/presenter-videos", params={"agent_id": agent["agent_id"]}).json(), [{"presenter_id": 2, "video_count": 1}])
        self.assertEqual(self.client.delete("/api/presenters/2").status_code, 204)
        self.assertEqual(self.client.get(f"/api/media/{video}", params={"agent_id": agent["agent_id"]}).json()["presenter_ids"], [])

    def test_videos_are_private_and_rescan_goes_to_the_agent(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        (video,) = self.sync_videos(agent, "a.mp4")
        self.assertEqual(self.client.get("/api/media", headers=self.B, params={"agent_id": agent["agent_id"]}).status_code, 404, "not B's Agent")
        self.assertEqual(self.client.patch(f"/api/media/{video}", json={"tags": "x"}, headers=self.B, params={"agent_id": agent["agent_id"]}).status_code, 404)
        with patch("app.cloud_agents.rpc", return_value={"synced": 1}) as rpc:
            self.assertEqual(self.client.post("/api/media/rescan", params={"agent_id": agent["agent_id"]}).json(), {"synced": 1})
        self.assertEqual(rpc.call_args.args[:3], (agent["agent_id"], self.owner_a["id"], "media_rescan"))


if __name__ == "__main__":
    unittest.main()
