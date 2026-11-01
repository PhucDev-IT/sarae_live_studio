"""What happens to each AI reply of a LIVE desk: spoken on OBS, offered as a comment, or held for the operator.

Voice is the main channel. Airtime is limited and a spoken sentence cannot be taken back, so:
- routing (route) sends to review anything flagged, unsure, sensitive or below the confidence threshold;
- two independent switches (docs/AI_AUTO_REPLY.md): auto_voice_reply speaks replies, auto_comment_reply posts them
  as TikTok LIVE chat messages (app/comment_reply.py); both can be on, each waits confirm_seconds so the operator
  can cancel; with both off the operator presses Speak / Gửi comment on each reply;
- pacing (pump): one sentence at a time, most useful first (orders, then price/size...), a question older than
  max_age is no longer spoken, and a topic just spoken is not repeated within the cooldown. Neither is dropped: the
  reply falls back to the comment channel so the viewer can still be answered;
- the spoken sentence (compose_speech) goes straight to the answer, without viewer names. Only when the product on
  air changed since the question does an intro name the product it is about. The Agent speaks intro and body as two
  clips: the body is the same every time, so its audio (often rendered in advance by preparation) is reused.
The comment branch has its own queue, pacing and Agent lane: it never waits for the voice and a failure of one branch
does not touch the other.
"""
import json
import time
from threading import Timer
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.identity import owner_settings
from app.comment_processing import monitors
from app import answer_bank, reply_rules
from app.speech_text import allowed_amounts, blocked_words, clip, safety_issues, spoken, strip_openers

VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")


class ReplyOptions(BaseModel):
    # Independent delivery switches (both may be on).
    auto_voice_reply: bool = False
    auto_comment_reply: bool = False
    # Both branches wait this long so the operator can cancel; 0 sends at once.
    confirm_seconds: float = Field(default=4, ge=0, le=15)
    comment_min_interval_seconds: float = Field(default=8, ge=4, le=120)
    comment_max_per_minute: int = Field(default=5, ge=1, le=20)
    comment_max_age_seconds: float = Field(default=60, ge=10, le=300)
    voice: str = Field(default=VOICES[0], pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")
    rate: int = Field(default=5, ge=-30, le=50)
    max_age_seconds: float = Field(default=25, ge=5, le=120)
    topic_cooldown_seconds: float = Field(default=40, ge=0, le=600)
    min_confidence: float = Field(default=0.75, ge=0.3, le=1)
    duck_video: bool = True
    # How the shop talks ("xưng em, gọi khách là chị, vui vẻ"), added to every AI request.
    persona: str = Field(default="", max_length=300)
    # Prepare answers and their audio as soon as a new product is on air (one AI call per product version).
    auto_prepare: bool = True
    # TikTok usernames whose comments are never answered (spam, staff...), stored without "@" and lowercased.
    # The LIVE account itself is always skipped (app/tiktok_chat.py), it need not be listed.
    ignored_usernames: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("ignored_usernames")
    @classmethod
    def clean_usernames(cls, value: list[str]) -> list[str]:
        out = []
        for item in value:
            name = str(item).strip().lstrip("@").casefold()
            if name and len(name) <= 64 and name not in out:
                out.append(name)
        return out

    @model_validator(mode="before")
    @classmethod
    def upgrade(cls, data):
        """Settings saved before the two switches (mode + channel + veto_seconds): keep the voice behaviour the
        shop had; never start posting comments by itself (the old comment channel only prepared text to copy)."""
        if not isinstance(data, dict) or "auto_voice_reply" in data or ("mode" not in data and "channel" not in data):
            return data
        data = dict(data)
        mode, channel = data.pop("mode", "manual"), data.pop("channel", "voice")
        veto = data.pop("veto_seconds", 4)
        data["auto_voice_reply"] = mode != "manual" and channel in ("voice", "both")
        data["auto_comment_reply"] = False
        data.setdefault("confirm_seconds", veto if mode == "semi_auto" else 0)
        return data


def reply_options(owner_id: str) -> ReplyOptions:
    return ReplyOptions.model_validate(owner_settings(owner_id).get("ai_reply") or {})


# Never spoken without the operator, whatever the mode.
NEVER_AUTO = {"COMPLAINT", "OTHER", "GENERAL", "GREETING"}
PRIORITY = {"ORDER": 3, "ASK_PRICE": 2, "ASK_SIZE": 2, "ASK_STOCK": 2, "ASK_COLOR": 1, "ASK_MATERIAL": 1,
            "ASK_SHIPPING": 1, "SHOW_PRODUCT": 1}
VOICE_PENDING = ("review", "ready", "scheduled", "queued")
SYLLABLES_PER_SECOND = 4.2


def route(reply: dict, options: ReplyOptions) -> tuple[str, str]:
    """("review", why) when the reply must wait for the operator, else ("auto", "")."""
    if reply["review_reasons"]:
        return "review", "; ".join(reply["review_reasons"])
    if reply["intent"] == "COMPLAINT" or (reply["intent"] in NEVER_AUTO and not reply.get("approved")):
        return "review", "Loại câu hỏi này luôn cần người duyệt"
    if reply["confidence"] < options.min_confidence:
        return "review", f"Độ tự tin {reply['confidence']:.2f} thấp hơn ngưỡng {options.min_confidence:.2f}"
    return "auto", ""


def estimate_seconds(text: str, rate: int) -> float:
    return len(text.split()) / (SYLLABLES_PER_SECOND * (1 + rate / 100)) + 0.6


def topic(reply: dict) -> str:
    return json.dumps([reply["intent"], reply["slots"], reply["product_id"]], sort_keys=True, ensure_ascii=False)


def spoken_text(intro: str | None, body: str) -> str:
    """Intro and body as one line for the screen ("Lan ơi, mẫu này…")."""
    if not intro:
        return body
    return f"{intro} {body[:1].lower() + body[1:] if body[1:2].islower() else body}"


def compose_speech(reply: dict, options: ReplyOptions, product_on_air: int | None,
                   say: str | None = None) -> tuple[str | None, str]:
    """(intro, body) the voice reads. The answer goes straight to the point: no viewer names or "mấy bạn hỏi nè".
    The only intro is the product, when the question was about one no longer on air ("còn Áo A lúc nãy thì");
    body = the answer, read the same way every time so its audio is reused."""
    if product_on_air == reply["product_id"]:
        drift = None
    elif reply.get("ref_label"):
        drift = f"{reply['ref_label']} thì"  # the viewer named it ("Mẫu số 5 thì …")
    else:
        drift = f"Còn {reply['product_name']} lúc nãy thì"
    body = clip(spoken((say if say is not None else reply["say"]).strip()), 440)
    intro = clip(spoken(drift, end=False), 120).rstrip() if drift else None
    return intro or None, body


def comment_text(reply: dict, text: str | None = None) -> str:
    tags = " ".join(f"@{c['username']}" for c in reply["comments"][:3] if c.get("username"))
    about = f"{reply['ref_label']}: " if reply.get("ref_label") else ""
    return clip(f"{tags} {about}{text if text is not None else reply['text']}".strip(), 150)


def _touch(reply: dict, **changes):
    from app.ai_live_batches import _now_iso
    reply.update(changes, updated_at=_now_iso())


def _schedule(st, key, seconds: float, action):
    old = st.timers.pop(key, None)
    if old:
        old.cancel()
    timer = Timer(max(0.05, seconds), action)
    timer.daemon = True
    st.timers[key] = timer
    timer.start()


def dispatch(st, replies: list[dict], options: ReplyOptions | None = None):
    """Hand each new reply to the voice branch and the comment branch, independently, per the two switches."""
    from app import comment_reply
    options = options or reply_options(st.owner_id)
    with st.lock:
        for reply in replies:
            channel, why = route(reply, options)
            voice_on = channel == "auto" and options.auto_voice_reply and bool(reply["say"])
            comment_on = channel == "auto" and options.auto_comment_reply and bool(reply["text"])
            if channel == "auto" and not (options.auto_voice_reply or options.auto_comment_reply):
                why = "Tự động trả lời đang tắt (cả giọng nói và bình luận)"
            _touch(reply, route=channel, note=why, comment_text=comment_text(reply) if reply["text"] else "",
                   voice_enabled=voice_on, comment_enabled=comment_on)
            _log_decision(st, reply, channel, why, voice_on, comment_on)
            if channel == "review":
                st.stats["review"] += 1
                _touch(reply, voice="review", comment="ready" if reply["text"] else None)
                continue
            if comment_on:
                comment_reply.schedule(st, reply, options)
            elif reply["text"]:
                _touch(reply, comment="ready")
            if not voice_on:
                _touch(reply, voice="ready" if reply["say"] else None)
            elif options.confirm_seconds > 0:
                _touch(reply, voice="scheduled", speak_at=time.time() + options.confirm_seconds)
                _schedule(st, ("veto", reply["id"]), options.confirm_seconds, lambda r=reply["id"]: promote(st, r))
            else:
                _touch(reply, voice="queued")
                st.voice_queue.append(reply["id"])
    pump(st)
    comment_reply.pump(st)


def _log_decision(st, reply: dict, channel: str, why: str, voice_on: bool, comment_on: bool):
    """One line per source comment: why the AI answers or holds it, the answer, and which branches will send it."""
    decision = "review" if channel == "review" else ("reply" if voice_on or comment_on else "manual")
    for c in reply["comments"]:
        monitors(st.key).record("AI_DECISION", decision, json.dumps({
            "comment": c.get("comment"), "reason": why or "Đủ điều kiện trả lời", "source": reply.get("source"),
            "intent": reply.get("intent"), "response": {"say": reply.get("say"), "text": reply.get("text")},
            "voice_enabled": voice_on, "comment_enabled": comment_on}, ensure_ascii=False),
            username=c.get("username"), comment_id=c.get("msg_id") or None)


def promote(st, reply_id: int):
    """The cancel window of a reply ended: queue it for the voice unless the operator cancelled it."""
    with st.lock:
        st.timers.pop(("veto", reply_id), None)
        reply = st.replies.get(reply_id)
        if reply is None or reply["voice"] != "scheduled":
            return
        _touch(reply, voice="queued")
        st.voice_queue.append(reply_id)
    pump(st)


def _fallback(reply: dict, why: str):
    """Not spoken after all: keep the viewer answerable by comment (unless the comment branch already has it)."""
    busy = reply.get("comment") in ("done", "scheduled", "queued", "sending", "sent")
    _touch(reply, voice="skipped", note=why, comment=reply["comment"] if busy or not reply["text"] else "ready")


def pump(st):
    """Speak the most useful queued reply if the voice is free; plan the next turn otherwise."""
    from app.ai_live_batches import context
    options = reply_options(st.owner_id)
    on_air = context(st.owner_id, st.agent_id)
    product_on_air = on_air["product"]["id"] if on_air else None
    with st.lock:
        now = time.monotonic()
        while st.voice_queue and now >= st.busy_until:
            ordered = sorted((st.replies[i] for i in st.voice_queue if i in st.replies),
                             key=lambda r: (-PRIORITY.get(r["intent"], 0), -len(r["comments"]), r["_asked"]))
            st.voice_queue = [r["id"] for r in ordered]
            if not ordered:
                break
            reply = ordered[0]
            st.voice_queue.pop(0)
            age = now - reply["_asked"]
            if age > options.max_age_seconds:
                _fallback(reply, f"Đã {int(age)} giây, quá hạn để nói")
                continue
            if now - st.topic_last.get(topic(reply), -1e9) < options.topic_cooldown_seconds:
                _fallback(reply, "Vừa nói chủ đề này, chuyển sang comment")
                continue
            send_voice(st, reply, options, product_on_air, ttl=options.max_age_seconds - age + 5)
        if st.voice_queue:
            _schedule(st, ("pump",), st.busy_until - now, lambda: pump(st))


def send_voice(st, reply: dict, options: ReplyOptions, product_on_air: int | None, *, say: str | None = None,
               ttl: float = 120, manual: bool = False):
    """Send one sentence to the desk's Agent (its OBS 'Sarae Voice' source). Raises HTTPException only if `manual`."""
    from app.cloud_agents import create_command
    from app.ai_voice import voice_config
    intro, body = compose_speech(reply, options, product_on_air, say)
    speech = spoken_text(intro, body)
    # Prices are checked on the sentence as written (digits) against the question's product snapshot; words on
    # the whole spoken sentence, names included.
    issues = safety_issues(say if say is not None else reply["say"], allowed_amounts(reply["_product"]))
    if blocked_words(speech) and "Có từ ngữ không phù hợp" not in issues:
        issues.append("Có từ ngữ không phù hợp")
    if issues:
        if manual:
            raise HTTPException(422, "Không phát được: " + "; ".join(issues))
        _touch(reply, voice="review", note="; ".join(issues))
        return
    voice = voice_config(st.owner_id)
    payload = {"text": body, "provider": voice.provider, "model": voice.model,
               "voice": voice.voice, "rate": voice.rate, "settings": voice.settings,
               "ttl_seconds": round(max(5.0, min(300.0, ttl)), 1), "duck": options.duck_video}
    if intro:
        payload["intro"] = intro
    try:
        command = create_command(st.agent_id, st.owner_id, "tts_speak", payload)
    except HTTPException as exc:
        if manual:
            raise
        _fallback(reply, f"Không gửi được tới Agent: {exc.detail}")
        return
    now = time.monotonic()
    st.busy_until = max(st.busy_until, now) + estimate_seconds(speech, voice.rate)
    st.topic_last[topic(reply)] = now
    st.stats["spoken"] += 1
    _touch(reply, voice="sent", spoken_text=speech, command_id=command.get("command_id"), note="")
    monitors(st.key).record("TTS_QUEUED", f"{voice.provider}/{voice.model}/{voice.voice}", speech)


# ----- operator actions --------------------------------------------------------------------------------

def find(st, reply_id: int) -> dict:
    reply = st.replies.get(reply_id)
    if reply is None:
        raise HTTPException(404, "Không tìm thấy câu trả lời (có thể đã quá cũ)")
    return reply


def speak_now(st, reply_id: int, say: str | None):
    """Operator pressed Speak (optionally after editing the sentence): sent now, without pacing or age checks."""
    from app.ai_live_batches import context
    options = reply_options(st.owner_id)
    if say is not None:
        say = clip(strip_openers(say), 300)
        if not say:
            raise HTTPException(422, "Câu nói trống")
        if blocked_words(say):
            raise HTTPException(422, "Câu nói có từ ngữ không phù hợp")
    on_air = context(st.owner_id, st.agent_id)
    with st.lock:
        reply = find(st, reply_id)
        if reply["voice"] in ("scheduled", "queued"):
            st.timers.pop(("veto", reply_id), None)
            if reply_id in st.voice_queue:
                st.voice_queue.remove(reply_id)
        send_voice(st, reply, options, on_air["product"]["id"] if on_air else None, say=say, manual=True)
        if say is not None:
            _touch(reply, say=say)
    # Spoken by the operator's decision: the answer is approved and reused for the same question from now on.
    if reply["intent"] not in ("COMPLAINT", "OTHER", "GREETING") and not reply_rules.mentions_other_product(reply["question"]):
        for question in reply["question"].split(" / "):
            answer_bank.save(st.owner_id, reply["product_id"], reply["version"], question, reply["intent"], reply["slots"],
                             [reply["say"]], [reply["text"]], "operator", approved=True)
    return reply


def cancel(st, reply_id: int):
    """Do not speak this reply (the cancel window); it stays available as a comment."""
    with st.lock:
        reply = find(st, reply_id)
        timer = st.timers.pop(("veto", reply_id), None)
        if timer:
            timer.cancel()
        if reply_id in st.voice_queue:
            st.voice_queue.remove(reply_id)
        if reply["voice"] in VOICE_PENDING:
            _fallback(reply, "Người vận hành đã huỷ phát")
        return reply


def dismiss(st, reply_id: int):
    with st.lock:
        from app import comment_reply
        reply = cancel(st, reply_id)
        comment_reply.cancel(st, reply_id)
        _touch(reply, dismissed=True, comment="dismissed" if reply["comment"] == "ready" else reply["comment"])
        return reply


def mark_commented(st, reply_id: int):
    with st.lock:
        reply = find(st, reply_id)
        _touch(reply, comment="done")
        return reply


def stop_all(st) -> int:
    """Emergency stop: every reply waiting to be spoken goes back to the comment channel. Returns how many."""
    with st.lock:
        waiting = [r for r in st.replies.values() if r["voice"] in ("scheduled", "queued")]
        for reply in waiting:
            timer = st.timers.pop(("veto", reply["id"]), None)
            if timer:
                timer.cancel()
            _fallback(reply, "Đã dừng khẩn cấp")
        st.voice_queue.clear()
        timer = st.timers.pop(("pump",), None)
        if timer:
            timer.cancel()
        st.busy_until = 0.0
        return len(waiting)
