# AI auto reply: voice + TikTok comment

One pipeline turns viewer comments into replies (app/ai_live_batches.py: rules → templates → answer bank → AI, one AI
call per batch). Each reply then goes to two independent branches:

```
comment ─► pipeline (1 AI call at most) ─► reply {say, text}
                                              ├─ auto_voice_reply   → voice branch   (TTS on the Agent, lane "background")
                                              └─ auto_comment_reply → comment branch (Playwright chat sender, lane "chat")
```
- Both on: both branches run in parallel; the comment never waits for the voice.
- One branch failing never changes the other.
- Both off: no automatic reply; the operator can still press Speak / Gửi comment on each reply.
- Replies that need review (flagged, unsure, complaints...) are never sent automatically by either branch.

## Settings (owner, `ai_reply` in owner settings, `/api/ai/live/reply-config`)

| Field | Default | Meaning |
|---|---|---|
| `auto_voice_reply` | false | Speak replies automatically. |
| `auto_comment_reply` | false | Post replies automatically as TikTok LIVE chat messages. |
| `confirm_seconds` | 4 | Both branches wait this long so the operator can cancel; 0 = send at once. |
| `comment_min_interval_seconds` | 8 | At least this long between two automatic chat messages (4–120). |
| `comment_max_per_minute` | 5 | At most this many automatic chat messages per minute (1–20). |
| `comment_max_age_seconds` | 60 | A reply older than this is not posted any more (EXPIRED) (10–300). |
| voice, rate, duck_video, max_age_seconds, topic_cooldown_seconds, min_confidence, persona, auto_prepare | unchanged | |

Upgrade of settings saved before (`mode`, `channel`, `veto_seconds`): `auto_voice_reply` = mode is not manual and
channel is voice/both; `auto_comment_reply` = false (the old comment channel only prepared text to copy, it never
posted); `confirm_seconds` = veto_seconds for semi_auto, 0 for auto. The AI/TTS settings are kept.

## Comment branch (Cloud)
Per LIVE desk: a queue, the confirm window, then the pacing below, then one Agent command `chat_send` at a time.
- The session must be RUNNING with a TikTok Account; its `user_id` is sent as `expected_user_id`.
- Never twice: the same source comment is answered once; the same text to the same viewer once per 10 minutes.
- Pacing: `comment_min_interval_seconds`, `comment_max_per_minute`; older than `comment_max_age_seconds` → EXPIRED.

### Agent command `chat_send` (lane `chat`)
```json
{"reply_id": 12, "session_id": 3, "expected_user_id": "6970...", "ttl_seconds": 45,
 "text": "55kg lấy size M vừa đẹp nha",
 "target": {"msg_id": "7301...", "user_id": "6812...", "username": "lan.nguyen", "nickname": "Lan", "comment": "55kg mặc size gì"}}
```
Result (command result, also the final log line):
```json
{"status": "CONFIRMED", "mode": "DIRECT_REPLY", "error": null,
 "steps": [{"step": "QUEUED", "ms": 0}, {"step": "TYPING", "ms": 340, "action": "composer: textbox[name~chat]"},
           {"step": "SUBMITTED", "ms": 1210, "action": "press Enter"}, {"step": "CONFIRMED", "ms": 2480, "action": "own message seen in chat list"}]}
```
`status`: CONFIRMED · SUBMITTED (sent, no proof it appeared) · FAILED · EXPIRED · DUPLICATE · RATE_LIMITED.
`mode`: DIRECT_REPLY · MENTION · NORMAL (null when nothing was sent).

### Agent chat sender (agent-core/src/sarae_agent/tiktok_chat_sender.py)
Runs on the Auto Pin browser worker: same Chrome profile and page, never a second browser.
1. Checks, failing closed (FAILED, nothing typed): TTL, duplicate, rate limit; Console page open and not a login /
   verification page; logged-in account `user_id` == `expected_user_id`; exactly one chat composer found; chat not
   disabled.
2. Mode, in order:
   - **DIRECT_REPLY**: the source comment is found in the Console chat list as exactly ONE row whose text equals the
     comment and whose author matches the viewer (nickname or @username); its Reply control is used. Several or no
     matching rows → next mode. The display name alone never identifies the viewer.
   - **MENTION**: type `@` + username, wait for the mention suggestion list, and pick the entry whose @username equals
     the viewer's exactly. No exact entry → the draft is cleared and → next mode. A typed "@name" without a picked
     suggestion is not a mention.
   - **NORMAL**: a plain chat message with the reply text.
3. Typing, then send (Enter, or the Send button if Enter did nothing). CONFIRMED only when the message appears in the
   chat list as the shop's own message (or the composer clears AND the text appears); otherwise SUBMITTED.
- Selectors are lists of candidates (roles and labels first, then attributes), in `tiktok_chat_selectors.json` in the
  Agent data folder when present (defaults in code), so they can be fixed without a release. No fixed coordinates.
- Never touches CAPTCHA / verification / limits: a verification page or a "too fast" notice stops the sender.
- `chat_probe` command: reports what the sender finds (composer, list, reply controls, mention list) without sending.

## Logs (processing log of the desk + `reply.comment_delivery`)
Each comment: comment id, username, text; why the AI answers or skips; the response; voice/comment enabled.
Each chat message: reply mode, status QUEUED → TYPING → SUBMITTED → CONFIRMED / FAILED / EXPIRED, milliseconds per
step, the selector or action used, the real error.
