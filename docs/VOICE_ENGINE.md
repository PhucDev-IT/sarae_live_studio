# Sarae Voice Engine

Sarae Web → **AI Assistant → Voice Settings** controls the voice used for new LIVE replies, prepared audio, manual speech and browser previews. The default remains Microsoft Edge TTS with `vi-VN-HoaiMyNeural`. Existing `tts_speak`, `tts_prepare`, `tts_clear` and `voice.speak` calls without provider fields continue to use Edge.

## G-OmniVoice (Phase 3: Backend only)

A third provider, `omnivoice`, runs on the separate G-OmniVoice TTS Backend (`voice-server/`). Sarae Backend keeps
the voice profiles (reference WAV in the Media Library storage, Whisper or manual transcript, revision), syncs them
to the voice server, creates TTS jobs and sends `tts_speak` / `tts_prepare` commands carrying job ids only. Edge and
ElevenLabs are unchanged and never call the voice server. Agents receive OmniVoice commands only when they announce
the capability in their heartbeat (Phase 4); older Agents get a clear `AGENT_NO_OMNIVOICE` error instead.
Details, API and the Phase 4 Agent contract: [TTS_SERVER.md section 17](TTS_SERVER.md).

## ElevenLabs setup

1. In Voice Settings, select ElevenLabs and save your API key. The Backend encrypts it in `owner_credentials`; its normal API responses show only whether a key exists. Configure `SARAE_CREDENTIAL_KEY` on the Backend for a persistent encryption key in production. Do not put the ElevenLabs key in Web environment variables or Agent config files.
2. Click **Tải danh sách** to load TTS capable models from `GET /v1/models` and account voices from `GET /v2/voices`. Select a model and voice, or enter IDs manually. `eleven_flash_v2_5` is the initial model suggestion; the account must have access to the selected combination.
3. Set speed (0.7–1.2), stability, similarity, and model supported style or speaker boost. Save, then use **Test Voice** to listen without starting a LIVE session. This preview uses the selected account's credits.
4. Keep the `Sarae Voice` OBS Media Source with audio enabled. The Agent uses the existing source and ducking logic. A newly queued sentence snapshots provider, model, voice and settings, so later changes affect only new commands.

The Backend sends non-secret settings in the existing Cloud command. An activated Windows Agent fetches the owner's ElevenLabs key via `GET /api/agents/voice-key/{agent_id}` using its Agent credential over HTTPS. The key is never included in browser responses, command payloads, acknowledgements or heartbeats. The Agent reads it only for a cache miss. There is no automatic provider fallback; an ElevenLabs failure is reported as a TTS failure.

## Runtime and monitoring

`VoiceEngine` owns the bounded queue, TTL, preparation, disk cache, OBS playback, clear and video ducking. `ProviderRegistry` maps provider names to implementations of `TTSProvider`. A new provider implements `normalize` and `synthesize` and registers itself; the queue and playback engine need no provider specific changes. Cache keys include provider, model, voice, normalized settings, rate and text.

ElevenLabs uses its official `POST /v1/text-to-speech/{voice_id}/stream` endpoint with `mp3_44100_128` output. It writes chunks directly to a temporary MP3 file, then atomically publishes the completed file for OBS. The Agent never accumulates the complete MP3 in memory. OBS Media Source plays completed local files, so the first received chunk is measured but playback waits for the full file. The flash model and cache/preparation reduce this delay. Transient 429/5xx/network failures retry up to three times with bounded backoff; auth, quota and bad request errors stop immediately. Error bodies are not logged because they may contain account information.

Agent logs include TTS request start, first audio, audio ready, OBS start/finish, provider/model/voice, character count, cache state and step latency. Backend records `AI_RESPONSE_READY` and queued voice events. The streaming TTS response does not currently expose a reliable per-request cost, so no monetary amount is inferred from character count.

## Validation

- `agent-core`: run `python -m pytest tests/test_voice_queue.py tests/test_voice_providers.py -q` in its virtual environment. Provider HTTP calls and errors are mocked; no ElevenLabs credits are used.
- `backend`: run `python -m pytest tests/test_ai_live_reply.py tests/test_voice_settings.py -q` with Backend dependencies and pytest. Owner isolation, Agent key authentication, catalog filtering and legacy command payloads are covered.
- `web`: run `npm run build` after initializing `vendor/react-ui-core` and installing Web dependencies.
- Production acceptance still needs one Test Voice with a real key and an OBS playback check on Windows. No TikTok LIVE is needed.

API references: [stream speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream), [list models](https://elevenlabs.io/docs/api-reference/models/list), [list voices](https://elevenlabs.io/docs/api-reference/voices/search), [error handling](https://elevenlabs.io/docs/eleven-api/resources/errors).
