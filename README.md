# Chess Insights

## By Neil Samant

A personalized chess analytics platform that turns a player's game history into opening trends, recurring patterns, and (eventually) engine-backed insights.

Built incrementally in small, verified sprints rather than all at once. Current status: **Sprint 6 complete** - those stored evaluations are now turned into per-move quality, upload-level weakness summaries, and training recommendations that link to real positions, on top of the durable background analysis from Sprint 5, Stockfish evaluation and the evaluation graph from Sprint 4, position-based opening classification from Sprint 3, the PostgreSQL-backed "Analysis History" from Sprint 2, and the stateless upload/preview flow from Sprint 1.

## What it does today

1. Upload a `.pgn` file (one game or many)
2. Pick which name in the file is yours from an auto-suggested dropdown (defaults to whoever appears most often)
3. Preview a stats dashboard - total games, W/D/L, win %, score % `(wins + 0.5*draws)/games`, White vs. Black split, per-opening win rates - and step through any game on a real chessboard (move-by-move navigation, flip board, click-to-jump move list)
4. **Save the analysis** to permanently import it into PostgreSQL
5. Browse a **Recent Analyses** list on the home page, reopen any saved upload later (even after restarting the app), or delete it
6. On a saved analysis's dashboard, games are paginated and a game's full move list is only fetched when you actually select it - the app never ships every move from every game up front
7. Analyse games with Stockfish, then see **which of your own moves cost the most** and what to work on next, with every recommendation linking straight to the position it came from

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

### Analysing a whole upload

A saved analysis has an **Analyze All Games** action that queues every game
still needing analysis in one request, then shows aggregate progress across the
upload. Games are still analysed one at a time by the worker, so queueing 79
games starts one engine, not 79.

The action is safe to press twice: games with a matching completed analysis are
skipped and games already queued are left alone, and the response says how many
of each. **Cancel All** stops everything unfinished while keeping the games that
already completed.

## Move quality and training insights

Once a game is analysed, the stored evaluations are turned into feedback about
**your own moves**. Nothing here re-runs the engine — every figure is
arithmetic over evaluations already on disk, computed when the page is loaded.

Per move: how far the engine's assessment of your position fell (**centipawn
loss**), a label from fixed thresholds — inaccuracy from 50, mistake from 100,
blunder from 200 — shown as `?!` / `?` / `??` in the move list, and whether you
found the engine's first choice. Filter to mistakes or worse and step through
your problem moves with Previous/Next issue.

Per upload: the same metrics aggregated, split by colour and by opening, a
ranked list of your costliest positions, and up to four **training
recommendations**. Each states the sample behind it, cites specific positions,
and links straight to the move on the board.

Only your moves are graded — your opponent's are never derived, counted, or
returned. A game saved under a name matching neither player is reported as
ungradable rather than silently graded as White.

### What it will not claim

A recommendation can say which positions cost you the most and where your
losses cluster. It will never say *why* in chess terms — no forks, no pawn
structure, no "you struggle in endgames". The rules are arithmetic over
evaluations and have no basis for that kind of claim, and a denylist test
enforces it rather than leaving it to good intentions.

A recommendation is only made when it clears both a sample floor and a
meaningfulness threshold, so a weakness you know you have may not be listed.
That is deliberate: an early version named an opening as a weakness on the
strength of two games at four centipawns above the library average, which is
noise presented as advice.

Mate errors are counted separately and **never given a pawn value**, so missing
a forced mate cannot quietly inflate or deflate an average. Centipawn and mate
stay distinct types from the database through to the screen.

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

`Ctrl+C` stops it gracefully: it finishes the games in flight and then exits.
Press it a second time to exit immediately - that is safe too, because the
abandoned job's lease expires and the next worker to start recovers it.

### Bounded concurrency

`ANALYSIS_WORKER_CONCURRENCY` (default `1`) caps how many games are analysed at
once. It is a hard ceiling on concurrent Stockfish processes **regardless of
how many games are queued** - submitting 500 games starts this many engines,
not 500.

```powershell
python -m app.worker --concurrency 2
```

Approximate CPU demand is `ANALYSIS_WORKER_CONCURRENCY × STOCKFISH_THREADS`,
and memory roughly `ANALYSIS_WORKER_CONCURRENCY × STOCKFISH_HASH_MB` - the
product, not the larger of the two. The worker logs both at startup. Default
`1` keeps a laptop responsive while analysis runs; raise it only if you have
cores to spare.

Every worker setting is documented in `backend/.env.example`.

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
│   │   ├── api/routes/       # HTTP layer (health, uploads, games, analysis)
│   │   ├── core/             # settings, DB engine/session
│   │   ├── models/           # SQLAlchemy tables (Upload, Game, Move, jobs)
│   │   ├── repositories/     # DB query functions
│   │   ├── schemas/          # Pydantic request/response models
│   │   ├── services/         # business logic (parser, statistics, import)
│   │   └── worker/           # the analysis worker: claiming, leases, sweep
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
├── docs/adr/                  # architecture decision records
├── compose.yaml               # PostgreSQL (Docker) for local dev
├── dev.ps1                    # starts backend + worker + frontend
└── plan2.md / planmini.md      # sprint planning docs
```

## Architecture

Three processes, one database:

```
  browser ──HTTP──▶ FastAPI ──writes job──▶ PostgreSQL ◀──claims job── worker
                       │                        ▲                        │
                       └────reads state─────────┘                        ▼
                                                                     Stockfish
```

The API never runs an engine. It validates a request, commits a job, and
returns; the worker claims jobs with `FOR UPDATE SKIP LOCKED`, holds a
time-limited lease while it works, and writes results back. The browser polls
for progress.

That split is what makes analysis durable: restarting the API does not disturb
a running analysis, and killing the worker mid-game leaves a lease that expires
so the job can be recovered and retried rather than being stuck forever.

PostgreSQL is the only infrastructure service. There is no Redis, no broker, and
no message queue - job state lives in the same transactional database as the
product data, so the two can never disagree. The reasoning, including when that
would be worth revisiting, is in
[docs/adr/0001-durable-analysis-jobs.md](docs/adr/0001-durable-analysis-jobs.md).

Move quality adds **no table and no migration**: it is computed when a page is
read, from evaluations already stored. Why that is the right trade here - and
what would have to change for a derived-data table to be worth it - is in
[docs/adr/0002-move-quality-derivation.md](docs/adr/0002-move-quality-derivation.md),
along with the rule that decides who delivered a checkmate, which is the
subtlest correctness problem in the sprint.

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

Without an engine configured you should see `612 passed, 6 skipped`; with one,
`618 passed`.

Full manual acceptance checklists are in
[docs/sprint-5-acceptance.md](docs/sprint-5-acceptance.md) and
[docs/sprint-6-acceptance.md](docs/sprint-6-acceptance.md). Each ends with a
table of mechanisms that are **required to fail** when deliberately broken —
worth re-running after any change to the worker or the move-quality
derivation.

Frontend (from `frontend/`):

```powershell
npm test
npm run build
npm run lint
```

## Current known limitations

Deliberately out of scope for now, planned for later sprints:

- No re-analysis of an already-analysed game at a different depth. The data
  model supports it (a different depth is a different configuration, so it
  would be a new job rather than a mutation), but no UI offers it
- No single headline "accuracy" percentage. Lichess and Chess.com use different
  formulas, and a third one resembling neither would be worse than none
- No explanation of *why* a move was bad in chess terms - move quality says
  what an evaluation did, not what the right plan was
- Resumability is game-level: a game interrupted mid-analysis restarts from the
  first position rather than resuming, because partial evaluations are
  deliberately never persisted
- No family-level opening rollups (openings are grouped by their full canonical
  name, so `Sicilian Defense: Najdorf Variation` and
  `Sicilian Defense: Najdorf Variation, English Attack` count separately)
- No authentication - all saved uploads are visible to anyone using the app
- No generative-AI explanations
- No deployment/production setup - local development only, and nothing is
  containerized except PostgreSQL
- PGN *imports* are still synchronous within the request; only engine analysis
  runs in the background
