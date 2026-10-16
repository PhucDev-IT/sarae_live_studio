# Sarae Live Studio

Monorepo for an internal live-commerce production workspace.

- `web/` — React, TypeScript, Vite and Tailwind CSS 4. Admin UI for content and operations.
- `backend/` — Python FastAPI, SQLite/SQLAlchemy, Alembic. Owns future business data.
- `desktop/` — existing Flutter Windows application, reserved for local machine and OBS control.
- `docs/` — development documents.

## Local prerequisites

Node.js (compatible with Vite 8), Python 3.12 and an existing Flutter Windows toolchain.

## Start Backend (Windows CMD)

```cmd
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

API health: http://127.0.0.1:8000/api/health
Swagger: http://127.0.0.1:8000/docs

## Start Web (second terminal)

```cmd
cd web
npm install
npm run dev
```

Web UI: http://localhost:5173

Run `npm run build` to validate frontend types and production bundle.
Run `npm run lint` for static checks.

## Scope of Foundation

Completed in this phase:
- Responsive shell, compact sidebar, light/dark mode, dashboard and module navigation.
- Actual API health monitoring; unavailable modules are clearly identified as pending.
- FastAPI settings, CORS, SQLite connection and baseline Alembic infrastructure.

Not implemented: product CRUD, video imports, cloud storage, thumbnail generation, playlist execution, OBS or AI. Dashboard shows em dashes rather than fake numbers.

## Storage and security

Development database is `backend/data/sarae_live.db` (ignored by Git). Use `backend/.env.example` as the basis for local configuration. Before exposing the service beyond localhost, implement authentication and suitable access controls. Desktop-local video folders are not accessible to remote browsers without an authorized server/agent.
