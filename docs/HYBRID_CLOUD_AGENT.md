# Sarae Hybrid Architecture — Cloud API + Windows Agent (Phase 1)

## Goal

- **Web / Backend** deploy on VPS, available to desktop and mobile via authenticated HTTPS.
- **Windows Local Agent** runs in the logged-in Windows desktop session that hosts TikTok LIVE Studio and OBS.
- Agent initiates outbound HTTPS calls to Cloud; no open inbound port on the Windows PC.
- Local mode for development is still supported; Cloud migration must not require redesigning browser URLs.

```text
Phone / Web Browser --HTTPS--> VPS (React Web + FastAPI + DB)
                                 ^ POST /api/agents/heartbeat
                                 |
                     Windows Local Agent (outbound)
                                 |
                         TikTok LIVE Studio / OBS
```

## Implemented in Phase 1 (branch dev)

- `backend/agent/main.py`: separate Python process in the Windows user session, reporting monitor state/events every 10 seconds.
- `backend/app/cloud_agents.py`: persistent SQLite last-seen registry; active status TTL 35 s. This serves remote **read-only monitoring**.
- `GET /api/agents/status`: protected by `SARAE_ADMIN_KEY`.
- `POST /api/agents/heartbeat`: protected by `SARAE_AGENT_KEY`.
- Web CAPTCHA Monitor has a Remote Status panel to inspect agents via the Cloud API.
- CAPTCHA detection still uses local monitor code in `app.verification_monitor`, never server-side desktop capture.

### Windows Agent configuration

From `backend/.env` on **Windows PC**:

```env
SARAE_CLOUD_API_URL=http://127.0.0.1:8000/api
SARAE_AGENT_ID=sarae-pc-01
SARAE_AGENT_KEY=choose-long-random-agent-secret
SARAE_AGENT_AUTOSTART_MONITOR=true
```

For a VPS, replace cloud API URL with `https://api.your-domain.com/api`.
Agent refuses plain HTTP except localhost. Both sides must share `SARAE_AGENT_KEY`.
Have CAPTCHA templates in `backend/data/verification_monitor/templates/` before turning on autostart.
Windows Agent executes in the **interactive user's desktop session**; Windows Service Session 0 cannot capture an ordinary user's screen reliably.

```powershell
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m agent.main
```

### Cloud Backend configuration

From `backend/.env` on **VPS**:

```env
SARAE_AGENT_KEY=choose-long-random-agent-secret
SARAE_ADMIN_KEY=another-long-random-dashboard-secret
```

Run FastAPI as usual. The dashboard's **Remote Status** panel accepts the admin key, held in browser sessionStorage for convenience. The admin key and agent key must be different.

All cloud requests originate from the Windows agent: changing networks or a home NAT router does not require port forwarding.

### Public deployment warning

**Do not expose this repository's existing FastAPI API publicly yet.**
The legacy video, live, verification, and AI control endpoints predate this agent work and lack a complete end-user authentication/authorization boundary. An agent heartbeat token only authenticates agent endpoints; it does not protect the rest of the API.

Before exposing to mobile/public Internet, add full user auth/RBAC, secure HTTPS reverse proxy, CORS for actual domain, rate limiting and tenant ownership checks. Separate configuration for dev/prod. SQLite agent registry is for single-instance Phase 1; migrate to PostgreSQL when scaling workers/instances.

### Not yet migrated

- OBS WebSocket controls, local media files and TTS playback still run through the existing local Backend routes.
- Cloud cannot start/stop the Windows monitor, upload local templates or trigger OBS via Agent yet.
- Chat events, AI orchestration and scheduling still use existing local process state.
- No command queue, relay WebSocket or acknowledgements yet.

**Recommended next phase:** outbound secure long-poll/WebSocket command channel with agent-scoped permissions, job IDs, idempotency and command results. Move local OBS, TTS, media and monitoring actions behind explicit `LocalAgent` interfaces before removing legacy direct-local code.

## Remote web use

The React Web uses `VITE_API_URL` to call a chosen Backend. For remote mobile access, deploy Web to HTTPS and point `VITE_API_URL` at public HTTPS FastAPI. The Remote Status panel then reads the cloud Agent registry, not the phone's screen. Protect that deployment with user authentication before broad access.
