# Catalog: Products & Presenters

Current scope: standalone catalog entries used as references for future short video classification.

## Backend
Run migration before starting API:
```cmd
cd backend
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Routes:
- GET /api/products?q=&limit=100&offset=0
- GET /api/products/{id}
- POST /api/products
- PUT /api/products/{id}
- DELETE /api/products/{id}
- Same set under /api/presenters

Product fields: sku (unique, required), name, description, optional price, image_url, ACTIVE/INACTIVE.
Presenter fields: name, description, avatar_url, ACTIVE/INACTIVE.
A real backend response is required; UI does not use mock catalog data.

## Frontend
```cmd
git submodule update --init --recursive
cd web
npm.cmd install
npm.cmd run build
npm.cmd run dev
```

Navigate to Products / Presenters. Add, edit, search and delete records. Forms use UI Core Input/Button and standard accessible text area/select until the dedicated Core controls are wired.

## API tests
```cmd
cd backend
.venv\Scripts\python.exe -m pip install httpx
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests use an in-memory SQLite database. No production/local user data is modified.

## Deliberate next steps
- Link videos to products and presenters with real relations.
- Add image uploading and media storage rather than URL-only references.
- Add paging and server-side filtering for large catalogs.
- Design safe deletion once video references exist; currently no video relation is present.
- Authentication/authorization is required before non-local deployment.

Implementation was pushed from GitHub source edits; Windows build, UI and test results still need execution on a development machine.
