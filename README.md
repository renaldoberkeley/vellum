# Vellum

Experimental document workspace.

## Repository Structure

- `docs/`: source-of-truth product and architecture docs
- `frontend/`: Next.js + TypeScript application
- `backend/`: FastAPI + SQLAlchemy + Alembic application
- `docker-compose.yml`: local PostgreSQL for development

## Prerequisites

- Node.js 20+
- npm 10+
- Python 3.11+
- Docker Desktop (for local PostgreSQL)

## Local Database (PostgreSQL)

From the repo root:

```bash
docker compose up -d postgres
```

To stop:

```bash
docker compose stop postgres
```

## Backend Setup and Run

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

## Frontend Setup and Run

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000.

## Database Migrations (Alembic)

From `backend/`:

```bash
source .venv/bin/activate
alembic revision -m "init"
alembic upgrade head
```

Phase 0 does not include application tables yet.

## Validation Commands

### Backend

```bash
cd backend
source .venv/bin/activate
pytest
ruff check .
mypy app
```

### Frontend

```bash
cd frontend
npm run lint
npm run typecheck
npm run smoke
```
