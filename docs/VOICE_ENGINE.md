# Sarae Voice Engine

Sarae Web → AI Assistant → Voice Settings supports **Microsoft Edge TTS** and **G-OmniVoice (Local AI)**. Edge defaults to `vi-VN-HoaiMyNeural`. Calls without provider fields continue to use Edge.

## G-OmniVoice

The standalone `voice-server/` loads the pinned model on the GPU. Backend owns reference WAV profiles, Auto Whisper/manual transcript and revisions, creates jobs, and sends job references to a capable Windows Agent. The Agent downloads WAV directly with a scoped read token or through Backend relay, then plays the existing OBS `Sarae Voice` Media Source. It does not load Torch/CUDA or create GPU jobs.

Create/sync a profile, choose and save the default voice, select a Windows Agent and run active readiness/warmup. **Nghe thử** uses a profile-specific preview without changing the saved default, including while a session is RUNNING. **Phát qua OBS (giọng đã lưu)** sends a manual command and shows the utterance monitor. SENT is not PLAYED; OBS PLAYING does not prove viewer audibility.

Fallback defaults to none. The only supported provider fallback is Edge TTS, explicitly selected per manual/prepare/live_reply context. Expired/cancelled/session-ended or OBS errors never trigger a voice switch. Backend protects in-use OmniVoice settings/WAV/transcript from mutation during RUNNING or a pending start. Run Backend with one worker; locks/token state are process-local.

## Edge TTS

Choose Hoài My or Nam Minh and rate -30% to +50%. Save or use **Lưu & nghe thử** to save then preview in the browser. Edge uses its existing bounded synthesis retries and cache; it does not call the GPU server or require an API key. Existing `tts_speak`, `tts_prepare`, `tts_clear` and local `voice.speak` remain available.

## Removed provider compatibility

ElevenLabs has been removed from Web, Backend config/command schemas, Agent Cloud/IPC schemas, provider registry, heartbeat capabilities, preview and fallback. Its catalog/key/Agent-key delivery endpoints have been removed. New requests selecting it are rejected; queued legacy commands fail validation rather than making external API calls.

For stored legacy default settings, Backend reads Edge defaults and returns a `migration_notice` shown in Voice Settings. Legacy provider fallback modes become none; an existing OmniVoice default and comment-only/hold policies are preserved. This compatibility read does not rewrite storage. Save the desired supported voice to persist the change and clear the notice. Old encrypted credentials are inert and are not deleted from production storage by this code change.

## OBS and upgrades

Keep the existing Sarae Voice Media Source with audio enabled. Playback uses completed local files, one worker, deadlines and video ducking/restore. Preserve Agent home, especially `voice/utterances.sqlite` and `event_outbox.json`, across upgrades. Acceptance tombstones prevent replay after an uncertain crash; this can drop a sentence accepted just before crash. Losing telemetry is never a reason to replay audio.

Rebuild the Agent sidecar and desktop distribution together before installing an update; a source merge alone does not replace an already installed binary. Do not start a second GPU service with Agent startup. No Scheduled Task or production deployment is part of this removal.

## Validation and operation

- Agent: run `.venv/Scripts/python.exe -m pytest -q` from `agent-core`.
- Backend: run `.venv/Scripts/python.exe -m unittest discover -s tests -q` from `backend`.
- Web: `npm run build`; browser fixtures are in `web/tests/voice_ui.py` (Vite port 5175).

Contracts and historical phase reports: [TTS_SERVER.md](TTS_SERVER.md). Real desktop/OBS measurements, remaining audio acceptance limits, local operating steps, retention and remote checklist: [TTS_PHASE6.md](TTS_PHASE6.md). Historical ElevenLabs regression results describe the code at the time of those phases, not the current provider set.
