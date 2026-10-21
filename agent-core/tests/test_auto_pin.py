"""Auto Pin: validation, the plain-Chrome login step and the hand-over to Playwright, without launching Chrome."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from sarae_agent import auto_pin
from sarae_agent.auto_pin import AutoPinService, find_chrome, needs_login

CONSOLE = "https://shop.tiktok.com/streamer/live/product/dashboard"


class FakeProc:
    def __init__(self, args):
        self.args = args
        self.pid = 4242
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        if self.alive:
            raise auto_pin.subprocess.TimeoutExpired(self.args, timeout)
        return 0


class FakeResponse:
    def __init__(self, path, body, status=200):
        self.url, self.status, self._body = "https://shop.tiktok.com" + path, status, body

    def json(self):
        return self._body


def console_responses(user_id="7494647113328657914", products=(("9", "1", "Son môi Đỏ"), ("9", "2", "Đồng hồ S9"))):
    """What the LIVE Console's own XHRs return after a reload: the logged-in account and the LIVE cart."""
    return [FakeResponse("/api/v1/streamer_desktop/account_info/get",
                         {"code": 0, "data": {"user_id": user_id, "user_name": "sarae.fashion3", "user_type": 1}}),
            FakeResponse("/api/v1/streamer_desktop/live_product/list",
                         {"code": 0, "data": {"products": [{"product_id": pid, "title": name, "seller_info": {"seller_id": seller}}
                                                           for seller, pid, name in products]}})]


class FakePage:
    def __init__(self, url, rows=None, responses=()):
        self.url = url
        self.rows = rows or []
        self.scripts = []
        self.handlers = []
        self.responses = list(responses)

    def is_closed(self):
        return False

    def on(self, event, handler):
        self.handlers.append(handler)

    def reload(self, **_options):
        self.reloads = getattr(self, "reloads", 0) + 1
        self.waits = 0
        if not getattr(self, "late_after", None) and self.reloads >= getattr(self, "answer_on_reload", 1):
            self._deliver()

    def _deliver(self):
        for response in self.responses:
            for handler in self.handlers:
                handler(response)

    def wait_for_timeout(self, _ms):
        # A console that answers its cart only after `late_after` polls (TikTok is often slower than the page).
        self.waits = getattr(self, "waits", 0) + 1
        if getattr(self, "late_after", None) and self.waits == self.late_after:
            self._deliver()

    def evaluate(self, script):
        self.scripts.append(script)
        return {"rows": self.rows, "scroll": None}


class AutoPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pin = AutoPinService(Path(self.temp.name) / "profile")

    def tearDown(self):
        self.pin.close()
        self.temp.cleanup()

    def test_only_tiktok_live_console_hosts(self):
        for url in ("http://livecenter.tiktok.com/", "https://evil.example/",
                    "https://livecenter.tiktok.com.attacker.example/"):
            with self.assertRaises(ValueError):
                self.pin.open_browser(url)
            with self.assertRaises(ValueError):
                self.pin.login_browser(url)

    def test_configure_products_and_interval(self):
        s = self.pin.configure(["SKU-A", " SKU-B "], 15)
        self.assertEqual(s["products"], ["SKU-A", "SKU-B"])
        self.assertEqual(s["interval_seconds"], 15)
        with self.assertRaises(ValueError):
            self.pin.configure(["SKU-A", "SKU-A"], 15)
        with self.assertRaises(ValueError):
            self.pin.configure(["SKU-A"], 1)

    def test_start_requires_products(self):
        with self.assertRaises(RuntimeError):
            self.pin.start()
        self.assertFalse(self.pin.status()["enabled"])
        self.assertEqual(self.pin.stop()["state"], "stopped")


class ChromePathTests(unittest.TestCase):
    ENV = {"PROGRAMFILES": "C:/Program Files", "PROGRAMFILES(X86)": "C:/Program Files (x86)",
           "LOCALAPPDATA": "C:/Users/me/AppData/Local"}

    @staticmethod
    def install(base):
        return str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")

    def test_registry_first(self):
        registered = "D:/Apps/Chrome/chrome.exe"
        self.assertEqual(find_chrome(self.ENV, exists=lambda p: True, registry=lambda: registered), registered)

    def test_standard_folders_when_registry_missing_or_stale(self):
        x86 = self.install("C:/Program Files (x86)")
        local = self.install("C:/Users/me/AppData/Local")
        found = lambda target: (lambda p: str(Path(p)) == str(Path(target)))  # noqa: E731
        self.assertEqual(Path(find_chrome(self.ENV, exists=found(x86), registry=lambda: None)), Path(x86))
        self.assertEqual(Path(find_chrome(self.ENV, exists=found(local), registry=lambda: "D:/gone/chrome.exe")),
                         Path(local))
        with self.assertRaises(RuntimeError):
            find_chrome({}, exists=lambda p: False, registry=lambda: None)


class LoginFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.launched = []

        def popen(args):
            proc = FakeProc(args)
            self.launched.append(proc)
            return proc

        self.pin = AutoPinService(Path(self.temp.name) / "profile", chrome_finder=lambda: "C:/Chrome/chrome.exe",
                                  popen=popen)
        self.pin.LOGIN_CLOSE_SECONDS = 0.2

    def tearDown(self):
        self.pin.close()
        self.temp.cleanup()

    def test_login_starts_plain_chrome_on_the_shared_profile(self):
        status = self.pin.login_browser(CONSOLE)
        args = self.launched[0].args
        self.assertEqual(args[0], "C:/Chrome/chrome.exe")
        self.assertIn(f"--user-data-dir={self.pin.profile_dir}", args)
        self.assertEqual(args[-1], CONSOLE)
        self.assertFalse(any("automation" in a or "remote-debugging" in a for a in args))
        self.assertEqual((status["state"], status["login_open"]), ("login", True))
        with self.assertRaises(RuntimeError):
            self.pin.login_browser(CONSOLE)  # one login window at a time
        self.launched[0].alive = False  # the operator closed Chrome after signing in
        self.assertEqual(self.pin.status()["state"], "logged_in")

    def test_playwright_refused_while_login_chrome_holds_profile(self):
        self.pin.login_browser(CONSOLE)
        with mock.patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
            with self.assertRaises(RuntimeError) as exc:
                self.pin.open_browser(CONSOLE)
            self.assertIn("Chrome đăng nhập vẫn đang mở", str(exc.exception))
            self.pin.configure(["SKU-A"], 20)
            with self.assertRaises(RuntimeError) as exc:
                self.pin.start()
            self.assertIn("Chrome đăng nhập vẫn đang mở", str(exc.exception))
        self.assertIsNone(self.pin._context)
        self.launched[0].alive = False
        with mock.patch.object(AutoPinService, "_profile_locked", return_value=True):  # a helper still holds it
            with self.assertRaises(RuntimeError) as exc:
                self.pin.open_browser(CONSOLE)
            self.assertIn("Chrome đăng nhập vẫn đang mở", str(exc.exception))

    def test_finish_login_closes_gracefully_or_asks_the_operator(self):
        self.pin.login_browser(CONSOLE)
        with mock.patch.object(auto_pin.subprocess, "run") as taskkill:
            with self.assertRaises(RuntimeError) as exc:
                self.pin.finish_login()  # this fake Chrome never exits
        command = taskkill.call_args.args[0]
        self.assertEqual(command[:3], ["taskkill", "/PID", "4242"])
        self.assertNotIn("/F", command)  # a normal window close, so Chrome saves the session
        self.assertIn("Chrome đăng nhập vẫn đang mở", str(exc.exception))

    def test_redirect_to_login_stops_auto_pin(self):
        self.pin.configure(["SKU-A"], 20)
        self.pin._context = object()  # Playwright already attached
        self.pin._page = FakePage("https://www.tiktok.com/login?redirect_url=" + CONSOLE)
        self.assertTrue(self.pin.start()["enabled"])
        deadline = time.monotonic() + 5
        while self.pin.status()["enabled"] and time.monotonic() < deadline:
            time.sleep(0.02)
        status = self.pin.status()
        self.assertEqual((status["enabled"], status["state"]), (False, "login_required"))
        self.assertIn("đăng nhập lại", status["last_error"])
        self.pin._context = None

    def test_scan_reads_account_and_cart_from_the_console_responses(self):
        self.pin._context = object()
        self.pin._page = FakePage(CONSOLE, responses=console_responses())
        result = self.pin.scan_products()
        self.assertEqual(result["account_profile"]["user_id"], "7494647113328657914", "account_info data.user_id, not seller_id")
        self.assertEqual(result["product_snapshot"]["complete"], True)
        self.assertEqual([(p["seller_id"], p["product_id"]) for p in result["product_snapshot"]["products"]], [("9", "1"), ("9", "2")])
        self.assertEqual(result["products"], ["Son môi Đỏ", "Đồng hồ S9"])
        self.assertEqual(self.pin._page.scripts, [], "no DOM script when the console's own response is available")
        # Old console without the product response: DOM rows are diagnostics only, never a complete snapshot.
        self.pin._page = FakePage(CONSOLE, rows=[{"name": "Váy", "pinned": False}])
        self.assertFalse(self.pin.scan_products()["product_snapshot"]["complete"])
        self.pin._page = FakePage("https://www.tiktok.com/login")
        with self.assertRaises(RuntimeError):
            self.pin.scan_products()
        self.assertEqual(self.pin.status()["state"], "login_required")
        self.pin._context = None

    def test_scan_waits_for_a_slow_console_and_reloads_once_if_needed(self):
        self.pin._context = object()
        slow = FakePage(CONSOLE, responses=console_responses())
        slow.late_after = 40  # 8 s after the page loaded: the old 5 s wait gave up here
        self.pin._page = slow
        self.assertTrue(self.pin.scan_products()["product_snapshot"]["complete"])
        silent_first = FakePage(CONSOLE, responses=console_responses())
        silent_first.answer_on_reload = 2
        self.pin._page = silent_first
        self.assertTrue(self.pin.scan_products()["product_snapshot"]["complete"])
        self.assertEqual(silent_first.reloads, 2)
        never = FakePage(CONSOLE)
        self.pin._page = never
        with self.assertRaises(RuntimeError) as raised:
            self.pin.scan_products()
        self.assertIn("TikTok chưa trả danh sách sản phẩm", str(raised.exception))
        self.pin._context = None

    def test_scan_for_another_account_keeps_the_previous_list(self):
        self.pin._context = object()
        self.pin._page = FakePage(CONSOLE, responses=console_responses())
        self.pin.scan_products("7494647113328657914")
        self.pin._page = FakePage(CONSOLE, responses=console_responses(user_id="7495962653689350865", products=(("8", "5", "Khác"),)))
        with self.assertRaises(auto_pin.AccountMismatch):
            self.pin.scan_products("7494647113328657914")
        self.assertEqual(self.pin.status()["products"], ["Son môi Đỏ", "Đồng hồ S9"], "the wrong account's cart is not kept")
        self.pin._context = None

    def test_start_needs_a_manual_sync_first(self):
        """Products found on the page are never pinned wholesale without the user's explicit sync."""
        self.pin._context = object()
        self.pin._page = FakePage(CONSOLE, [{"name": "A", "pinned": False}, {"name": "B", "pinned": False}])
        with self.assertRaises(RuntimeError):
            self.pin.start()
        self.pin.configure(["A", "B"], 15)
        with mock.patch.object(AutoPinService, "_pin", side_effect=lambda term: term):
            status = self.pin.start()
            self.assertEqual((status["enabled"], status["products"]), (True, ["A", "B"]))
            self.pin.stop()
        self.pin._context = None

    def test_scan_needs_the_controlled_chrome(self):
        with self.assertRaises(RuntimeError) as exc:
            self.pin.scan_products()
        self.assertIn("Chưa mở Chrome điều khiển", str(exc.exception))

    def test_login_and_verify_urls(self):
        for url in ("https://www.tiktok.com/login", "https://seller-vn.tiktok.com/account/login?x=1",
                    "https://shop.tiktok.com/verify/identity", "https://accounts.tiktok.com/x"):
            self.assertTrue(needs_login(url), url)
        for url in (CONSOLE, CONSOLE + "?from=login_page", "https://livecenter.tiktok.com/live/1"):
            self.assertFalse(needs_login(url), url)


if __name__ == "__main__":
    unittest.main()
