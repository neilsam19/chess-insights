# Chess Insights

## By Neil Samant

A personalized chess analytics platform that turns a player's game history into opening trends, recurring patterns, and (eventually) engine-backed insights.

Built incrementally in small, verified sprints rather than all at once. Current status: **Sprint 4 complete** - a saved game can now be analysed by Stockfish, with per-position evaluations persisted and drawn as an evaluation graph, on top of position-based opening classification from Sprint 3, the PostgreSQL-backed "Analysis History" from Sprint 2, and the stateless upload/preview flow from Sprint 1.

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

## Opening classification

Openings are identified from the **positions actually reached** in a game, not
from the PGN's `ECO`/`Opening` headers. Every position in a game is normalized
to an EPD and looked up against a vendored snapshot of the Lichess opening
dataset, walking backwards from the deepest position until a named opening
matches. This means openings are correct even when a PGN has no opening
headers, has wrong ones, or reaches a line by transposition.

Precedence is strict: **dataset position match → PGN header fallback →
unknown**. A header can never override a real position match.

### Dataset attribution

Opening names and ECO codes come from
[lichess-org/chess-openings](https://github.com/lichess-org/chess-openings),
released under the **CC0 1.0 Public Domain Dedication**. A fixed snapshot is
vendored in `backend/app/data/openings/` (source TSVs plus `COPYING.txt`); the
exact upstream commit is recorded in `MANIFEST.json`. Nothing is downloaded at
runtime and classification never contacts an external service.

The searchable index (`openings_index.tsv`) is generated from those TSVs, with
each opening's EPD derived using the same function the classifier applies to
game positions — so the two representations cannot drift apart. To move to a
newer snapshot:

```powershell
git clone --depth 1 https://github.com/lichess-org/chess-openings ..\chess-openings-snapshot
copy ..\chess-openings-snapshot\*.tsv backend\app\data\openings\
cd backend
python scripts\build_opening_index.py --sha <upstream commit sha>
python -m app.scripts.reclassify_openings   # re-classify already-saved games
```

Updating the dataset is a deliberate, reviewable code change, never an
uncontrolled runtime change.

### Reclassifying saved analyses

Analyses saved before an opening-dataset change can be updated in place,
without re-uploading any PGN:

```powershell
cd backend
python -m app.scripts.reclassify_openings --dry-run   # report only
python -m app.scripts.reclassify_openings             # apply
```

It is idempotent (re-running changes nothing), writes only opening metadata,
and commits one upload at a time. `--force` re-examines every game rather than
only those from an older snapshot; `--upload <id>` restricts it to one upload.

## Engine analysis

A saved game can be analysed by Stockfish: every position from the starting
position through the final move is evaluated, and the results are persisted so
they survive restarts. Open a saved analysis, select a game, and press
**Analyze Game**.

Preview games are deliberately *not* analysable - analysis needs a persisted
game to attach results to, so the action only appears once an upload is saved.

Evaluations follow **standard engine convention: positive favours White**,
regardless of which side you played. Centipawn and mate scores stay distinct
(`+0.74`, `M3`, `Checkmate`) - mate is never flattened into a large fake
centipawn number.

Analysis is **durable background work**. Pressing Analyze Game queues a job and
returns immediately; a separate worker process picks it up and runs Stockfish.
The page shows queued, then per-position progress, and you can navigate away,
reload, or close the tab without losing the job. Cancel and retry are
available while a job is active or after it failed.

Because the work outlives the request, it also survives a crash: a worker that
dies mid-game leaves a lease that expires, and the next worker to start
recovers the job and re-runs it from the first position.

### Running the worker

`.\dev.ps1` starts it alongside the API and frontend. To run it on its own:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.worker            # poll until stopped (Ctrl+C)
python -m app.worker --once     # process at most one job, then exit
```

The API never starts an engine, so **the worker is what needs
`STOCKFISH_PATH`**. Without a worker running, jobs simply stay queued.

### Installing Stockfish

Stockfish is a binary dependency and is **not** installed by `pip`. Download a
build from [stockfishchess.org/download](https://stockfishchess.org/download/),
unzip it somewhere stable, and point `backend/.env` at it:

```
STOCKFISH_PATH=C:\tools\stockfish\stockfish-windows-x86-64-avx2.exe
STOCKFISH_DEPTH=12
STOCKFISH_THREADS=1
STOCKFISH_HASH_MB=64
```

No quotes are needed even if the path contains spaces. Pick the most specific
build your CPU supports (`avx2` or `bmi2` are considerably faster than the
`sse41-popcnt` compatibility build).

The executable path is read from configuration only - never from `PATH`, and
never from a client request. To confirm the binary works before starting the
app:

```powershell
echo uci | & "C:\tools\stockfish\stockfish-windows-x86-64-avx2.exe"
```

A `Stockfish ...` banner followed by `uciok` means it's good. With the backend
running, `GET http://localhost:8001/api/engine/status` reports the same thing:

```json
{ "available": true, "engine_name": "Stockfish", "engine_version": "17", "configured_depth": 12 }
```

If `STOCKFISH_PATH` is unset or wrong, analysis requests return `503` with an
explanation and no analysis run is recorded - a misconfigured server is not a
failed analysis.

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

Engine tests use a fake in-memory engine, so **the suite does not require
Stockfish**. The tests that exercise the real binary are marked and skipped
unless `STOCKFISH_PATH` is set:

```powershell
pytest -m stockfish_integration      # only the real-engine tests
pytest -m "not stockfish_integration"  # everything else
```

Without an engine configured you should see `331 passed, 6 skipped`; with one,
`337 passed`.

Frontend (from `frontend/`):

```powershell
npm test
npm run build
npm run lint
```

## Current known limitations

Deliberately out of scope for now, planned for later sprints:

- No move-quality classification - evaluations are stored, but centipawn loss,
  accuracy, and blunder/mistake labels are not derived from them yet
- No bulk analysis: one game at a time, synchronously. There are no background
  jobs, queues, progress tracking, cancellation, or resumability, so a run left
  as `running` by a killed backend stays that way
- No family-level opening rollups (openings are grouped by their full canonical
  name, so `Sicilian Defense: Najdorf Variation` and
  `Sicilian Defense: Najdorf Variation, English Attack` count separately)
- No authentication - all saved uploads are visible to anyone using the app
- No generative-AI explanations
- No deployment/production setup - local development only
- No background jobs - imports happen synchronously within the request
