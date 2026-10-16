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

## 3. LIVE
1. Start an OBS product LIVE session and connect public TikTok chat.
2. In AI Assistant, enable and save your API provider first. Set output token limit high enough to fit grouped questions.
3. On TikTok Chat, enable **AI LIVE Batch**. Default window: 2.5 seconds, maximum 8 comments. This switch is intentionally **off by default**.
4. Comments passing dedup and anti-spam are collected, labeled with a snapshot of product ID, sale price, description, status and content version **at comment arrival time**. Each batch makes one provider call. Identical normalized text is grouped with all senders retained. Different phrasings are sent to the same model to identify semantic similarities. LLM results are shown as suggestions, not automatically posted.
5. Exact-question-group results for the same provider/model and product version can be returned from bounded in-memory cache. When price, description or status changes, cache version changes. Cache is reset on backend restart.

**Requirements:** Backend is a single process; batch settings, counters and pending work currently live only in memory. Do not use multiple Uvicorn workers. Rate limits are applied by the existing AI provider. At high rates, the one-worker model queue may create delays. The request logger displays batch IDs, grouped comments, token counts and failures. The current product context is tied to OBS playback; when no product is being broadcast, comments are logged and skipped for AI.

## 4. Voice
Two operator-initiated actions appear on a successful AI batch:
- **Nghe thử trên máy**: browser Vietnamese Web Speech voice, requires a compatible voice installed.
- **Phát vào OBS**: requires `edge-tts` installed and a separate OBS **Media Source** called exactly `Sarae Voice`. Place it in the LIVE program scene with audio enabled (unmuted OBS mixer); allow playing local files and disable looping. Press the action, then confirm to send a generated Vietnamese neural MP3 to the OBS source via obs-websocket.

The backend defaults to `vi-VN-HoaiMyNeural`; voice endpoint accepts `vi-VN-NamMinhNeural`. Edge TTS relies on an unofficial online service and can change or be rate-limited. No automatic speech on arrival: operator must approve every spoken message. **Test in OBS preview before streaming** to avoid broadcasting incorrect messages or simultaneous audio with your clothing video. Use OBS mixer to duck/mute original video as desired. TTS files are stored under git-ignored `backend/data/voice`, with unique paths to avoid Windows decoder file locks; periodic cleanup can be added later.

## Important limitations
- This phase is not a full semantic-cache/vector database; same-word normalization is safe, and semantic merging is requested from the LLM in each batch. It does not automatically infer size or availability.
- Only the selected product is supplied; previous-product reference resolution is not implemented yet.
- If an LLM response is invalid, rate-limited or incomplete, the batch reports an error. The user must review the suggested response.
- No automatic comment posting, TikTok pinning, or unsolicited live speech.
