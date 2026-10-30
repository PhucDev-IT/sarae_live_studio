"""An Agent closed mid-LIVE: its last heartbeat still says "running", but the LIVE console must not show it playing
(it could not be stopped either, the Agent being offline)."""
import unittest
from datetime import datetime, timedelta, timezone

from support import SaasTestCase


class OfflineAgentStatusTests(SaasTestCase):
    def test_last_heartbeat_of_an_offline_agent_is_not_playing(self):
        agent = self.activate_agent(self.owner_a, "PC-A")
        self.heartbeat(agent, {"running": True, "session_id": 1, "product_id": 3, "connected": True, "paused": True,
                               "last_status": "Đang phát video.mp4"})
        on = {"agent_id": agent["agent_id"]}
        live = self.client.get("/api/live/status", params=on).json()
        self.assertEqual((live["running"], live["connected"]), (True, True))
        from app.cloud_agents import connection
        with connection() as db:
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            db.execute("UPDATE agent_status SET last_seen=? WHERE agent_id=?", (old, agent["agent_id"]))
            db.commit()
        stale = self.client.get("/api/live/status", params=on).json()
        self.assertEqual((stale["running"], stale["paused"], stale["connected"]), (False, False, False))
        self.assertEqual((stale["session_id"], stale["product_id"]), (None, None), "nothing on air any more")
        self.assertIn("offline", stale["error"])
        self.assertNotIn("Đang phát", stale["last_status"])


if __name__ == "__main__":
    unittest.main()
