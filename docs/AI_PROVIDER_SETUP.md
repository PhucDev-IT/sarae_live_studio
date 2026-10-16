# AI Provider — Phase 1 (operator-triggered)

This phase introduces clean provider-switching via `backend/app/ai_service.py` and an AI Playground page at `/#/ai`.

The provider calls are real API calls, not keyword heuristics. **No live comment is automatically sent to a model yet.** No AI response is posted to TikTok, sent to TTS, or allowed to control OBS.

## Setup (Windows)

From project root:
```powershell
git pull origin dev
cd backend
# Add one credential to backend/.env (do not commit it):
# GEMINI_API_KEY=your_api_key
# or OPENAI_API_KEY=your_api_key
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:5173/#/ai`, choose provider, model ID, limits and enable AI; **Save**, then type a comment and click Test AI. The provider's actual model and quota must be available to your project.

Start with a Gemini model currently available in Google AI Studio for your project. Default `gemini-3.5-flash-lite` is just a suggested model ID; change it in the UI if unavailable. Free Tier depends on project, region, model availability and rate limits. On HTTP 429, retry later instead of repeatedly resubmitting. The free Gemini API tier can use prompts to improve Google's products; use fictional product context while testing if privacy matters.

API:
- `GET/PUT /api/ai/config` — provider settings (no secrets).
- `POST /api/ai/analyze` — one explicit comment analysis.
- `GET /api/ai/usage` — counters since backend startup.
- `GET /api/tiktok-chat/processing` — includes AI_REQUEST and AI_RESPONSE/AI_ERROR logs.

Provider adapters use JSON over HTTPS via `httpx` without introducing additional SDKs. Prompt context includes only the manually supplied product name/details; no entire product table or chat history is sent. The limit is a simple in-memory request-count cap (not billing enforcement). Token counters come from provider responses, not estimates; these values reset on server restart.

## Later
- Product context builder from DB, reviewed against current session.
- Select only non-spam comments for AI, asynchronous queue, backpressure, cache, retry/rate-limits.
- User-controlled automatic processing toggle, budget measurement and moderation.
