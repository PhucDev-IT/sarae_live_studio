# Sarae AI — product-aware batching and voice (opt-in)

## 1. Update and dependencies (Windows)
```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pip install edge-tts
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```
Keep your working Gemini / OpenAI credentials in backend/.env.

## 2. Create product
**Thông tin cho AI** (product sheet, stored in `products.ai_facts`): size chart (kg / cm ranges), colors, material,
shipping, notes. With a size chart, "50kg mặc gì", "có màu đen không", "vải gì" are answered from these facts with no
AI call. Facts are part of the product version: editing them retires earlier answers.
In Products, only enter **name, sale price, description, status**. An internal SKU is generated automatically when creating a product; existing SKUs and product-video relationships remain intact. Put accurate material, size, color and shipping claims in description if they should influence the answer.

## 2b. Hỏi đáp AI (Q&A per product) and Thử AI (no LIVE needed)
- Web page **Hỏi đáp AI** (backend/app/ai_answers.py): pick a product, see its answers (the shop's own first, then those
  the AI learned for the current product version), add/edit/delete answers with several spoken and comment phrasings.
  The shop's answers are approved, kept when the product is edited, reused for the same or closely worded questions,
  sent to the AI as `shop_qa` context for related questions, and re-checked against the current price at each use.
- Tab **Thử AI**: type comments (one per line, `Tên: nội dung` sets the viewer name) and see exactly what a LIVE would
  produce: source (rule / answer bank / AI), route (voice, comment, review + reasons), the spoken sentence (with a play
  button, edge-tts on the server) and the comment text. Nothing is spoken on OBS, posted or learned.

## 2c. Chat follows the LIVE session
A session's TikTok Account handle (`tiktok_accounts.user_name`) is read automatically: the chat reader starts when the
session starts or resumes and stops when it ends (backend/app/tiktok_chat.py follow_session / release_session). The
session screen shows **Bình luận & AI trả lời** (comments, AI replies, processing log) with Kết nối lại / Ngắt. The old
TikTok Chat page only points to Phiên LIVE.

## 3. LIVE: one answer per question
1. Start an OBS product LIVE session and connect public TikTok chat.
2. In AI Assistant, enable and save your API provider first. The batch request raises the output token limit itself (about 110 tokens per question).
3. On TikTok Chat, enable **AI realtime** (off by default). Default window: 2.5 seconds, maximum 8 comments.
4. Comments that pass dedup and anti-spam are labeled with a snapshot of the product on air (id, price, original price, stock, description, status, content version) **at arrival time**.
5. Each question is answered by the cheapest source that can (backend/app/ai_live_batches.py):
   - **noise** (greetings, hearts, single letters) is skipped and listed with its reason;
   - **rules** (backend/app/reply_rules.py): a short question about the price and nothing else is answered from the product price, with rotating phrasings, 0 tokens;
   - **rules from facts**: size by weight/height from the size chart (one or two matching sizes; outside the chart goes to the AI), colors, material;
   - **answer bank** (`ai_answers` table, backend/app/answer_bank.py, survives restarts): the same normalized words, or close wording where every number and the negation are identical ("55kg" never reuses a "70kg" answer). Entries come from checked AI answers, from preparation, and from the operator (an edited or spoken answer is approved and never overwritten by the AI);
   - **AI**: everything else, in ONE request per batch. The prompt asks for one answer per `qid` (never a shared generic answer). Code checks that every qid came back; missing ones are asked once more on their own, then held for review. Nothing is dropped silently.
6. Viewers share one answer only when **code** finds the same intent, the same details (slots: weight, height, size, color, place, quantity) and the same product version. `55kg` and `70kg` size questions are always separate.
7. Before anyone hears or reads it, a reply is cleaned (robotic openers such as "Dạ chào anh chị" removed, length capped) and checked (backend/app/speech_text.py): any price must be the product's price/original price or an amount in its description; links, phone numbers and bad words are refused; questions about another product ("cái lúc nãy") go to review.

## 3b. Preparation when a product goes on air
With `auto_prepare` (default on) and AI realtime on, the first status poll or comment after a product change starts
ONE AI call that lists the ~15 questions viewers usually ask about this product, with 3 spoken and 2 comment
phrasings each, checked like any reply (wrong prices dropped, complaints never prepared). They go to the answer bank
(once per product version, reused after a restart) and the Agent renders their audio in advance (`tts_prepare`).
"Chuẩn bị lại câu trả lời" in the web regenerates them.

## 4. Voice (main channel) and comments
Routing and pacing are in backend/app/live_reply_director.py; options are per owner (`/api/ai/live/reply-config`):
- **Mode**: `manual` (operator presses Speak, default), `semi_auto` (spoken after `veto_seconds` unless cancelled), `auto`.
- **Channel**: `voice`, `comment`, or `both` (orders: voice + comment; several askers: voice; one viewer's personal question: comment).
- Never spoken without the operator: complaints, GENERAL/OTHER, flagged replies, confidence below `min_confidence`.
- One sentence at a time, most useful first (orders, then price/size/stock). A question older than `max_age_seconds` is not spoken late, and a topic spoken within `topic_cooldown_seconds` is not repeated; both fall back to the comment channel instead of disappearing.
- The sentence is an `intro` (cleaned viewer names, "Lan với Hoa ơi,", and the product if it is no longer on air) plus the answer `text`. The Agent plays them as two clips so the body audio, the same for every viewer, comes from its cache. Numbers are read as words ("200k" -> "hai trăm nghìn", "1m6" -> "một mét sáu").
- **Dừng giọng nói** cancels everything waiting and sends `tts_clear` to the Agent.

On the Windows Agent (agent-core/src/sarae_agent/voice/tts.py):
- A queue of at most 4 sentences; the next starts only when OBS reports the previous one ended. A sentence past its `ttl_seconds` is dropped.
- MP3s are cached by (voice, rate, text) under `voice/cache` (300 kept): a repeated sentence plays without calling the TTS service.
- While the voice speaks, the video inputs are lowered by 12 dB and restored when the queue is empty (also after a crash, from `voice/ducked.json`).
- Requires `edge-tts` and an OBS **Media Source** named exactly `Sarae Voice` in the LIVE scene, audio enabled, looping off.
- `tts_prepare` renders up to 40 sentences into the cache in a separate thread (never played). The voice status (queue, sentence being spoken, last error, sentences being prepared) goes up with every heartbeat and shows in the web.
- **Agents must be updated** with the backend: older Agents reject the new `tts_speak` fields (`rate`, `ttl_seconds`, `duck`, `intro`) and do not know `tts_clear` / `tts_prepare`.

Comments are **not** posted to TikTok automatically (no public API; automating the app risks the account). The operator presses **Copy comment** and pastes it; the reply is then marked as commented.

**Requirements:** the backend is a single process; batch settings are persisted, but pending work, replies, answer cache and voice queue live in memory and are lost on restart. Do not use multiple Uvicorn workers. Edge TTS is an unofficial online service and can change or be rate-limited. Test in OBS preview before streaming.

## Important limitations
- "Same question" is matched by words (exact, or close wording with identical numbers and negation), not by meaning; very different phrasings still go to the AI once and are then in the bank.
- Rules answer only single-topic questions; mixed questions ("giá bn, có size L k") go to the AI.
- Pending comments, replies and the voice queue live in memory (one backend process); the answer bank and facts are persisted.
- Comments are not posted to TikTok automatically.
# Multi-provider Voice Engine

See [Voice Engine](VOICE_ENGINE.md) for Edge/ElevenLabs configuration, Agent key delivery, monitoring and mock test commands.
