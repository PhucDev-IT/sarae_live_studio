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
In Products, only enter **name, sale price, description, status**. An internal SKU is generated automatically when creating a product; existing SKUs and product-video relationships remain intact. Put accurate material, size, color and shipping claims in description if they should influence the answer.

## 3. LIVE: one answer per question
1. Start an OBS product LIVE session and connect public TikTok chat.
2. In AI Assistant, enable and save your API provider first. The batch request raises the output token limit itself (about 110 tokens per question).
3. On TikTok Chat, enable **AI realtime** (off by default). Default window: 2.5 seconds, maximum 8 comments.
4. Comments that pass dedup and anti-spam are labeled with a snapshot of the product on air (id, price, original price, stock, description, status, content version) **at arrival time**.
5. Each question is answered by the cheapest source that can (backend/app/ai_live_batches.py):
   - **noise** (greetings, hearts, single letters) is skipped and listed with its reason;
   - **rules** (backend/app/reply_rules.py): a short question about the price and nothing else is answered from the product price, with rotating phrasings, 0 tokens;
   - **cache**: the same question (normalized words, teencode expanded) about the same product version, already answered by the same model;
   - **AI**: everything else, in ONE request per batch. The prompt asks for one answer per `qid` (never a shared generic answer). Code checks that every qid came back; missing ones are asked once more on their own, then held for review. Nothing is dropped silently.
6. Viewers share one answer only when **code** finds the same intent, the same details (slots: weight, height, size, color, place, quantity) and the same product version. `55kg` and `70kg` size questions are always separate.
7. Before anyone hears or reads it, a reply is cleaned (robotic openers such as "Dạ chào anh chị" removed, length capped) and checked (backend/app/speech_text.py): any price must be the product's price/original price or an amount in its description; links, phone numbers and bad words are refused; questions about another product ("cái lúc nãy") go to review.

## 4. Voice (main channel) and comments
Routing and pacing are in backend/app/live_reply_director.py; options are per owner (`/api/ai/live/reply-config`):
- **Mode**: `manual` (operator presses Speak, default), `semi_auto` (spoken after `veto_seconds` unless cancelled), `auto`.
- **Channel**: `voice`, `comment`, or `both` (orders: voice + comment; several askers: voice; one viewer's personal question: comment).
- Never spoken without the operator: complaints, GENERAL/OTHER, flagged replies, confidence below `min_confidence`.
- One sentence at a time, most useful first (orders, then price/size/stock). A question older than `max_age_seconds` is not spoken late, and a topic spoken within `topic_cooldown_seconds` is not repeated; both fall back to the comment channel instead of disappearing.
- The sentence adds cleaned viewer names ("Lan với Hoa ơi, ...") and names the product if it is no longer on air. Numbers are read as words ("200k" -> "hai trăm nghìn", "1m6" -> "một mét sáu").
- **Dừng giọng nói** cancels everything waiting and sends `tts_clear` to the Agent.

On the Windows Agent (agent-core/src/sarae_agent/voice/tts.py):
- A queue of at most 4 sentences; the next starts only when OBS reports the previous one ended. A sentence past its `ttl_seconds` is dropped.
- MP3s are cached by (voice, rate, text) under `voice/cache` (300 kept): a repeated sentence plays without calling the TTS service.
- While the voice speaks, the video inputs are lowered by 12 dB and restored when the queue is empty (also after a crash, from `voice/ducked.json`).
- Requires `edge-tts` and an OBS **Media Source** named exactly `Sarae Voice` in the LIVE scene, audio enabled, looping off.
- **Agents must be updated** with the backend: older Agents reject the new `tts_speak` fields (`rate`, `ttl_seconds`, `duck`) and do not know `tts_clear`.

Comments are **not** posted to TikTok automatically (no public API; automating the app risks the account). The operator presses **Copy comment** and pastes it; the reply is then marked as commented.

**Requirements:** the backend is a single process; batch settings are persisted, but pending work, replies, answer cache and voice queue live in memory and are lost on restart. Do not use multiple Uvicorn workers. Edge TTS is an unofficial online service and can change or be rate-limited. Test in OBS preview before streaming.

## Important limitations
- Matching of "same question" is by normalized words, not meaning: two phrasings of one question still each cost a line in the AI request (but are merged afterwards when intent and slots match).
- The answer cache is in memory per LIVE desk; a persistent answer bank and answers prepared when a product goes on air are not implemented yet.
- Size, color and stock answers are only as good as the product description; there are no structured size-chart fields yet.
- The Agent's voice status (queue, last error) is visible locally (`voice.status`) but not yet sent to the web with heartbeats.
