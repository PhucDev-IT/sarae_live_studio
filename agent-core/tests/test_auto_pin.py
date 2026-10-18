"""Auto Pin service validation and worker behaviour without launching Chrome."""
import tempfile
import unittest
from pathlib import Path
from sarae_agent.auto_pin import AutoPinService

class AutoPinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.pin=AutoPinService(Path(self.temp.name)/"profile")

    def tearDown(self):
        self.pin.close()
        self.temp.cleanup()

    def test_only_tiktok_live_console_hosts(self):
        for url in ("http://livecenter.tiktok.com/", "https://evil.example/",
                    "https://livecenter.tiktok.com.attacker.example/"):
            with self.assertRaises(ValueError):
                self.pin.open_browser(url)

    def test_configure_products_and_interval(self):
        s=self.pin.configure(["SKU-A"," SKU-B "],15)
        self.assertEqual(s["products"],["SKU-A","SKU-B"])
        self.assertEqual(s["interval_seconds"],15)
        with self.assertRaises(ValueError):
            self.pin.configure(["SKU-A","SKU-A"],15)
        with self.assertRaises(ValueError):
            self.pin.configure(["SKU-A"],1)

    def test_start_requires_browser(self):
        self.pin.configure(["SKU-A"],20)
        with self.assertRaises(RuntimeError):
            self.pin.start()
        self.assertFalse(self.pin.status()["enabled"])
        self.assertEqual(self.pin.stop()["state"],"stopped")

if __name__=="__main__":
    unittest.main()
