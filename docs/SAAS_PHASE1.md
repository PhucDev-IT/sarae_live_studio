# Sarae SaaS Phase 1 — 2 roles, licensed Windows Agents, local-first video

**Implementation branch:** `dev`  
**Status:** Foundation ready for developer testing, **NOT production-ready**.  
**Roles:** `SUPER_ADMIN` and `OWNER` only.

## Decisions

- `SUPER_ADMIN`: the platform operator. Create OWNER accounts, issue one-time activation keys, update/disable licenses, view registered Agents and revoke them. Cannot silently send LIVE/OBS/TTS commands to OWNER Agents.
- `OWNER`: full remote access to the Agents the account owns. Existing legacy product/catalog/LIVE modules are **not yet tenant-scoped** and are unavailable in public SaaS mode until migrated.
- Video MP4 remains **only on Windows Agent folders** in this phase. Cloud stores metadata, local-media IDs, playback commands and playlist definitions; no cloud video storage purchase required.
- API activation key is **not** Agent ID. One-time activation registers an Agent and returns its stable `agent_id` plus its **unique device credential**, encrypted locally with Windows DPAPI.
- Remote browsers and mobile use the same Web origin with HTTPS and account login. Agent always calls Cloud outbound; no Windows inbound port forwarding.

## 1. Upgrade local code

```powershell
git pull origin dev
cd backend
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Back up the entire `backend/data` directory first**. The new identity/agent/playlist databases are separate SQLite files in that directory, and existing legacy data is not automatically migrated.

## 2. Cloud/Backend setup

Set in the Backend `.env`:

```env
SARAE_SAAS_MODE=true
CORS_ORIGINS=https://your-sarae-web-domain.example
```

For a local development-only test, `CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173` is acceptable.

```powershell
cd backend
.venv\Scripts\python.exe -m app.bootstrap_admin
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use a valid email and password of at least 12 characters for `SUPER_ADMIN`. The bootstrap script refuses to create another `SUPER_ADMIN`.

**Note:** `SARAE_SAAS_MODE=true` blocks all legacy routes except `/api/health` and the new `/api/identity`, `/api/agents`, `/api/agent-library`, and `/api/agent-playlists` APIs. This protects against accidental cross-owner data exposure but also means existing product/LIVE catalog pages **will not work yet** in SaaS mode. Leave `SARAE_SAAS_MODE=false` for existing local single-user workflows; NEVER expose that legacy mode publicly.

In the Web app go to **Tài khoản & License** (`/#/account`) and log in as `SUPER_ADMIN`. Create an OWNER, select maximum Agents and expiration, then press **Cấp mã kích hoạt**. Copy the returned code immediately; the code is not retrievable later.

## 3. Windows Agent setup and activation

On the Windows PC that actually runs OBS and TikTok LIVE Studio, set `backend/.env`:

```env
SARAE_CLOUD_API_URL=http://127.0.0.1:8000/api
SARAE_AGENT_AUTOSTART_MONITOR=false
```

For VPS, replace `SARAE_CLOUD_API_URL` with `https://your-api-domain.example/api`. Plain HTTP is blocked outside localhost.

```powershell
cd backend
.venv\Scripts\python.exe -m agent.activate
.venv\Scripts\python.exe -m agent.configure_media
.venv\Scripts\python.exe -m agent.main
```

- **Activate:** enter the one-time code issued by `SUPER_ADMIN`. The Agent ID/credential are saved as `backend/data/agent_activation.json`, with credential encrypted using the current Windows user's DPAPI.
- **Configure Media:** enter one or more local directories, e.g. `D:\Sarae\Videos`. Directory list lives at `backend/data/agent_media_folders.json`.
- **Run:** the Windows Agent polls the Cloud every few seconds; its media index is rebuilt/synced approximately once a minute. Video bytes are never sent to Cloud.

The Agent must run under the interactive Windows user, preferably the same user who activated it and owns the OBS desktop session. Windows DPAPI-protected credentials are tied to that Windows account.

## 4. OWNER workflow on Web/mobile

1. OWNER logs into **Tài khoản & License**.
2. **Windows Agents** (`/#/agents`) lists only Agents belonging to that OWNER.
3. Open **Thư viện video của Agent**, refresh the metadata catalog and select MP4 entries.
4. Enter a playlist name, create a playlist, and play it through the Windows Agent.
5. Agent resolves `media_id` to an allowlisted local folder, instructs OBS to play the file, and advances automatically when OBS reports the media ended. All operations run on Windows.
6. OWNER can use Monitor configuration, OBS/TTS controls, rename or revoke their devices. Revocation makes future Agent heartbeats/commands fail authentication.

Local-media IDs are derived from canonical local paths, scoped by Agent. Moving a file changes its ID; the old inventory entry becomes unavailable after the next sync.

## 5. New API surfaces

- `POST /api/identity/login`
- `GET /api/identity/me`
- `POST/GET /api/identity/owners` — SUPER_ADMIN only
- `PUT /api/identity/owners/{id}/license` — SUPER_ADMIN only
- `POST /api/identity/owners/{id}/activation-keys` — SUPER_ADMIN only
- `POST /api/identity/activate` — one-time activation key
- `GET/PATCH/DELETE /api/identity/agents` (detail operations include agent ID)
- `POST /api/agents/heartbeat`, `GET /api/agents/commands/{id}/next`, `POST /api/agents/commands/ack` — per-Agent credential
- `GET /api/agents/status`, `GET/POST /api/agents/commands` — authenticated per-OWNER access
- `POST /api/agent-library/sync` — device credential
- `GET /api/agent-library/{agent_id}` — OWNER
- `GET/POST /api/agent-playlists`; `PUT/DELETE /api/agent-playlists/{id}`; `POST /api/agent-playlists/{id}/play` — owner-scoped

## 6. Limitations / required future work

- **Critical**: The legacy product/presenter/media/LIVE session modules were originally single-tenant; they must be migrated with `owner_id` and authorization on every read/write before users can access them in SaaS mode. They are intentionally blocked for now.
- SQLite is not the target production multi-node datastore. The new identity, Agents, command, media inventory and playlist SQLite files need coordinated migrations to PostgreSQL, backups, and audit logging before a real commercial launch.
- Implement login rate limits, stronger session lifecycle (logout/revoke/refresh), password recovery, abuse controls, per-device rotation, telemetry and standardized CORS/HTTPS/proxy settings.
- Need Agent GUI installer/`.exe`, supervised background operation, durable OBS session recovery, reliable snapshot preview and Windows integration tests.
- Local OBS and legacy Cloud playback engines must not compete for the same OBS input. The old Cloud MP4 endpoints are not registered and the old Cloud product commands have been disabled.
- License features and one-time credentials were developed and pushed; **not independently tested end-to-end on Windows** in this workspace.
