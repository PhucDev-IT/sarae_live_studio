"""Auto-pin TikTok LIVE Console in a dedicated Playwright-managed Chrome profile.

This module never handles CAPTCHA challenges or extracts credentials. The operator
signs into LIVE Console in the visible Chrome window. DOM selectors may require
updates when the console UI changes.
"""
from __future__ import annotations
import logging
import re
import threading
import time
from pathlib import Path

log=logging.getLogger("sarae.autopin")
LIVE_ORIGINS={"livecenter.tiktok.com","seller-vn.tiktok.com","seller.tiktokglobalshop.com","seller-us.tiktok.com"}
class AutoPinService:
    def __init__(self, profile_dir:Path):
        self.profile_dir=profile_dir
        self._lock=threading.RLock()
        self._stop=threading.Event()
        self._thread=None
        self._context=None
        self._playwright=None
        self._page=None
        self._items:list[str]=[]
        self._index=0
        self._seconds=30
        self._enabled=False
        self._last_error=""
        self._last_pin=""
        self._state="idle"

    def status(self):
        with self._lock:
            return {"state":self._state,"enabled":self._enabled,"interval_seconds":self._seconds,
                    "products":list(self._items),"current_index":self._index,
                    "last_pin":self._last_pin,"last_error":self._last_error,
                    "browser_open":self._context is not None}

    def open_browser(self,url:str):
        from urllib.parse import urlsplit
        u=urlsplit(url)
        if u.scheme!="https" or u.hostname not in LIVE_ORIGINS:
            raise ValueError("Chỉ mở TikTok LIVE Console trên tên miền được hỗ trợ")
        with self._lock:
            if self._context is not None:
                self._page.goto(url,wait_until="domcontentloaded",timeout=30000)
                return self.status()
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError("Chưa cài playwright. Cần cài dependency Agent và Chrome để dùng Auto Pin") from exc
            self.profile_dir.mkdir(parents=True,exist_ok=True)
            self._playwright=sync_playwright().start()
            try:
                self._context=self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),channel="chrome",headless=False,
                    accept_downloads=False,viewport=None)
                self._page=self._context.pages[0] if self._context.pages else self._context.new_page()
                self._page.goto(url,wait_until="domcontentloaded",timeout=30000)
                self._state="ready"
            except Exception:
                self._playwright.stop()
                self._playwright=None;self._context=None;self._page=None
                raise
        return self.status()

    def configure(self,products:list[str],interval_seconds:int):
        if not 5<=interval_seconds<=3600:raise ValueError("Chu kỳ phải từ 5 đến 3600 giây")
        cleaned=[v.strip() for v in products if v.strip()]
        if not cleaned or len(cleaned)>100:raise ValueError("Cần 1–100 mã hoặc tên sản phẩm")
        if len(set(cleaned))!=len(cleaned):raise ValueError("Danh sách sản phẩm trùng nhau")
        with self._lock:
            self._items=cleaned
            self._seconds=interval_seconds
            self._index=0
        return self.status()

    def _pin(self,term:str):
        page=self._page
        if page is None:raise RuntimeError("Chưa mở Chrome")
        if page.url.startswith("chrome://"):raise RuntimeError("Chưa mở LIVE Console")
        from urllib.parse import urlsplit
        if urlsplit(page.url).hostname not in LIVE_ORIGINS:
            raise RuntimeError("Trang hiện tại không phải TikTok LIVE Console")
        # Target a product row by its visible product name/ID and click its Pin button.
        # Avoid guessing coordinates and avoid interacting with challenge dialogs.
        row=page.get_by_text(term,exact=True).first
        if row.count()==0:raise RuntimeError("Không tìm thấy sản phẩm: "+term)
        candidate=row.locator("xpath=ancestor::*[.//button][1]")
        button=candidate.get_by_role("button",name=re.compile(r"^(pin|ghim)$",re.I))
        if button.count()!=1:
            raise RuntimeError("Không xác định được duy nhất nút Ghim cho "+term)
        button.click(timeout=5000)
        # Re-check that the button changed state, rather than reporting blind success.
        unpin=candidate.get_by_role("button",name=re.compile(r"^(unpin|bỏ ghim|bỏ ghim sản phẩm)$",re.I))
        unpin.wait_for(state="visible",timeout=5000)
        return term

    def _loop(self):
        # Playwright synchronous Browser/Context objects must be created and used
        # within the same worker thread; opened browser is initialized here.
        while not self._stop.wait(self._seconds):
            with self._lock:
                if not self._enabled:return
                term=self._items[self._index]
                try:
                    self._last_pin=self._pin(term)
                    self._last_error=""
                    self._index=(self._index+1)%len(self._items)
                    self._state="running"
                except Exception as exc:
                    self._last_error=str(exc)[:300]
                    self._state="attention"
                    self._enabled=False
                    log.warning("Auto Pin dừng: %s",exc)
                    return

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():return self.status()
            if not self._items:raise RuntimeError("Chưa có danh sách sản phẩm")
            if self._context is None:raise RuntimeError("Chưa mở Chrome LIVE Console")
            self._stop.clear();self._enabled=True;self._state="running"
            self._thread=threading.Thread(target=self._loop,name="auto-pin",daemon=True)
            self._thread.start()
        return self.status()

    def stop(self):
        self._stop.set()
        with self._lock:
            self._enabled=False;self._state="stopped"
        return self.status()
