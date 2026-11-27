"""Reply desk: comment filters, AI with the product on air, voice/comment branches and the confirm window."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from sarae_agent.errors import AgentError
from sarae_agent.studio.replies import ChatReader, ReplyDesk, is_question
from sarae_agent.studio.service import StudioError, StudioService
from studio_helpers import ACCOUNT, create_product


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.delenv("SARAE_STUDIO_DATA_DIR", raising=False)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeAI:
    def __init__(self, enabled=True, answer=None):
        self.enabled = enabled
        self.answer = answer or {"intent": "ASK_PRICE", "reply": "Dạ áo giá 389.000đ ạ", "needs_review": False,
                                 "reason": "giá", "usage": {"latency_ms": 5}}
        self.calls = []

    def config(self):
        return {"config": {"enabled": self.enabled}}

    def reply(self, params):
        self.calls.append(params)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {**self.answer, "product_id": params.get("product_id"), "action_executed": False}


class FakeVoice:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def speak(self, text, voice, **kwargs):
        if self.error:
            raise self.error
        self.calls.append({"text": text, "voice": voice, **kwargs})
        return {"status": "queued", "position": 1}

    def status(self):
        return {"queued": len(self.calls)}

    def clear(self):
        self.calls.clear()

    def close(self):
        pass


class FakeTikTok:
    def __init__(self, status="CONFIRMED"):
        self.calls, self.status = [], status

    def chat_send(self, payload):
        self.calls.append(payload)
        return {"status": self.status, "mode": "DIRECT_REPLY", "error": None}


def make_live(product_id=None):
    """The LIVE layer as the desk sees it: the product the timeline has pinned now."""
    return SimpleNamespace(current_product_id=lambda: product_id, _live_session=lambda: None)


@pytest.fixture
def studio(tmp_path):
    instance = StudioService(data_dir=tmp_path / "studio")
    yield instance
    instance.close()


def desk_for(studio, *, ai=None, live=None, tiktok=None, speaker=None, clock=None, **settings):
    desk = ReplyDesk(studio, ai or FakeAI(), live or make_live(), tiktok or FakeTikTok(), voice=speaker or FakeVoice(),
                     clock=clock or Clock(), start_threads=False)
    if settings:
        desk.configure(settings)
    return desk


def comment(text, username="lan.nguyen", msg_id="m1", nickname="Lan"):
    return {"comment": text, "username": username, "nickname": nickname, "msg_id": msg_id, "user_id": "u1"}


def error_code(operation) -> str:
    with pytest.raises(StudioError) as caught:
        operation()
    return caught.value.code


def test_question_heuristic():
    for text in ("giá bn shop", "50kg mặc size gì", "còn màu đen ko", "ship mấy ngày?", "1m58 45kg",
                 "giá: bao nhiu", "ib e", "tư vấn e với", "có ship cod hông", "còn size L k ạ", "áo này có không shop",
                 "quần dài bn ạ", "link đâu"):
        assert is_question(text), text
    for text in ("đẹp quá", "hello shop", "❤️❤️", "theo dõi shop", "xinh thế", "okk"):
        assert not is_question(text), text


def test_operator_edit_is_saved_and_the_ai_text_never_goes_out(studio):
    clock, speaker, tiktok = Clock(), FakeVoice(), FakeTikTok()
    desk = desk_for(studio, clock=clock, speaker=speaker, tiktok=tiktok, auto_voice=True, confirm_seconds=5)
    studio.set_tiktok_account(ACCOUNT) if hasattr(studio, "set_tiktok_account") else None
    entry_id = desk.ingest(comment("quần dài bn ạ"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    assert desk.status()["entries"][0]["auto_in"] == 5
    # Starting to edit stops the countdown; saving keeps the operator's text.
    assert desk.hold({"id": entry_id})["entry"]["auto"] == []
    saved = desk.edit({"id": entry_id, "text": "Quần dài riêng 59.000đ nha chị"})["entry"]
    assert saved["reply"] == "Quần dài riêng 59.000đ nha chị" and saved["edited"] is True
    assert desk.status()["entries"][0]["reply"] == "Quần dài riêng 59.000đ nha chị"
    desk.speak({"id": entry_id})  # Đọc after saving: the saved text, not the AI's
    assert speaker.calls[-1]["text"] == "Quần dài riêng 59.000đ nha chị"
    assert error_code(lambda: desk.edit({"id": entry_id, "text": "  "})) == "VALIDATION_ERROR"
    assert error_code(lambda: desk.edit({"id": entry_id})) == "VALIDATION_ERROR"


def test_a_late_ai_answer_does_not_overwrite_the_operator_text(studio):
    desk = desk_for(studio)
    entry_id = desk.ingest(comment("giá bao nhiêu"), simulated=False)["id"]
    entry = desk._entry(desk._queue.popleft())
    desk.edit({"id": entry_id, "text": "Dạ 109k nha"})  # written by hand while the AI was still thinking
    desk.answer(entry)
    assert desk.status()["entries"][0]["reply"] == "Dạ 109k nha"


def test_the_whole_feed_is_returned(studio):
    desk = desk_for(studio)
    for n in range(120):
        desk.ingest(comment(f"đẹp quá {n}", msg_id=f"m{n}", username=f"user{n}"), simulated=False)
    status = desk.status()
    assert len(status["entries"]) == 120 and status["stats"]["received"] == 120


def test_filters_before_ai(studio):
    studio.import_tiktok_snapshot({**ACCOUNT, "user_name": "shopviet"}, [])
    desk = desk_for(studio, ignored_usernames=["@Spam.Bot"])
    stages = [desk.ingest(comment(*args), simulated=False)["stage"] for args in (
        ("giá bao nhiêu", "shopviet", "a"), ("giá bao nhiêu", "spam.bot", "b"), ("đẹp quá", "hoa", "c"),
        ("❤️❤️❤️", "hoa", "d"), ("giá bao nhiêu", "hoa", "e"), ("giá bao nhiêu", "hoa", "f"))]
    assert stages == ["ignored", "ignored", "skipped", "skipped", "queued", "ignored"]
    # The same chat event delivered twice is not shown again.
    desk.ingest(comment("giá bao nhiêu", "mai", "e"), simulated=False)
    assert len(desk.status()["entries"]) == 6 and desk.status()["queued"] == 1


def test_ai_disabled_and_answer_all(studio):
    desk = desk_for(studio, ai=FakeAI(enabled=False))
    assert desk.ingest(comment("giá?"), simulated=False)["reason"] == "AI đang tắt."
    desk = desk_for(studio, answer_all=True)
    assert desk.ingest(comment("đẹp quá", msg_id="z"), simulated=False)["stage"] == "queued"


def test_answer_uses_product_on_air_and_schedules_auto_send(studio):
    product = create_product(studio, "Áo linen")
    clock, ai = Clock(), FakeAI()
    desk = desk_for(studio, ai=ai, live=make_live(product["id"]), clock=clock,
                    auto_voice=True, auto_comment=True, confirm_seconds=5)
    entry_id = desk.ingest(comment("giá bao nhiêu"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    assert ai.calls == [{"comment": "giá bao nhiêu", "username": "Lan", "product_id": product["id"]}]
    entry = desk.status()["entries"][0]
    assert entry["id"] == entry_id and entry["stage"] == "ready" and entry["reply"] == "Dạ áo giá 389.000đ ạ"
    assert entry["auto"] == ["voice", "comment"] and entry["auto_in"] == 5
    assert desk.cancel({"id": entry_id})["entry"]["auto"] == []


def test_review_replies_are_never_sent_automatically(studio):
    ai = FakeAI(answer={"intent": "COMPLAINT", "reply": "Shop xin lỗi bạn", "needs_review": True, "reason": "",
                        "usage": None})
    desk = desk_for(studio, ai=ai, auto_voice=True, auto_comment=True)
    desk.ingest(comment("sao giao chậm vậy?"), simulated=False)
    desk.answer(desk._entry(desk._queue.popleft()))
    entry = desk.status()["entries"][0]
    assert entry["stage"] == "review" and entry["auto"] == [] and entry["auto_in"] is None


def test_simulated_comment_never_goes_to_tiktok(studio):
    tiktok = FakeTikTok()
    desk = desk_for(studio, tiktok=tiktok, auto_voice=True, auto_comment=True)
    entry_id = desk.simulate({"comment": "giá bao nhiêu shop?"})["entry"]["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    assert desk.status()["entries"][0]["auto"] == ["voice"]
    result = desk.send_comment({"id": entry_id})["entry"]
    assert result["comment_delivery"]["status"] == "FAILED" and tiktok.calls == []


def test_stale_questions_expire_before_ai(studio):
    clock, ai = Clock(), FakeAI()
    desk = desk_for(studio, ai=ai, clock=clock, max_age_seconds=30)
    desk.ingest(comment("giá?"), simulated=False)
    clock.now += 31
    desk.answer(desk._entry(desk._queue.popleft()))
    assert desk.status()["entries"][0]["stage"] == "expired" and ai.calls == []


def test_manual_voice_uses_settings_name_and_edited_text(studio):
    voice = FakeVoice()
    desk = desk_for(studio, speaker=voice, voice="vi-VN-NamMinhNeural", rate=10, duck_video=False)
    entry_id = desk.ingest(comment("giá?"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    result = desk.speak({"id": entry_id, "text": "  Dạ   389k ạ "})["entry"]
    assert result["stage"] == "done" and result["voice"]["status"] == "QUEUED" and result["reply"] == "Dạ 389k ạ"
    call = voice.calls[0]
    assert (call["text"], call["voice"], call["rate"], call["duck"], call["intro"]) == \
        ("Dạ 389k ạ", "vi-VN-NamMinhNeural", 10, False, "Lan ơi,")
    assert error_code(lambda: desk.speak({"id": entry_id, "text": ""})) == "VALIDATION_ERROR"


def test_voice_failure_is_reported_without_touching_comment(studio):
    desk = desk_for(studio, speaker=FakeVoice(AgentError("TTS_QUEUE_FULL", "Đang có 4 câu chờ đọc")))
    entry_id = desk.ingest(comment("giá?"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    entry = desk.speak({"id": entry_id})["entry"]
    assert entry["voice"] == {"status": "FAILED", "detail": "Đang có 4 câu chờ đọc"} and entry["comment_delivery"] is None


def test_comment_branch_sends_verified_account_and_pacing(studio):
    studio.import_tiktok_snapshot(ACCOUNT, [])
    tiktok = FakeTikTok()
    desk = desk_for(studio, tiktok=tiktok, comment_min_interval_seconds=10, comment_max_per_minute=3)
    entry_id = desk.ingest(comment("50kg mặc size gì", msg_id="tt-9"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    entry = desk.send_comment({"id": entry_id})["entry"]
    assert entry["comment_delivery"]["status"] == "CONFIRMED" and entry["stage"] == "done"
    payload = tiktok.calls[0]
    assert payload["expected_user_id"] == ACCOUNT["user_id"] and payload["text"] == "Dạ áo giá 389.000đ ạ"
    assert payload["target"]["msg_id"] == "tt-9" and payload["min_interval_seconds"] == 10
    assert payload["max_per_minute"] == 3


def test_comment_requires_a_verified_account(studio):
    tiktok = FakeTikTok()
    desk = desk_for(studio, tiktok=tiktok)
    entry_id = desk.ingest(comment("giá?"), simulated=False)["id"]
    desk.answer(desk._entry(desk._queue.popleft()))
    assert desk.send_comment({"id": entry_id})["entry"]["comment_delivery"]["status"] == "FAILED"
    assert tiktok.calls == []


@pytest.mark.parametrize("settings", [{"confirm_seconds": 99}, {"voice": "en-US"}, {"rate": 80},
                                      {"auto_voice": "yes"}, {"ignored_usernames": ["bad name!"]}, {"unknown": 1}])
def test_invalid_settings_are_rejected(studio, settings):
    desk = desk_for(studio)
    assert error_code(lambda: desk.configure(settings)) == "VALIDATION_ERROR"


def test_dismiss_removes_from_queue(studio):
    ai = FakeAI()
    desk = desk_for(studio, ai=ai)
    entry_id = desk.ingest(comment("giá?"), simulated=False)["id"]
    assert desk.dismiss({"id": entry_id})["entry"]["stage"] == "dismissed" and desk.status()["queued"] == 0
    assert error_code(lambda: desk.dismiss({"id": 999})) == "NOT_FOUND"


def test_worker_threads_answer_and_auto_speak(studio):
    voice = FakeVoice()
    desk = ReplyDesk(studio, FakeAI(), make_live(), FakeTikTok(), voice=voice)
    try:
        desk.configure({"auto_voice": True, "confirm_seconds": 0})
        desk.ingest(comment("giá bao nhiêu?"), simulated=False)
        deadline = time.monotonic() + 5
        while not voice.calls and time.monotonic() < deadline:
            time.sleep(0.05)
        assert voice.calls and voice.calls[0]["text"] == "Dạ áo giá 389.000đ ạ"
    finally:
        desk.close()


class FakeLiveClient:
    def __init__(self, unique_id):
        self.unique_id, self.handlers = unique_id, {}

    def on(self, event_type):
        def register(handler):
            self.handlers[event_type.__name__] = handler
            return handler
        return register

    async def connect(self):
        await self.handlers["ConnectEvent"](None)
        user = SimpleNamespace(unique_id="lan.nguyen", nickname="Lan", id=42)
        await self.handlers["CommentEvent"](SimpleNamespace(user=user, comment="giá nhiêu?",
                                                            common=SimpleNamespace(msg_id=777)))

    async def disconnect(self):
        pass


def test_chat_reader_delivers_comments_with_stable_ids():
    received, states = [], []
    reader = ChatReader(received.append, lambda: states.append(1), FakeLiveClient)
    reader.start("shopviet")
    deadline = time.monotonic() + 5
    reader.backoff = (60,)
    while (not received or reader.snapshot()["status"] in ("connecting", "connected")) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert received == [{"msg_id": "777", "username": "lan.nguyen", "nickname": "Lan", "user_id": "42",
                         "comment": "giá nhiêu?"}]
    # TikTok closed the chat while nobody pressed Stop: the reader connects again by itself.
    assert reader.snapshot()["status"] == "reconnecting" and reader.snapshot()["reconnects"] == 1
    reader.stop()
    assert reader.snapshot()["status"] == "idle"


class DroppingClient(FakeLiveClient):
    """Each connection delivers one comment, then TikTok drops it."""
    count = 0

    async def connect(self):
        DroppingClient.count += 1
        await self.handlers["ConnectEvent"](None)
        user = SimpleNamespace(unique_id="lan.nguyen", nickname="Lan", id=42)
        await self.handlers["CommentEvent"](SimpleNamespace(user=user, comment=f"câu {DroppingClient.count}?",
                                                            common=SimpleNamespace(msg_id=DroppingClient.count)))
        if DroppingClient.count >= 3:
            raise ConnectionError("network down")


def test_chat_reader_reconnects_after_drops_until_stopped():
    DroppingClient.count = 0
    received = []
    reader = ChatReader(received.append, lambda: None, DroppingClient)
    reader.backoff = (0,)
    reader.start("shopviet")
    deadline = time.monotonic() + 5
    while len(received) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    reader.backoff = (60,)
    assert [r["comment"] for r in received[:3]] == ["câu 1?", "câu 2?", "câu 3?"]
    while reader.snapshot()["status"] != "reconnecting" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert "network down" in reader.snapshot()["error"]
    # While it waits to reconnect it is still running: a second start is refused, Stop cancels the reconnect.
    with pytest.raises(StudioError):
        reader.start("other")
    reader.stop()
    time.sleep(0.1)
    assert reader.snapshot()["status"] == "idle"


class FakeReader:
    def __init__(self):
        self.state, self.started = "idle", []

    def snapshot(self):
        return {"username": "", "status": self.state, "error": ""}

    def start(self, username):
        self.started.append(username)
        self.state = "connected"

    def stop(self):
        self.state = "idle"


def test_chat_follows_the_live_session(studio):
    studio.import_tiktok_snapshot({**ACCOUNT, "user_name": "shopviet"}, [])
    live = make_live()
    live.session_listeners = []
    desk = desk_for(studio, live=live)
    desk.reader = FakeReader()
    assert live.session_listeners == [desk._on_session]
    desk._on_session({"id": "s"}, True)
    assert desk.reader.started == ["shopviet"] and desk.reader.state == "connected"
    desk._on_session({"id": "s"}, False)
    assert desk.reader.state == "idle"


def test_chat_started_by_hand_or_disabled_follow_is_left_alone(studio):
    studio.import_tiktok_snapshot({**ACCOUNT, "user_name": "shopviet"}, [])
    live = make_live()
    live.session_listeners = []
    desk = desk_for(studio, live=live, follow_session=False)
    desk.reader = FakeReader()
    desk._on_session({"id": "s"}, True)
    assert desk.reader.started == []
    desk.configure({"follow_session": True})
    desk.chat_start({})
    desk._on_session({"id": "s"}, False)
    assert desk.reader.state == "connected"  # the operator started it, so it keeps reading
