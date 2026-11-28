"""Reply desk: viewer comments → AI reply about the product on air → voice and/or TikTok comment.

Mirrors the dev branch (docs/AI_AUTO_REPLY.md) on one machine:
- comments come from the public LIVE chat (TikTokLive, read-only, no login) of the verified TikTok account,
  or from the operator's test box (simulated, never posted to TikTok);
- deterministic filters first (own account, ignored users, duplicates, noise, plain small talk), then one AI
  call per remaining comment, using the product currently playing in OBS as context. Vietnamese viewers rarely
  type "?" ("quần bo ống ạ"), so only comments that are certainly small talk are skipped here; the AI decides
  the rest and an empty reply without review means "nothing to answer";
- two independent branches: voice (Agent VoiceEngine → OBS voice input) and comment (Chrome chat sender on the
  Studio's TikTok profile, with its own pacing, duplicate and TTL guards). One failing never affects the other;
- automatic sending waits ``confirm_seconds`` so the operator can cancel; replies that need review, simulated
  comments (comment branch) and stale questions are never sent automatically.
State is in memory for the Core's lifetime; each finished reply is also logged on the LIVE session.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from sarae_agent.errors import AgentError

from .models import StudioError
from .ai import product_brief
from .knowledge import load_shop, qa_match
from .voice_server import LocalVoiceServer, VOICE_ID

log = logging.getLogger(__name__)
SETTING_KEY = "reply"
VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")
DEFAULTS = {"auto_voice": False, "auto_comment": False, "confirm_seconds": 4, "voice": VOICES[0], "rate": 0,
            "call_name": True, "duck_video": True, "answer_all": False, "max_age_seconds": 60,
            "comment_min_interval_seconds": 8, "comment_max_per_minute": 5, "ignored_usernames": [],
            "follow_session": True}
QUESTION_TERMS = ("giá", "gia ", "bao nhiêu", "bao nhieu", "bn", "nhiêu", "size", "sz", "kg", "ký", "cân", "cao",
                  "màu", "mau ", "còn", "con hang", "hết", "ship", "giao", "freeship", "cod", "vải", "chất", "chat lieu",
                  "đổi", "trả", "bảo hành", "mua", "đặt", "chốt", "link", "giỏ", "gio hang", "mã", "voucher", "giảm",
                  "sale", "khuyến mãi", "form", "mặc", "vừa", "rộng", "chật", "dài", "ngắn", "co giãn", "mỏng", "dày",
                  "nóng", "giặt", "xem", "mẫu", "khi nào", "bao lâu", "ở đâu", "sao", "không ạ", "ko ạ", "k ạ",
                  "ib", "inbox", "tư vấn", "tu van", "check", "nhiu", "bnhieu", "thế nào", "như nào", "nhu nao",
                  "lên form", "có không", "được không", "đc không", "dc ko", "đc k", "dc k", "số đo", "eo", "vòng")
# "… không?", "… ko", "… k ạ", "… hông shop": a yes/no question without "?".
QUESTION_END = re.compile(r"(?:^|\s)(?:không|khong|ko|k|hông|hong|hok|hk|khum|chưa|chua)"
                          r"(?:\s+(?:ạ|a|vậy|v|vay|nhỉ|nhi|nhé|shop|sh|chị|chi|c|em|e|ah))?\s*[.!]*$")
NOISE = re.compile(r"^[\W_]*$|^(.)\1*$", re.UNICODE)
# Syllables that make up praise, greetings, thanks, follows and cheers. A comment skips the AI only when EVERY
# syllable is one of these (after mentions and punctuation are removed) and no question signal is present:
# an unknown word ("bo ống", "chun", "túi") always reaches the AI.
SMALL_TALK = frozenset("""
đẹp dep xinh xắn xan dễ de thương thuong cute xịn xin sang chảnh chanh tuyệt tuyet vời voi yêu yeu iu love
mê me thích thich ưng ung hợp hop xỉu xiu
hello helo hi hí chào chao alo lô lo hú
ok oke okie oki okela uk ừ ừm um vâng vang dạ da yes
cảm cam ơn on thanks thank tks thks
follow fl flw theo dõi doi ủng ung hộ ho tim thả tha like share cho
chúc chuc may mắn man đông dong khách khach ạ a ơi oi ah à á nha nhé nhe nhá na ne nè nhỉ nhi luôn luon
quá qua lắm lam ghê ghe thế the vậy vay ghê wow woa ui uii ôi oi trời troi
shop sh shopp em e chị chi c anh a mn mọi moi người nguoi cả ca nhà nha bạn ban bé be idol
rồi roi nữa nua cũng cung vào vao live lai lại đây day hôm hom nay
haha hihi hehe huhu kaka kkk hjhj good nice great
""".split())
LAUGH = re.compile(r"^(?:h[aiêeo]|k|j|hj)+$")


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _base(char: str) -> str:
    return unicodedata.normalize("NFD", char)[0]


def is_small_talk(text: str) -> bool:
    """Praise, greetings, thanks or cheers only: nothing a seller needs to answer."""
    if is_question(text):
        return False
    t = re.sub(r"@\S+", " ", normalize(text))
    words = re.sub(r"[^\w]+", " ", t).split()
    if not words:
        return True

    def known(word: str) -> bool:
        short = word  # "đẹppp", "okkk", "quáaa": a stretched last letter, accented or not
        while len(short) > 1 and _base(short[-1]) == _base(short[-2]):
            short = short[:-1] if _base(short[-1]) == short[-1] else short[:-2] + short[-1]
        return word in SMALL_TALK or short in SMALL_TALK or bool(LAUGH.match(word))

    return all(known(word) for word in words)


def is_question(text: str) -> bool:
    t = normalize(text)
    # Words without punctuation: short terms ("ib", "eo", "giá") must be whole words, not parts of other words.
    padded = f" {' '.join(re.sub(r'[^\w]+', ' ', t).split())} "
    return ("?" in t or any(f" {term} " in padded if len(term) <= 3 else term in padded for term in QUESTION_TERMS)
            or bool(QUESTION_END.search(t)) or bool(re.search(r"\d+\s*(kg|ký|ki|cm|m\d|tuổi)", t)))


def validate_settings(params: dict, current: dict) -> dict:
    unknown = set(params) - set(DEFAULTS)
    if unknown:
        raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
    value = {**DEFAULTS, **current, **params}
    for key in ("auto_voice", "auto_comment", "call_name", "duck_video", "answer_all", "follow_session"):
        if not isinstance(value[key], bool):
            raise StudioError("VALIDATION_ERROR", f"{key} must be true or false.", {"field": key})
    limits = {"confirm_seconds": (0, 30), "rate": (-30, 50), "max_age_seconds": (10, 300),
              "comment_min_interval_seconds": (4, 120), "comment_max_per_minute": (1, 20)}
    for key, (low, high) in limits.items():
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise StudioError("VALIDATION_ERROR", f"{key} must be {low} to {high}.", {"field": key})
    voice = value["voice"]
    prefix, _, cloned = voice.partition(":") if isinstance(voice, str) else ("", "", "")
    if not isinstance(voice, str) or (voice not in VOICES and not (prefix in ("local", "remote") and VOICE_ID.fullmatch(cloned))):
        raise StudioError("VALIDATION_ERROR", "Unknown voice.", {"field": "voice"})
    names = value["ignored_usernames"]
    if not isinstance(names, list) or len(names) > 100:
        raise StudioError("VALIDATION_ERROR", "ignored_usernames must list at most 100 names.", {"field": "ignored_usernames"})
    clean = []
    for name in names:
        name = str(name).strip().lstrip("@").casefold()
        if not re.fullmatch(r"[a-z0-9._]{2,24}", name):
            raise StudioError("VALIDATION_ERROR", f"Invalid TikTok username: {name[:30]}", {"field": "ignored_usernames"})
        if name not in clean:
            clean.append(name)
    value["ignored_usernames"] = clean
    return {key: value[key] for key in DEFAULTS}


class ChatReader:
    """Public LIVE chat of one TikTok account (TikTokLive), on its own asyncio thread."""

    def __init__(self, on_comment: Callable[[dict], None], on_state: Callable[[], None], client_type=None):
        self._on_comment = on_comment
        self._on_state = on_state
        self._client_type = client_type
        self._lock = threading.RLock()
        self.username = ""
        self.status = "idle"
        self.error = ""
        self._generation = 0
        self._client = None
        self._loop = None
        self.reconnects = 0
        # Seconds before each automatic reconnect after an unexpected drop (the last one repeats).
        self.backoff = (2, 5, 10, 20, 30)

    def snapshot(self) -> dict:
        with self._lock:
            return {"username": self.username, "status": self.status, "error": self.error, "reconnects": self.reconnects}

    def start(self, username: str) -> None:
        with self._lock:
            if self.status in ("connecting", "connected", "reconnecting"):
                raise StudioError("CHAT_RUNNING", "Đang đọc chat. Hãy dừng trước khi đổi tài khoản.")
            client_type = self._client_type
            if client_type is None:
                try:
                    from TikTokLive import TikTokLiveClient as client_type
                except ImportError as exc:
                    raise StudioError("CHAT_UNAVAILABLE", "Studio Core chưa cài TikTokLive.") from exc
            self._generation += 1
            generation = self._generation
            self.username, self.status, self.error = username, "connecting", ""
            self.reconnects = 0
        self._spawn(generation, username, client_type)
        self._on_state()

    def _spawn(self, generation: int, username: str, client_type) -> None:
        threading.Thread(target=self._worker, args=(generation, username, client_type), name="studio-chat",
                         daemon=True).start()

    def _retry(self, generation: int, username: str, client_type, attempt: int) -> None:
        """An unexpected drop (network, TikTok closing the socket): connect again while nobody pressed Stop."""
        delay = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
        log.warning("LIVE chat dropped; reconnecting in %ss (attempt %s)", delay, attempt)

        def later():
            time.sleep(delay)
            with self._lock:
                if generation != self._generation or self.status != "reconnecting":
                    return  # stopped or restarted meanwhile
                self.status = "connecting"
            self._on_state()
            self._spawn(generation, username, client_type)
        threading.Thread(target=later, name="studio-chat-retry", daemon=True).start()

    def _worker(self, generation: int, username: str, client_type) -> None:
        from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = client_type(unique_id="@" + username)
        with self._lock:
            if generation != self._generation:
                loop.close()
                return
            self._client, self._loop = client, loop

        @client.on(ConnectEvent)
        async def on_connect(_event):
            with self._lock:
                if generation == self._generation:
                    self.status, self.error = "connected", ""
            self._on_state()

        @client.on(CommentEvent)
        async def on_comment(event):
            if generation != self._generation:
                return
            user = event.user
            self._on_comment({
                "msg_id": str(getattr(getattr(event, "common", None), "msg_id", None) or getattr(event, "msg_id", "") or ""),
                "username": str(getattr(user, "unique_id", "") or ""),
                "nickname": str(getattr(user, "nickname", "") or ""),
                "user_id": str(getattr(user, "id", "") or getattr(user, "user_id", "") or ""),
                "comment": str(event.comment or ""),
            })

        @client.on(DisconnectEvent)
        async def on_disconnect(_event):
            with self._lock:
                if generation == self._generation and self.status != "stopping":
                    self.status = "disconnected"
            self._on_state()

        failure = ""
        try:
            loop.run_until_complete(client.connect())
        except Exception as exc:  # noqa: BLE001 - offline user, network, TikTok changes
            failure = f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            retry = 0
            with self._lock:
                if generation == self._generation:
                    self._client = self._loop = None
                    if self.status == "stopping":
                        self.status = "idle"
                    else:
                        # Not stopped by the operator: keep reading the chat.
                        self.reconnects += 1
                        retry = self.reconnects
                        self.status = "reconnecting"
                        self.error = failure or "TikTok đã ngắt kết nối chat."
            loop.close()
            self._on_state()
            if retry:
                self._retry(generation, username, client_type, retry)

    def stop(self) -> None:
        with self._lock:
            if self.status == "reconnecting":
                self._generation += 1  # cancels the pending reconnect
            if self.status not in ("connecting", "connected"):
                self.status = "idle"
                return
            self.status = "stopping"
            client, loop = self._client, self._loop
        if client is not None and loop is not None and loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
            except RuntimeError:
                pass
        self._on_state()


class ReplyDesk:
    MAX_ENTRIES = 200
    MAX_QUEUE = 30
    SPAM_SECONDS = 20
    # What one viewer asked and was told, so "vậy lấy size nào" follows "em 52kg" (memory only, per Core run).
    HISTORY_TURNS = 4
    HISTORY_SECONDS = 900

    def __init__(self, service, ai, live, tiktok, *, emit: Callable[[str, dict], None] = lambda *_: None,
                 voice=None, reader_client_type=None, clock: Callable[[], float] = time.monotonic,
                 start_threads: bool = True, voice_server=None):
        self.service, self.ai, self.live, self.tiktok = service, ai, live, tiktok
        self.emit = emit
        self._clock = clock
        self._voice = voice
        self.voice_server = voice_server or LocalVoiceServer()
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._entries: deque[dict] = deque(maxlen=self.MAX_ENTRIES)
        self._queue: deque[int] = deque()
        self._seq = 0
        self._seen_msg: dict[str, float] = {}
        self._seen_text: dict[tuple[str, str], float] = {}
        self._history: dict[str, deque] = {}
        self._closed = False
        self._comment_busy = False
        self.stats = {"received": 0, "ignored": 0, "skipped": 0, "answered": 0, "spoken": 0, "commented": 0,
                      "failed": 0, "expired": 0}
        self.reader = ChatReader(self._on_chat, self._changed, reader_client_type)
        # Chat started by a LIVE session (not by the operator) stops with that session.
        self._chat_by_session = False
        listeners = getattr(live, "session_listeners", None)
        if isinstance(listeners, list):
            listeners.append(self._on_session)
        self._threads = []
        if start_threads:
            threading.Thread(target=self.voice_server.refresh, name="studio-voice-catalog", daemon=True).start()
            for target, name in ((self._ai_worker, "studio-reply-ai"), (self._scheduler, "studio-reply-send")):
                thread = threading.Thread(target=target, name=name, daemon=True)
                thread.start()
                self._threads.append(thread)

    # --- helpers ---------------------------------------------------------------------------
    @property
    def voice(self):
        if self._voice is None:
            from sarae_agent.voice.tts import VoiceEngine
            from sarae_agent.voice.providers import default_registry
            registry = default_registry()
            registry.register(getattr(self.voice_server, "provider", "local_voice"), self.voice_server)
            self._voice = VoiceEngine(self.live.config, self.live.obs, registry=registry)
        return self._voice

    def settings(self) -> dict:
        try:
            return validate_settings({}, self.service.get_setting(SETTING_KEY, DEFAULTS))
        except StudioError:
            return dict(DEFAULTS)

    def _changed(self) -> None:
        self.emit("studio.replies.changed", {})

    def _entry(self, entry_id: object) -> dict:
        if isinstance(entry_id, bool) or not isinstance(entry_id, int):
            raise StudioError("VALIDATION_ERROR", "id must be a reply number.", {"field": "id"})
        for entry in self._entries:
            if entry["id"] == entry_id:
                return entry
        raise StudioError("NOT_FOUND", "Câu trả lời này không còn trong bàn trả lời.")

    def _public(self, entry: dict) -> dict:
        now = self._clock()
        result = {key: value for key, value in entry.items() if key not in {"at", "auto_at"}}
        result["age_seconds"] = round(now - entry["at"])
        result["auto_in"] = max(0, round(entry["auto_at"] - now, 1)) if entry.get("auto_at") else None
        return result

    def _on_session(self, session: dict, started: bool) -> None:
        """Follow the LIVE session: read its chat while it plays (docs/AI_AUTO_REPLY.md, dev behaviour)."""
        if started:
            if not self.settings()["follow_session"] or self.reader.snapshot()["status"] in ("connecting", "connected", "reconnecting"):
                return
            account = self.service.tiktok_account() or {}
            username = (account.get("user_name") or "").strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", username):
                return
            self.reader.start(username)
            self._chat_by_session = True
        elif self._chat_by_session:
            self._chat_by_session = False
            self.reader.stop()

    def _product_on_air(self) -> str | None:
        current = getattr(self.live, "current_product_id", None)
        try:
            return current() if callable(current) else None
        except Exception:  # noqa: BLE001 - no context is better than a failed reply
            return None

    def _viewer_key(self, entry: dict) -> str:
        return (entry["username"] or entry["nickname"] or "").casefold()

    def _remember(self, entry: dict) -> None:
        """Called under the lock once the AI (or the shop's Q&A) has answered."""
        key = self._viewer_key(entry)
        if not key:
            return
        turns = self._history.setdefault(key, deque(maxlen=self.HISTORY_TURNS))
        turns.append({"at": self._clock(), "id": entry["id"], "comment": entry["comment"], "reply": entry["reply"],
                      "product_id": entry["product_id"]})

    def _history_for(self, entry: dict) -> list[dict] | None:
        """This viewer's recent turns for the AI, oldest first; stale ones are forgotten."""
        now = self._clock()
        with self._lock:
            for key in [k for k, turns in self._history.items() if not turns or now - turns[-1]["at"] > self.HISTORY_SECONDS]:
                del self._history[key]
            turns = [turn for turn in self._history.get(self._viewer_key(entry), ())
                     if turn["id"] != entry["id"] and now - turn["at"] <= self.HISTORY_SECONDS]
        return [{"khach": turn["comment"], "shop": turn["reply"] or None,
                 "cung_mau_dang_live": turn["product_id"] == entry["product_id"]} for turn in turns] or None

    def _others(self, product_id: str | None) -> list[dict] | None:
        """The rest of today's lineup (and catalog) for quick answers; nothing here ever pins a product."""
        session = None
        try:
            find = getattr(self.live, "_live_session", None)
            session = find() if callable(find) else None
            lineup = self.service.lineup((session or {}).get("video_ids") or [], exclude_id=product_id)
        except Exception:  # noqa: BLE001 - a reply without the lineup is better than no reply
            log.warning("Could not read the LIVE lineup for the AI.", exc_info=True)
            return None
        others = [product_brief(p, on_air_today=True) for p in lineup["session"]]
        others += [product_brief(p, on_air_today=False) for p in lineup["others"]]
        return others or None

    # --- intake ----------------------------------------------------------------------------
    def _on_chat(self, message: dict) -> None:
        self.ingest(message, simulated=False)

    def ingest(self, message: dict, *, simulated: bool) -> dict:
        settings = self.settings()
        now = self._clock()
        text = " ".join(str(message.get("comment") or "").split())[:500]
        username = str(message.get("username") or "").strip().lstrip("@")[:60]
        with self._lock:
            self._prune(now)
            self._seq += 1
            entry = {"id": self._seq, "msg_id": str(message.get("msg_id") or "")[:80], "username": username,
                     "nickname": str(message.get("nickname") or "")[:60] or username,
                     "user_id": str(message.get("user_id") or "")[:40], "comment": text, "simulated": simulated,
                     "created_at": datetime.now(timezone.utc).isoformat(), "at": now, "stage": "queued",
                     "reason": "", "product_id": None, "intent": None, "reply": "", "needs_review": False,
                     "voice": None, "comment_delivery": None, "auto_at": None, "auto": [], "usage": None,
                     "edited": False}
            self.stats["received"] += 1
            account = self.service.tiktok_account() if not simulated else None
            own = account and username.casefold() in {(account.get("user_name") or "").casefold()}
            if entry["msg_id"] and entry["msg_id"] in self._seen_msg:
                return self._public(entry)  # the same chat event delivered twice: not shown again
            if entry["msg_id"]:
                self._seen_msg[entry["msg_id"]] = now
            self._entries.appendleft(entry)
            text_key = (username.casefold(), normalize(text))
            if not text:
                self._finish(entry, "ignored", "Bình luận trống.")
            elif own:
                self._finish(entry, "ignored", "Tin nhắn của chính tài khoản đang LIVE.")
            elif username.casefold() in settings["ignored_usernames"]:
                self._finish(entry, "ignored", "Người dùng nằm trong danh sách bỏ qua.")
            elif not simulated and text_key in self._seen_text and now - self._seen_text[text_key] < self.SPAM_SECONDS:
                self._finish(entry, "ignored", "Cùng người, cùng nội dung trong 20 giây.")
            elif NOISE.match(text) or len(text) < 2:
                self._finish(entry, "skipped", "Chỉ là biểu tượng hoặc ký tự lặp.")
            elif not settings["answer_all"] and is_small_talk(text):
                self._finish(entry, "skipped", "Chỉ là lời khen, chào hoặc cảm ơn.")
            elif not self.ai.config()["config"]["enabled"] and not qa_match(text, load_shop(self.service)["qa"]):
                self._finish(entry, "skipped", "AI đang tắt.")
            else:
                self._seen_text[text_key] = now
                while len(self._queue) >= self.MAX_QUEUE:
                    dropped = self._entry(self._queue.popleft())
                    self._finish(dropped, "expired", "Hàng chờ đầy; bỏ câu cũ nhất.")
                self._queue.append(entry["id"])
                self._wake.notify_all()
        self._changed()
        return self._public(entry)

    def _finish(self, entry: dict, stage: str, reason: str) -> None:
        entry["stage"], entry["reason"] = stage, reason
        if stage in self.stats:
            self.stats[stage] += 1

    def _prune(self, now: float) -> None:
        for store, keep in ((self._seen_msg, 600), (self._seen_text, self.SPAM_SECONDS)):
            for key in [k for k, t in store.items() if now - t > keep]:
                del store[key]

    # --- AI ----------------------------------------------------------------------------------
    def _ai_worker(self) -> None:
        while True:
            with self._wake:
                while not self._closed and not self._queue:
                    self._wake.wait(1)
                if self._closed:
                    return
                entry = self._entry(self._queue.popleft())
            self.answer(entry)

    def answer(self, entry: dict) -> None:
        settings = self.settings()
        with self._lock:
            if self._clock() - entry["at"] > settings["max_age_seconds"]:
                self._finish(entry, "expired", "Câu hỏi đã quá cũ khi tới lượt AI.")
                self._changed()
                return
            entry["stage"] = "thinking"
            # The product the timeline has pinned for the current position of the video on air.
            entry["product_id"] = self._product_on_air()
        self._changed()
        try:
            params = {"comment": entry["comment"], "username": entry["nickname"] or entry["username"]}
            if entry["product_id"]:
                params["product_id"] = entry["product_id"]
            qa = qa_match(entry["comment"], load_shop(self.service)["qa"])
            if qa:
                # The shop's own ready answer: sent at once, no AI call, no review.
                result = {"intent": "GENERAL", "reply": qa["answer"], "needs_review": False,
                          "reason": "Q&A của shop: " + ", ".join(qa["keywords"]), "usage": None}
            else:
                result = self.ai.reply(params, history=self._history_for(entry), others=self._others(entry["product_id"]))
        except StudioError as exc:
            with self._lock:
                self._finish(entry, "failed", exc.message)
            self._changed()
            return
        with self._lock:
            if entry["stage"] == "dismissed":
                self._changed()
                return
            entry.update({"intent": result["intent"], "needs_review": result["needs_review"],
                          "reason": result["reason"], "usage": result["usage"]})
            if not entry["edited"]:
                entry["reply"] = result["reply"]
            self._remember(entry)
            if not result["reply"] and not result["needs_review"]:
                # The AI read the comment and found nothing to answer: hidden like small talk, never a blank card.
                entry["stage"] = "skipped"
                entry["reason"] = "AI: không cần trả lời" + (f" ({result['reason']})" if result["reason"] else "")
                self.stats["skipped"] += 1
                self._changed()
                return
            self.stats["answered"] += 1
            if not result["reply"]:
                entry["stage"] = "review"
            elif result["needs_review"]:
                entry["stage"] = "review"
            else:
                entry["stage"] = "ready"
                auto = [branch for branch, on in (("voice", settings["auto_voice"]),
                                                  ("comment", settings["auto_comment"] and not entry["simulated"])) if on]
                if auto:
                    entry["auto"] = auto
                    entry["auto_at"] = self._clock() + settings["confirm_seconds"]
                    self._wake.notify_all()
        self._changed()

    # --- delivery ----------------------------------------------------------------------------
    def _scheduler(self) -> None:
        while True:
            with self._wake:
                if self._closed:
                    return
                now = self._clock()
                due = [e for e in self._entries if e.get("auto_at") and e["auto_at"] <= now and e["auto"]]
                for entry in due:
                    entry["auto_at"] = None
                if not due:
                    self._wake.wait(0.3)
                    continue
            for entry in due:
                branches, entry["auto"] = entry["auto"], []
                if "voice" in branches:
                    self._speak(entry, entry["reply"])
                if "comment" in branches:
                    threading.Thread(target=self._comment, args=(entry, entry["reply"]), daemon=True).start()

    def _speak(self, entry: dict, text: str) -> None:
        settings = self.settings()
        with self._lock:
            if self._clock() - entry["at"] > settings["max_age_seconds"]:
                entry["voice"] = {"status": "EXPIRED", "detail": "Câu hỏi đã quá cũ."}
                self._changed()
                return
        name = (entry["nickname"] or "").strip()
        # The viewer's name is its own cached clip, so the answer body is reused for everyone.
        intro = f"{name[:40]} ơi," if settings["call_name"] and name else None
        try:
            voice = settings["voice"]
            options = {}
            prefix = getattr(self.voice_server, "prefix", "local:")
            if voice.startswith(("local:", "remote:")) and not voice.startswith(prefix):
                # Chosen under the other voice mode (local dev server vs. Sarae server): the LIVE still speaks.
                log.warning("Giọng %s không dùng được ở chế độ giọng hiện tại; đọc bằng giọng Edge %s.", voice, VOICES[0])
                voice = VOICES[0]
            elif voice.startswith("remote:"):
                # Unavailable server: the provider says the line with Edge (remote_voice.py), never fails the LIVE.
                voice = voice[7:]
                options = {"provider": self.voice_server.provider, "model": "live_fast",
                           "settings": {"revision": self.voice_server.revision(voice)}}
            elif voice.startswith("local:"):
                if not self.voice_server.catalog()["voice_server"]["online"]:
                    raise AgentError("TTS_UNAVAILABLE", "Giọng local chưa sẵn sàng. Bật voice server rồi bấm Làm mới giọng.")
                voice = voice[6:]
                options = {"provider": "local_voice", "model": "live_fast", "settings": {"revision": self.voice_server.revision(voice)}}
            result = self.voice.speak(text, voice, rate=settings["rate"], duck=settings["duck_video"], **options,
                                      intro=intro, ttl_seconds=float(settings["max_age_seconds"]))
            status = {"status": "QUEUED", "detail": f"Vị trí {result.get('position')} trong hàng đọc"}
            with self._lock:
                self.stats["spoken"] += 1
        except AgentError as exc:
            status = {"status": "FAILED", "detail": exc.message}
            with self._lock:
                self.stats["failed"] += 1
        with self._lock:
            entry["voice"] = status
            if entry["stage"] in ("ready", "review"):
                entry["stage"] = "done"
        self._log(entry)
        self._changed()

    def _comment(self, entry: dict, text: str) -> None:
        settings = self.settings()
        with self._lock:
            if self._comment_busy:
                entry["comment_delivery"] = {"status": "RATE_LIMITED", "error": "Đang gửi một bình luận khác."}
                self._changed()
                return
            self._comment_busy = True
            entry["comment_delivery"] = {"status": "SENDING", "error": None}
        self._changed()
        try:
            account = self.service.tiktok_account()
            if entry["simulated"]:
                result = {"status": "FAILED", "error": "Bình luận thử không được gửi lên TikTok."}
            elif not account:
                result = {"status": "FAILED", "error": "Chưa xác minh tài khoản TikTok. Hãy đồng bộ sản phẩm trước."}
            else:
                result = self.tiktok.chat_send({
                    "reply_id": entry["id"], "session_id": 1, "expected_user_id": account["user_id"],
                    "ttl_seconds": max(5, settings["max_age_seconds"] - (self._clock() - entry["at"])), "text": text,
                    "target": {"msg_id": entry["msg_id"] or None, "user_id": entry["user_id"] or None,
                               "username": entry["username"] or None, "nickname": entry["nickname"] or None,
                               "comment": entry["comment"]},
                    "min_interval_seconds": settings["comment_min_interval_seconds"],
                    "max_per_minute": settings["comment_max_per_minute"]})
        except StudioError as exc:
            result = {"status": "FAILED", "error": exc.message}
        except Exception as exc:  # noqa: BLE001 - the chat sender reports, never crashes the desk
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        with self._lock:
            self._comment_busy = False
            entry["comment_delivery"] = {"status": result.get("status"), "mode": result.get("mode"),
                                         "error": result.get("error")}
            if result.get("status") in ("CONFIRMED", "SUBMITTED"):
                self.stats["commented"] += 1
                if entry["stage"] in ("ready", "review"):
                    entry["stage"] = "done"
            else:
                self.stats["failed"] += 1
        self._log(entry)
        self._changed()

    def _log(self, entry: dict) -> None:
        session = self.live._live_session() if self.live is not None else None
        if session is None:
            return
        try:
            self.service.record_live_event(session["id"], "REPLY", entry.get("product_id"), {
                "comment": entry["comment"][:200], "reply": entry["reply"][:300], "username": entry["username"],
                "voice": (entry.get("voice") or {}).get("status"),
                "comment_delivery": (entry.get("comment_delivery") or {}).get("status")})
        except StudioError:
            pass

    # --- operator API ------------------------------------------------------------------------
    def status(self, params: dict | None = None) -> dict:
        with self._lock:
            entries = [self._public(entry) for entry in self._entries]
            stats, queued = dict(self.stats), len(self._queue)
        account = self.service.tiktok_account()
        voice = self._voice.status() if self._voice is not None else None
        catalog = self.voice_server.catalog()
        return {"settings": self.settings(), "chat": self.reader.snapshot(), "entries": entries, "stats": stats,
                "queued": queued, "voice": voice,
                "voices": [v["id"] for v in catalog["voice_options"]], **catalog,
                "account_username": (account or {}).get("user_name") or None,
                "product_id": self._product_on_air()}

    def configure(self, params: dict) -> dict:
        self.service.set_setting(SETTING_KEY, validate_settings(params, self.settings()))
        return self.status()

    def voices(self, params: dict | None = None) -> dict:
        if params:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.")
        self.voice_server.refresh()
        return self.status()

    def voices_start(self, params: dict | None = None) -> dict:
        if params:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.")
        try:
            self.voice_server.start()
        except AgentError as exc:
            raise StudioError(exc.code, exc.message) from exc
        return self.status()

    def chat_start(self, params: dict) -> dict:
        unknown = set(params) - {"username"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        username = params.get("username") or (self.service.tiktok_account() or {}).get("user_name") or ""
        username = str(username).strip().lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9._]{2,24}", username):
            raise StudioError("VALIDATION_ERROR", "Nhập username TikTok hợp lệ, ví dụ @tenkenh.", {"field": "username"})
        self.reader.start(username)
        self._chat_by_session = False
        return self.status()

    def chat_stop(self, params: dict | None = None) -> dict:
        self._chat_by_session = False
        self.reader.stop()
        return self.status()

    def simulate(self, params: dict) -> dict:
        unknown = set(params) - {"comment", "username"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        comment = params.get("comment")
        if not isinstance(comment, str) or not comment.strip() or len(comment) > 500:
            raise StudioError("VALIDATION_ERROR", "comment must contain 1 to 500 characters.", {"field": "comment"})
        username = str(params.get("username") or "khach.thu").strip()[:24]
        return {"entry": self.ingest({"comment": comment, "username": username, "nickname": username}, simulated=True)}

    def _text(self, entry: dict, params: dict) -> str:
        text = params.get("text", entry["reply"])
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise StudioError("VALIDATION_ERROR", "Reply text must contain 1 to 300 characters.", {"field": "text"})
        text = " ".join(text.split())
        with self._lock:
            if text != entry["reply"]:
                entry["edited"] = True
            entry["reply"], entry["auto"], entry["auto_at"] = text, [], None
        return text

    def edit(self, params: dict) -> dict:
        """Save the operator's text as the reply (later Đọc / Gửi and any automatic send use it)."""
        unknown = set(params) - {"id", "text"}
        if unknown:
            raise StudioError("VALIDATION_ERROR", "Unexpected parameters.", {"fields": sorted(map(str, unknown))})
        with self._lock:
            entry = self._entry(params.get("id"))
        if "text" not in params:
            raise StudioError("VALIDATION_ERROR", "text is required.", {"field": "text"})
        self._text(entry, params)
        self._changed()
        return {"entry": self._public(entry)}

    def hold(self, params: dict) -> dict:
        """The operator started editing: the countdown stops so the AI's text is never sent meanwhile."""
        return self.cancel(params)

    def speak(self, params: dict) -> dict:
        with self._lock:
            entry = self._entry(params.get("id"))
        self._speak(entry, self._text(entry, params))
        return {"entry": self._public(entry)}

    def send_comment(self, params: dict) -> dict:
        with self._lock:
            entry = self._entry(params.get("id"))
        self._comment(entry, self._text(entry, params))
        return {"entry": self._public(entry)}

    def cancel(self, params: dict) -> dict:
        """Stop an automatic send that is still in its confirm window."""
        with self._lock:
            entry = self._entry(params.get("id"))
            entry["auto"], entry["auto_at"] = [], None
        self._changed()
        return {"entry": self._public(entry)}

    def dismiss(self, params: dict) -> dict:
        with self._lock:
            entry = self._entry(params.get("id"))
            entry["auto"], entry["auto_at"] = [], None
            if entry["id"] in self._queue:
                self._queue.remove(entry["id"])
            self._finish(entry, "dismissed", "Bạn đã bỏ qua.")
        self._changed()
        return {"entry": self._public(entry)}

    def stop_voice(self, params: dict | None = None) -> dict:
        if self._voice is not None:
            self._agent(self._voice.clear)
        return self.status()

    @staticmethod
    def _agent(operation):
        try:
            return operation()
        except AgentError as exc:
            raise StudioError(exc.code, exc.message) from exc

    def close(self) -> None:
        with self._wake:
            self._closed = True
            self._wake.notify_all()
        self.reader.stop()
        if self._voice is not None:
            try:
                self._voice.close()
            except Exception:  # noqa: BLE001
                pass
