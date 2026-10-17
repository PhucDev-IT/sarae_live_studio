> **SaaS UPDATE (Oct 2026):** For the current two-role, per-Agent activation and local-video architecture, follow [SAAS_PHASE1.md](./SAAS_PHASE1.md). Older shared `SARAE_AGENT_KEY` / `SARAE_ADMIN_KEY` instructions below are **historical and no longer valid** for the updated Agent protocol.

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

## Phase 2 — Remote commands (implemented)

The Cloud API now maintains an authenticated SQLite command queue. On each cycle (5 seconds) the Windows Agent sends a heartbeat, polls one queued command, executes it locally, and acknowledges success/failure. The Web agent console supports:

- CAPTCHA Monitor: start/stop and set **scan interval (0.5–15 s)** / match threshold (0.65–0.99).
- OBS media: status, previous/next, pause/resume and stop.
- Operator-approved TTS to dedicated `Sarae Voice` OBS source.
- Command history with pending/running/completed/failed state.

API: `POST /api/agents/commands`, `GET /api/agents/commands?agent_id=...`, `GET /api/agents/commands/{agent_id}/next`, `POST /api/agents/commands/ack`. Admin and Agent each have separate keys.

**Important limitations:** This is an initial command queue, not yet a distributed job system. If Agent crashes after claiming a command, the command remains `running` until manually repaired; there is no lease expiration / retry. SQLite registry and queue are for a single Backend instance. Monitor configuration is saved in `backend/data/verification_monitor/monitor_config.json` on the Windows computer and restored on restart. Legacy local routes are still present, and do not automatically proxy through the command queue. A cloud-hosted backend cannot access the Agent's local video library. Also, the Agent's OBS playback session is process-local: if media playback is started by a different local Backend process, Agent commands may not know its state.

### Windows Agent console example

```text
[13:20:29] Đã đồng bộ VPS | CAPTCHA: Đang quét màn hình, chưa thấy CAPTCHA
```

The label `watching` means **monitor is active and has not matched a CAPTCHA**, not that a CAPTCHA was found.

### Before real VPS/mobile release

Implement account sign-in and RBAC for **every** Web/API route, replacing the temporary manually-entered admin secret. Add persistent DB-backed command leases, sequence IDs, agent authorization scoped to a workstation, remote preview/result acknowledgements, user auditing, CORS/CSRF/rate limiting, PostgreSQL deployment, and transfer/sync of playlist metadata and media. Do **not** treat this milestone as a publicly secured production release.

## Multi-monitor capture (new)

The Windows CAPTCHA detector now uses `mss.monitors[0]` virtual-desktop geometry, intersected with `win32gui.GetWindowRect(hwnd)`. This supports multiple displays, including negative X/Y coordinates (a secondary monitor to the left or above the primary). No screen selector is needed: the agent targets the detected LIVE Studio window. The `capture_bounds` and `monitors_detected` fields are included in monitor state to assist diagnostics.

Important: TikTok LIVE Studio must not be minimized/covered, and windows spanning several monitors are captured only within the virtual desktop bounds. Mixed DPI scaling and display changes during capture require testing on the target Windows configuration.

## Agent observability and command reliability update

- Agent periodically uploads summarized OBS connectivity (~15 seconds), alongside CAPTCHA status.
- Cloud job poll now marks `running` jobs older than 90 seconds as `failed` rather than automatically executing them again; this avoids duplicate TTS/OBS actions. The agent may still have performed the action before losing network access, so `failed` is not a guarantee that no side effect occurred.
- **Still pending for production**: server-generated per-device credentials, complete authenticated Web access, proper command idempotency/leases, playlist and media-data relocation, full OBS owner-process migration, cloud startup/session control, automatic agent updates, integration tests on Windows, and database upgrade for scalable VPS use.

## Windows process identification (October 2026)

The CAPTCHA monitor now validates the visible top-level window against its owning Windows **process executable**, rather than searching for `TikTok` in the window title. It reads PID and executable path with `win32process` and `psutil`, and retains the selected HWND/window bounds for capture.

Expected executable basenames currently include normalized `TikTok LIVE Studio.exe`, `TikTokLiveStudio.exe`, `TikTok_Live_Studio.exe`. Actual filenames can differ depending on TikTok release: verify the process name in Task Manager or the new **Ứng dụng đang giám sát** display. Optional extra executable basenames can be configured in the Windows machine's environment as a comma-separated list:

```env
SARAE_TIKTOK_EXE_NAMES=YourActualStudioExecutable.exe
```

If the target app is running elevated while Agent lacks access to executable path, the match may fail. Do not fall back to matching any window containing `TikTok` because that can silently capture browser content. Restart Agent after changing executable identification settings.

Install the updated Python packages (`pip install -r requirements.txt`) and launch `backend/run_agent_windows.bat` in the Windows interactive session.

**Not completed:** Cloud-hosted Web/Backend is not yet a complete replacement for the local LIVE session engine. The full playlist/media path mapping, OBS process ownership, end-user login, remote upload/synchronization and installable Windows executable remain separate tasks. The provided `.bat` launcher is not an `.exe` installer.

## Phase 3 — Agent-owned Cloud product video playback (experimental)

Implemented:

- `GET /api/agent-media/products/{product_id}/manifest` returns the ordered video list with SHA-256 and length, restricted by `X-Sarae-Agent-Key`.
- `GET /api/agent-media/files/{media_id}` returns managed Cloud MP4 files, likewise restricted.
- `backend/agent/cloud_playback.py` downloads and checks each MP4 before atomically caching at `backend/data/agent_media/<sha256>.mp4`. Existing SHA-verified cache files are reused.
- Cloud command types `cloud_product_play` (positive product_id), `cloud_product_next`, `cloud_product_stop`; the Web remote Agent panel exposes them. Playback uses the Windows OBS source defined in Agent's local OBS config and never VPS filesystem paths.
- Agent checks OBS media status on its poll cycle and auto-advances after the media source reports ENDED or STOPPED. Session progress exists only in that Agent process. A restart loses playback state; manually start it again.

**Limitations**: This experiment plays **one product video list**, not the entire managed LIVE session workflow. Cloud must have **actual uploaded MP4 files** in `backend/data/media`; a metadata-only `local_path` from the old local database will fail with HTTP 409, and must be uploaded to Cloud. Large files are downloaded before playback starts (Agent cache). Reused cached media are hashed to verify integrity. Downloads assume suitable disk space and reliable network. The existing local OBS LIVE engine and this feature must never use the same OBS Media Source concurrently: do not start a legacy local session while Cloud product playback is active. The same media source name must exist on Windows OBS.

**Before production**: integrate Cloud session history and scheduling with Agent-owned state, agent-specific media authorization, cloud-managed upload/synchronization, resilient download/resume/quotas/cleanup, proper OBS end event reliability, persistent Agent playback state, multi-Agent device ownership, signed per-user authorization and real integration tests. Do not expose legacy APIs publicly without the planned end-user auth/RBAC.
