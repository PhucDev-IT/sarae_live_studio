"""What happens to each AI reply of a LIVE desk: spoken on OBS, offered as a comment, or held for the operator.

Voice is the main channel. Airtime is limited and a spoken sentence cannot be taken back, so:
- routing (route) sends to review anything flagged, unsure, sensitive or below the confidence threshold;
- modes: manual (operator presses Speak), semi_auto (spoken after a few seconds unless the operator cancels),
  auto (spoken at once, still through every check below);
- pacing (pump): one sentence at a time, most useful first (orders, then price/size...), a question older than
  max_age is no longer spoken, and a topic just spoken is not repeated within the cooldown. Neither is dropped: the
  reply falls back to the comment channel so the viewer can still be answered;
- the spoken sentence (compose_speech) goes straight to the answer, without viewer names. Only when the product on
  air changed since the question does an intro name the product it is about. The Agent speaks intro and body as two
  clips: the body is the same every time, so its audio (often rendered in advance by preparation) is reused.
Comments are not posted automatically (TikTok has no API for it): the operator copies the prepared text.
"""
import json
import time
from threading import Timer
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.identity import owner_settings
from app.comment_processing import monitors
from app import answer_bank, reply_rules
from app.speech_text import allowed_amounts, blocked_words, clip, safety_issues, spoken, strip_openers

VOICES = ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural")


class ReplyOptions(BaseModel):
    mode: Literal["manual", "semi_auto", "auto"] = "manual"
    channel: Literal["voice", "comment", "both"] = "voice"
    voice: str = Field(default=VOICES[0], pattern=r"^vi-VN-(HoaiMy|NamMinh)Neural$")
    rate: int = Field(default=5, ge=-30, le=50)
    veto_seconds: float = Field(default=4, ge=1, le=15)
    max_age_seconds: float = Field(default=25, ge=5, le=120)
    topic_cooldown_seconds: float = Field(default=40, ge=0, le=600)
    min_confidence: float = Field(default=0.75, ge=0.3, le=1)
    duck_video: bool = True
    # How the shop talks ("xưng em, gọi khách là chị, vui vẻ"), added to every AI request.
    persona: str = Field(default="", max_length=300)
    # Prepare answers and their audio as soon as a new product is on air (one AI call per product version).
    auto_prepare: bool = True


def reply_options(owner_id: str) -> ReplyOptions:
    return ReplyOptions.model_validate(owner_settings(owner_id).get("ai_reply") or {})


# Never spoken without the operator, whatever the mode.
NEVER_AUTO = {"COMPLAINT", "OTHER", "GENERAL", "GREETING"}
# Details that make a question one viewer's own: better answered by a comment when both channels are on.
PERSONAL_SLOTS = {"weight_kg", "height_cm", "place", "quantity"}
PRIORITY = {"ORDER": 3, "ASK_PRICE": 2, "ASK_SIZE": 2, "ASK_STOCK": 2, "ASK_COLOR": 1, "ASK_MATERIAL": 1,
            "ASK_SHIPPING": 1, "SHOW_PRODUCT": 1}
VOICE_PENDING = ("review", "ready", "scheduled", "queued")
SYLLABLES_PER_SECOND = 4.2


def route(reply: dict, options: ReplyOptions) -> tuple[str, str]:
    """(channel, why): channel is review, voice, comment or both."""
    if reply["review_reasons"]:
        return "review", "; ".join(reply["review_reasons"])
    if reply["intent"] == "COMPLAINT" or (reply["intent"] in NEVER_AUTO and not reply.get("approved")):
        return "review", "Loại câu hỏi này luôn cần người duyệt"
    if reply["confidence"] < options.min_confidence:
        return "review", f"Độ tự tin {reply['confidence']:.2f} thấp hơn ngưỡng {options.min_confidence:.2f}"
    if options.channel == "comment":
        return "comment", ""
    if options.channel == "voice":
        return ("voice", "") if reply["say"] else ("comment", "Không có câu để nói")
    if reply["intent"] == "ORDER":
        return "both", "Chốt đơn: nói để tạo không khí, comment để xác nhận"
    if len(reply["comments"]) >= 2:
        return "voice", "Nhiều người cùng hỏi"
    if PERSONAL_SLOTS & reply["slots"].keys():
        return "comment", "Câu hỏi riêng của một người"
    return "voice", ""


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
    drift = None if product_on_air == reply["product_id"] else f"Còn {reply['product_name']} lúc nãy thì"
    body = clip(spoken((say if say is not None else reply["say"]).strip()), 440)
    intro = clip(spoken(drift, end=False), 120).rstrip() if drift else None
    return intro or None, body


def comment_text(reply: dict, text: str | None = None) -> str:
    tags = " ".join(f"@{c['username']}" for c in reply["comments"][:3] if c.get("username"))
    return clip(f"{tags} {text if text is not None else reply['text']}".strip(), 150)


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
    """Route new replies and start their voice (by mode) or comment state."""
    options = options or reply_options(st.owner_id)
    with st.lock:
        for reply in replies:
            channel, why = route(reply, options)
            _touch(reply, route=channel, note=why, comment_text=comment_text(reply) if reply["text"] else "")
            if channel == "review":
                st.stats["review"] += 1
                _touch(reply, voice="review", comment="ready" if reply["text"] else None)
                continue
            if channel in ("comment", "both") and reply["text"]:
                _touch(reply, comment="ready")
            if channel not in ("voice", "both"):
                continue
            if options.mode == "manual":
                _touch(reply, voice="ready")
            elif options.mode == "semi_auto":
                _touch(reply, voice="scheduled", speak_at=time.time() + options.veto_seconds)
                _schedule(st, ("veto", reply["id"]), options.veto_seconds, lambda r=reply["id"]: promote(st, r))
            else:
                _touch(reply, voice="queued")
                st.voice_queue.append(reply["id"])
    pump(st)


def promote(st, reply_id: int):
    """The cancel window of a semi_auto reply ended: queue it for the voice unless the operator cancelled it."""
    with st.lock:
        st.timers.pop(("veto", reply_id), None)
        reply = st.replies.get(reply_id)
        if reply is None or reply["voice"] != "scheduled":
            return
        _touch(reply, voice="queued")
        st.voice_queue.append(reply_id)
    pump(st)


def _fallback(reply: dict, why: str):
    """Not spoken after all: keep the viewer answerable by comment."""
    _touch(reply, voice="skipped", note=why, comment="ready" if reply["text"] and reply["comment"] != "done" else reply["comment"])


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
    payload = {"text": body, "voice": options.voice, "rate": options.rate,
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
    st.busy_until = max(st.busy_until, now) + estimate_seconds(speech, options.rate)
    st.topic_last[topic(reply)] = now
    st.stats["spoken"] += 1
    _touch(reply, voice="sent", spoken_text=speech, command_id=command.get("command_id"), note="")
    monitors(st.key).record("TTS_QUEUED", options.voice, speech)


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
    """Do not speak this reply (the semi_auto cancel window); it stays available as a comment."""
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
        reply = cancel(st, reply_id)
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
