# Chess Insights

## By Neil Samant

A personalized chess analytics platform that turns a player's game history into opening trends, recurring patterns, and (eventually) engine-backed insights.

Built incrementally in small, verified sprints rather than all at once. Current status: **Sprint 2 complete** - PGN uploads are now permanently saved to PostgreSQL and browsable as an "Analysis History," on top of the original stateless upload/preview flow from Sprint 1.

## What it does today

1. Upload a `.pgn` file (one game or many)
2. Pick which name in the file is yours from an auto-suggested dropdown (defaults to whoever appears most often)
3. Preview a stats dashboard - total games, W/D/L, win %, score % `(wins + 0.5*draws)/games`, White vs. Black split, per-opening win rates - and step through any game on a real chessboard (move-by-move navigation, flip board, click-to-jump move list)
4. **Save the analysis** to permanently import it into PostgreSQL
5. Browse a **Recent Analyses** list on the home page, reopen any saved upload later (even after restarting the app), or delete it
6. On a saved analysis's dashboard, games are paginated and a game's full move list is only fetched when you actually select it - the app never ships every move from every game up front

### Preview vs. permanent import

There are two distinct upload endpoints, on purpose:

- **`POST /api/uploads/preview`** - stateless. Parses the file and returns stats + every game so you can pick your name and look before committing. Nothing is written to the database. You can preview the same file as many times as you want.
- **`POST /api/uploads`** - the confirmed "Save analysis" action. Parses the file again (parsing twice is cheap at this scale and avoids staging tables), hashes the file, and permanently persists the upload + every game + every move in one all-or-nothing transaction. Uploading the exact same file under the exact same player name twice returns `409 Conflict` with the existing upload's id, rather than creating a duplicate - but the same file *can* be re-imported under a different player's name (useful for analyzing the same games from the opponent's perspective).

## Tech stack

- **Backend:** Python 3.12, FastAPI, `python-chess`, SQLAlchemy 2.x, Alembic, PostgreSQL, pytest
- **Frontend:** React 19, TypeScript, Vite, `react-router-dom`, `react-chessboard`

## Project structure

```
chess-insights/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # HTTP layer (health, uploads, games)
│   │   ├── core/             # settings, DB engine/session
│   │   ├── models/           # SQLAlchemy tables (Upload, Game, Move)
│   │   ├── repositories/     # DB query functions
│   │   ├── schemas/          # Pydantic request/response models
│   │   └── services/         # business logic (parser, statistics, import)
│   ├── alembic/               # DB migrations
│   ├── tests/                 # pytest, incl. real-Postgres integration tests
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── api/               # fetch wrappers per backend area
│       ├── components/        # FileUpload, GameList, ChessGameViewer, etc.
│       ├── pages/              # HomePage, SavedDashboardPage
│       ├── types/              # TypeScript types mirroring backend schemas
│       └── utils/               # adapters, player-name helpers
├── sample-data/
│   └── sample-games.pgn
├── compose.yaml               # PostgreSQL (Docker) for local dev
├── dev.ps1                    # starts backend + frontend dev servers
└── plan2.md / planmini.md      # sprint planning docs
```

## Prerequisites

- Python 3.12
- Node.js (current LTS) + npm
- Docker Desktop (for PostgreSQL)

## Setup

### Database (PostgreSQL via Docker)

From the repo root:

```powershell
docker compose up -d
```

This starts Postgres in a container, listening on **host port 5433** (not the
default 5432 - deliberately moved to avoid colliding with any native
Postgres install already running on port 5432). Data persists in a named
Docker volume across restarts (`docker compose down` keeps it; add `-v` only
if you actually want to wipe the database).

### Backend

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

`.env` already points at the Docker Postgres instance above by default - no
edits needed for local dev.

Apply database migrations (only needed once, or after pulling new
migrations):

```powershell
alembic upgrade head
```

### Frontend

```powershell
cd frontend
npm install
```

## Running the app

From the repo root:

```powershell
.\dev.ps1
```

This opens two windows: the backend (`uvicorn`, port 8001) and the frontend
(`vite`, usually port 5173). Open the frontend's printed URL in a browser.

(Backend runs on **8001**, not the FastAPI default 8000, for the same reason
as the Postgres port above - avoiding a conflicting process already using
that port on this machine. Adjust in `dev.ps1` and
`frontend/src/api/*.ts`'s `API_BASE_URL` if your setup differs.)

## Tests

Backend (from `backend/`, with the venv active and Postgres running via
`docker compose up -d`):

```powershell
pytest
alembic check
```

Tests include pure unit tests (PGN parsing, statistics - no database needed)
and integration tests that run against the **real** Postgres instance
(wrapped in a transaction that's rolled back after each test, so nothing is
left behind in your dev database).

Frontend (from `frontend/`):

```powershell
npm run build
npm run lint
```

## Current known limitations

Deliberately out of scope for now, planned for later sprints:

- No engine analysis (Stockfish) or move-quality classification
- Opening names beyond what a PGN's own `ECO`/`Opening` headers provide (no
  Lichess opening-name dataset integration yet - a bare ECO code like `B90`
  is shown as `ECO B90` rather than resolved to a full name)
- No authentication - all saved uploads are visible to anyone using the app
- No generative-AI explanations
- No deployment/production setup - local development only
- No background jobs - imports happen synchronously within the request
