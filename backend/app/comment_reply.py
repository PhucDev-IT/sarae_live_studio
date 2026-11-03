"""Comment branch of the AI auto reply: post a reply as a TikTok LIVE chat message through the desk's Agent
(docs/AI_AUTO_REPLY.md). Independent of the voice branch: its own queue, pacing and Agent lane ("chat").

Per LIVE desk (BatchState): replies wait for the operator's cancel window (confirm_seconds), then a queue sends ONE
`chat_send` command at a time with
- no duplicates: a source comment is answered once; the same text to the same viewer once per DUPLICATE_WINDOW;
- pacing: comment_min_interval_seconds between messages and comment_max_per_minute;
- expiry: a reply older than comment_max_age_seconds is not posted any more (EXPIRED).
The Agent checks all of this again and decides how to send (direct reply, mention, plain chat); its result comes back
as the command outcome (apply_outcome) and is kept on the reply (`comment_delivery`) and in the processing log.
"""
import json
import time

from fastapi import HTTPException
from sqlalchemy import select, text as sql_text

from app.database import SessionLocal

from app.comment_processing import monitors
from app.speech_text import allowed_amounts, blocked_words, clip, safety_issues, strip_openers

DUPLICATE_WINDOW = 600
PENDING = ("scheduled", "queued", "sending")
SENT_STATUSES = ("CONFIRMED", "SUBMITTED")
CHECK_OUTCOME_SECONDS = 3.0


def _touch(reply: dict, **changes):
    from app.live_reply_director import _touch as touch
    touch(reply, **changes)


def _norm(text: str) -> str:
    from app.reply_rules import normalize
    return normalize(text)


def _target(reply: dict) -> int:
    """Index in reply["comments"] of the viewer the next / current message answers (a merged reply answers every
    viewer who asked the same question, one message each, in the order they asked)."""
    return min(int(reply.get("comment_target") or 0), max(len(reply.get("comments") or []) - 1, 0))


def _asked(reply: dict) -> float:
    """When the current target viewer asked (monotonic)."""
    each = reply.get("_comment_asked") or []
    return each[_target(reply)] if _target(reply) < len(each) else reply["_asked"]


def _viewer(reply: dict) -> dict:
    """The comment the message answers: the current target viewer (see _target).
    A username TikTok would not accept for a mention is left out (the Agent then replies or chats normally)."""
    import re
    c = reply["comments"][_target(reply)] if reply.get("comments") else {}
    username = str(c.get("username") or "")
    user_id = str(c.get("user_id") or "")
    return {"msg_id": str(c.get("msg_id") or "")[:80], "user_id": user_id if user_id.isdigit() else "",
            "username": username if re.fullmatch(r"[A-Za-z0-9._]{1,64}", username) else "",
            "nickname": str(c.get("nickname") or "")[:80], "comment": str(c.get("comment") or "")[:300]}


def _delivery(status: str, **extra) -> dict:
    return {"status": status, "mode": None, "error": None, "steps": [], "sent_at": None, **extra}


def _log(st, reply: dict, decision: str, detail: dict):
    viewer = _viewer(reply)
    monitors(st.key).record("COMMENT_SEND", decision, json.dumps({"reply_id": reply["id"], "comment": viewer["comment"],
                                                                  **detail}, ensure_ascii=False),
                            username=viewer["username"], comment_id=viewer["msg_id"] or None)


def schedule(st, reply: dict, options) -> None:
    """Start the comment branch of a reply (called with st.lock held)."""
    from app.live_reply_director import _schedule
    if options.confirm_seconds > 0:
        _touch(reply, comment="scheduled", comment_send_at=time.time() + options.confirm_seconds,
               comment_delivery=_delivery("QUEUED"))
        _schedule(st, ("comment", reply["id"]), options.confirm_seconds, lambda r=reply["id"]: promote(st, r))
    else:
        _touch(reply, comment="queued", comment_delivery=_delivery("QUEUED"))
        st.comment_queue.append(reply["id"])


def promote(st, reply_id: int):
    with st.lock:
        st.timers.pop(("comment", reply_id), None)
        reply = st.replies.get(reply_id)
        if reply is None or reply.get("comment") != "scheduled":
            return
        _touch(reply, comment="queued")
        st.comment_queue.append(reply_id)
    pump(st)


def _expected_user_id(st) -> str | None:
    """TikTok user_id of the account of the RUNNING session on this desk (the Agent sends only as that account)."""
    from app.live_sessions import LiveSession
    with SessionLocal() as db:
        session = db.scalars(select(LiveSession).where(LiveSession.owner_id == st.owner_id,
                                                       LiveSession.agent_id == st.agent_id,
                                                       LiveSession.status == "RUNNING")
                             .order_by(LiveSession.started_at.desc())).first()
        if session is None or not session.tiktok_account_id:
            return None
        row = db.execute(sql_text("SELECT user_id FROM tiktok_accounts WHERE id=:a"),
                         {"a": session.tiktok_account_id}).first()
        return str(row[0]) if row and row[0] else None


def _finish(st, reply: dict, status: str, error: str | None = None, **extra):
    """Result for the current target viewer (called with st.lock held). The next viewer who asked the same question
    is queued right away (pacing still applies); after the last one the reply's state is "sent" if any viewer got
    the message."""
    delivery = {**(reply.get("comment_delivery") or _delivery(status)), "status": status, "error": error, **extra}
    viewer = _viewer(reply)
    results = [*(reply.get("comment_results") or []),
               {"msg_id": viewer["msg_id"], "username": viewer["username"], "nickname": viewer["nickname"],
                "status": status, "mode": delivery.get("mode"), "error": error}]
    _log(st, reply, status, {"mode": delivery.get("mode"), "error": error, "steps": delivery.get("steps")})
    if _target(reply) + 1 < len(reply.get("comments") or []):
        _touch(reply, comment="queued", comment_target=_target(reply) + 1, comment_results=results,
               comment_delivery=_delivery("QUEUED"))
        st.comment_queue.insert(0, reply["id"])
        return
    if any(r["status"] in SENT_STATUSES for r in results):
        state = "sent"
    else:
        state = {"CONFIRMED": "sent", "SUBMITTED": "sent", "EXPIRED": "expired"}.get(status, "failed")
    _touch(reply, comment=state, comment_delivery=delivery, comment_results=results)


def _duplicate(st, reply: dict, now: float) -> str | None:
    viewer = _viewer(reply)
    if viewer["msg_id"] and viewer["msg_id"] in st.comment_msg_ids:
        return "Comment này đã được trả lời"
    key = f"{viewer['user_id'] or viewer['username']}|{_norm(reply['text'])}"
    sent = st.comment_texts.get(key)
    if sent is not None and now - sent < DUPLICATE_WINDOW:
        return "Đã gửi đúng câu này cho người xem này gần đây"
    return None


def pump(st):
    """Send the next queued comment when the pacing allows; plan the next turn otherwise. Never raises."""
    from app.live_reply_director import _schedule, reply_options
    from app.cloud_agents import create_command
    options = reply_options(st.owner_id)
    with st.lock:
        if st.comment_inflight is not None:
            _schedule(st, ("comment_outcome",), CHECK_OUTCOME_SECONDS, lambda: check_outcomes(st))
            return
        now = time.monotonic()
        st.comment_sent_times = [t for t in st.comment_sent_times if now - t < 60]
        while st.comment_queue:
            reply = st.replies.get(st.comment_queue[0])
            if reply is None or reply.get("comment") != "queued":
                st.comment_queue.pop(0)
                continue
            age = now - _asked(reply)
            if age > options.comment_max_age_seconds:
                st.comment_queue.pop(0)
                _finish(st, reply, "EXPIRED", f"Câu hỏi đã {int(age)} giây, quá hạn để comment")
                continue
            duplicate = _duplicate(st, reply, now)
            if duplicate:
                st.comment_queue.pop(0)
                _finish(st, reply, "DUPLICATE", duplicate)
                continue
            wait = 0.0
            if st.comment_last_sent is not None:
                wait = st.comment_last_sent + options.comment_min_interval_seconds - now
            if len(st.comment_sent_times) >= options.comment_max_per_minute:
                wait = max(wait, st.comment_sent_times[0] + 60 - now)
            if wait > 0:
                _schedule(st, ("comment_pump",), wait, lambda: pump(st))
                return
            st.comment_queue.pop(0)
            expected = _expected_user_id(st)
            if not reply.get("session_id"):
                _finish(st, reply, "FAILED", "Câu trả lời không thuộc phiên LIVE nào: không gửi comment")
                continue
            if not expected:
                _finish(st, reply, "FAILED", "Phiên LIVE chưa có TikTok Account đã xác minh: không gửi comment")
                continue
            viewer = _viewer(reply)
            payload = {"reply_id": reply["id"], "session_id": reply["session_id"], "expected_user_id": expected,
                       "ttl_seconds": round(max(5.0, options.comment_max_age_seconds - age), 1),
                       "text": clip(reply.get("comment_override") or reply["text"], 150), "target": viewer,
                       "min_interval_seconds": options.comment_min_interval_seconds,
                       "max_per_minute": options.comment_max_per_minute}
            try:
                from app.chat_payload import validate_chat_send
                payload = validate_chat_send(payload)
                command = create_command(st.agent_id, st.owner_id, "chat_send", payload,
                                         context={"module": "ai_comment", "key": st.key, "reply_id": reply["id"]})
            except HTTPException as exc:
                _finish(st, reply, "FAILED", f"Không gửi được tới Agent: {exc.detail}")
                continue
            st.comment_inflight = reply["id"]
            st.comment_last_sent = now
            st.comment_sent_times.append(now)
            if viewer["msg_id"]:
                st.comment_msg_ids.add(viewer["msg_id"])
            st.comment_texts[f"{viewer['user_id'] or viewer['username']}|{_norm(reply['text'])}"] = now
            _touch(reply, comment="sending", comment_command_id=command.get("command_id"))
            _log(st, reply, "QUEUED", {"text": payload["text"], "target": viewer["username"],
                                       "command_id": command.get("command_id")})
            _schedule(st, ("comment_outcome",), CHECK_OUTCOME_SECONDS, lambda: check_outcomes(st))
            return


def check_outcomes(st):
    """While a chat message is in flight, collect the Agent's result even if nobody has the web page open."""
    from app.cloud_agents import apply_outcomes
    with st.lock:
        st.timers.pop(("comment_outcome",), None)
    try:
        apply_outcomes(st.agent_id)
    except Exception:  # noqa: BLE001 - retried at the next check
        pass
    pump(st)


def apply_outcome(command: dict, context: dict) -> None:
    """Final result of a `chat_send` command (called once by cloud_agents.apply_outcomes)."""
    from app.ai_live_batches import states
    st = states.get(context.get("key") or "")
    if st is None:
        return
    with st.lock:
        reply = st.replies.get(context.get("reply_id"))
        if st.comment_inflight == context.get("reply_id"):
            st.comment_inflight = None
        if reply is not None:
            result = command.get("result") if isinstance(command.get("result"), dict) else {}
            if command["status"] == "SUCCESS" and result.get("status"):
                status = str(result["status"])
                _finish(st, reply, status, result.get("error"), mode=result.get("mode"),
                        steps=result.get("steps") or [], sent_at=command.get("completed_at")
                        if status in SENT_STATUSES else None)
                if status in SENT_STATUSES:
                    st.stats["commented"] = st.stats.get("commented", 0) + 1
            else:
                reason = {"TIMEOUT": "Agent không phản hồi kịp", "CANCELLED": "Lệnh đã huỷ"}.get(command["status"])
                _finish(st, reply, "FAILED", command.get("error") or reason or command["status"])
    pump(st)


# ----- operator actions --------------------------------------------------------------------------------

def send_now(st, reply_id: int, text: str | None):
    """Operator pressed Gửi comment (optionally edited): queued first, pacing and duplicate checks still apply."""
    from app.live_reply_director import find
    if text is not None:
        text = clip(strip_openers(text), 150)
        if not text:
            raise HTTPException(422, "Nội dung comment trống")
    with st.lock:
        reply = find(st, reply_id)
        if reply.get("comment") in ("sending",):
            raise HTTPException(409, "Comment này đang được gửi")
        if reply.get("comment") == "sent":
            raise HTTPException(409, "Comment này đã được gửi")
        content = text or reply.get("text") or ""
        if not content:
            raise HTTPException(422, "Câu trả lời không có nội dung comment")
        issues = safety_issues(content, allowed_amounts(reply["_product"]))
        if blocked_words(content) and "Có từ ngữ không phù hợp" not in issues:
            issues.append("Có từ ngữ không phù hợp")
        if issues:
            raise HTTPException(422, "Không gửi được: " + "; ".join(issues))
        timer = st.timers.pop(("comment", reply_id), None)
        if timer:
            timer.cancel()
        if reply_id in st.comment_queue:
            st.comment_queue.remove(reply_id)
        _touch(reply, comment="queued", comment_override=text, comment_delivery=_delivery("QUEUED"))
        st.comment_queue.insert(0, reply_id)
    pump(st)
    return reply


def cancel(st, reply_id: int):
    from app.live_reply_director import find
    with st.lock:
        reply = find(st, reply_id)
        timer = st.timers.pop(("comment", reply_id), None)
        if timer:
            timer.cancel()
        if reply_id in st.comment_queue:
            st.comment_queue.remove(reply_id)
        if reply.get("comment") in ("scheduled", "queued"):
            _touch(reply, comment="ready", comment_delivery=None, note="Đã huỷ gửi comment")
        return reply


def stop_all(st) -> int:
    with st.lock:
        waiting = [r for r in st.replies.values() if r.get("comment") in ("scheduled", "queued")]
        for reply in waiting:
            timer = st.timers.pop(("comment", reply["id"]), None)
            if timer:
                timer.cancel()
            _touch(reply, comment="ready", comment_delivery=None)
        st.comment_queue.clear()
        return len(waiting)
