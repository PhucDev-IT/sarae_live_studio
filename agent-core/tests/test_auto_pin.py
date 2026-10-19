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


class FakePage:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False


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

    def test_login_and_verify_urls(self):
        for url in ("https://www.tiktok.com/login", "https://seller-vn.tiktok.com/account/login?x=1",
                    "https://shop.tiktok.com/verify/identity", "https://accounts.tiktok.com/x"):
            self.assertTrue(needs_login(url), url)
        for url in (CONSOLE, CONSOLE + "?from=login_page", "https://livecenter.tiktok.com/live/1"):
            self.assertFalse(needs_login(url), url)


if __name__ == "__main__":
    unittest.main()
