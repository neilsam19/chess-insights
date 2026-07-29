# Chess Insights — Project Status

**Purpose of this file:** a self-contained snapshot for planning the next
sprint. It assumes no prior context. Hand it to a planner along with the
question "what should Sprint 4 be?"

**Last updated:** 2026-07-27
**Current state:** Sprints 1, 2, and 3 complete, accepted, committed, and pushed.
**Repo:** `main` clean, all work pushed to `origin/main`.

---

## 1. What the product does today

A personal chess analytics tool. A single user uploads their own PGN files and
gets back statistics and a board viewer, with analyses saved permanently.

End-to-end flow:

```
upload .pgn  →  preview (stateless)  →  pick which player name is yours
             →  save  →  PostgreSQL  →  reopen any time from "Analysis History"
             →  select a game  →  that game's moves fetched on demand
```

What the user sees:

- Totals, W/D/L, win %, score % `(W + 0.5D)/G`, White vs Black split
- Per-opening performance, grouped by **canonical opening name**
- A real board with move navigation, click-to-jump, flip, colour-aware orientation
- A history list of saved analyses, reopenable and deletable

---

## 2. Verified state (reproducible)

All of the following pass as of the last run:

```powershell
docker compose up -d

cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 98 passed
.\.venv\Scripts\python.exe -m alembic check      # No new upgrade operations detected

cd ..\frontend
npm test        # 8 passed
npm run build   # clean
npm run lint    # clean
```

Also verified: the full backend suite passes against a **freshly migrated
empty database** as well as the populated dev one; `alembic upgrade head`
builds the schema from empty in 2 migrations; the opening backfill is
idempotent (`--force` → 0 updated, 79 unchanged); and data survives a complete
`docker compose down` / `up` cycle.

**Live data in the dev database:** 3 uploads, 79 games, 6,972 moves, 52
distinct canonical openings, 0 unclassified games.

---

## 3. Architecture

```
backend/  Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, python-chess, pytest
  app/api/routes/     health, uploads, games
  app/core/           settings, engine + request-scoped session
  app/models/         Upload, Game, Move
  app/repositories/   focused query functions (not a generic framework)
  app/schemas/        Pydantic request/response models
  app/services/       pgn_parser, statistics, import_service, library_stats,
                      epd, opening_dataset, opening_classifier
  app/scripts/        reclassify_openings (backfill CLI)
  app/data/openings/  vendored Lichess dataset + generated index + MANIFEST
  scripts/            build_opening_index.py (dataset build tooling)

frontend/ React 19, TypeScript, Vite, react-router-dom, react-chessboard, Vitest
  src/api/            fetch wrappers per backend area
  src/components/     FileUpload, GameList, ChessGameViewer, MoveList,
                      DashboardSummary, StatCard, OpeningLabel, UploadHistoryList
  src/pages/          HomePage, SavedDashboardPage
  src/utils/          openings (display formatting), libraryAdapters, players
```

### Data model

```
uploads  id(uuid pk), original_filename, file_sha256, player_name,
         normalized_player_name, uploaded_at, game_count, warnings(jsonb)
         UNIQUE (file_sha256, normalized_player_name)

games    id(uuid pk), upload_id → uploads ON DELETE CASCADE, game_index,
         event, site, round, played_date_text,
         white_name, black_name, white_elo, black_elo,
         result, user_color, user_result,
         eco, opening_name, opening_source, opening_matched_ply,
         opening_dataset_version,
         move_count, raw_pgn, game_hash
         INDEX (upload_id)

moves    id(bigint pk), game_id → games ON DELETE CASCADE,
         ply, san, uci, fen_before, fen_after
         UNIQUE (game_id, ply), INDEX (game_id)
```

### API

```
POST   /api/uploads/preview            stateless parse + stats + all games
POST   /api/uploads                    confirmed import (201 / 409 duplicate)
GET    /api/uploads                    paginated history
GET    /api/uploads/{id}               metadata
GET    /api/uploads/{id}/stats         derived statistics
GET    /api/uploads/{id}/games         paginated summaries (no move arrays)
GET    /api/games/{id}                 one game + ordered moves
DELETE /api/uploads/{id}               cascades to games + moves
```

---

## 4. Invariants future work must not break

These were each explicitly verified and are load-bearing:

1. **Aggregate statistics are always derived, never stored.** There are no
   win/score/percentage columns anywhere. Stats are recomputed from stored
   games through one pure `statistics.compute_stats`, shared by the preview
   path and the DB path.
2. **Preview writes zero database rows.** The preview route takes no DB
   dependency at all. There is an automated regression test.
3. **Import is one all-or-nothing transaction.** Services never commit; the
   request-scoped session commits at the boundary.
4. **Classification happens server-side only.** Client-supplied opening values
   are ignored (tested).
5. **One EPD normalization function** (`app/services/epd.py`) is used both to
   generate the dataset index and to normalize game positions, so the two
   cannot drift.
6. **The opening dataset is vendored.** No network access at runtime, ever.
   Updating it is a deliberate code change recorded in `MANIFEST.json`.
7. **Move arrays are never in list responses.** A game's moves are fetched
   only when that game is selected.
8. **Duplicate detection** is on `(file_sha256, normalized_player_name)` —
   the same file may be imported under a different player's perspective.

### Opening classification specifics

Openings come from positions, not PGN headers. Each game's positions are
walked **backwards** (starting at the dataset's max depth, 36 ply) until one
matches a vendored Lichess opening EPD; that deepest match wins. Precedence is
strict: **dataset match → PGN header fallback → unknown**. Headers can never
override a position match; they survive verbatim inside `games.raw_pgn`.

Dataset: `lichess-org/chess-openings` @ `51b886249b9e418498d25b6e39b926c3de99c29a`,
3,807 openings, CC0. Loaded once per process; lookup is a dict, ~0.2 µs.
Classification costs ~3.2 ms/game.

---

## 5. Known gaps and deliberate non-goals

**Not built, by design (so far):**

- No engine analysis (Stockfish), evaluations, or move-quality labels
- No authentication — single user, every saved upload visible to anyone
  with access to the app
- No background jobs / queues — imports are synchronous in-request
- No deployment; local development only
- No cross-upload combined statistics
- No family-level opening rollups (`Sicilian Defense: Najdorf Variation` and
  `… Najdorf Variation, English Attack` are separate groups)
- No LLM features

**Real limitations worth knowing:**

- **The PGN-header fallback is unreachable in practice.** All 20 legal first
  moves are named openings in this dataset, so any game with at least one move
  always matches. `opening_source = "unknown"` occurs only for zero-move games.
  The fallback code is correct and tested (via an injected empty index) but is
  effectively dead against the current snapshot.
- **Frontend test coverage is narrow.** Vitest covers opening display and lazy
  move loading only. Most Sprint 1/2 components (upload form, name picker,
  stat cards, move list, board viewer) have no tests.
- **No CI.** Every check above is run manually. Migration-from-empty in
  particular is a manual step.
- **3 pre-existing high npm advisories:** `brace-expansion` (transitively via
  eslint) and a `react-router` RSC-mode CSRF issue. The app uses client-side
  `BrowserRouter` with no server actions, so the affected feature isn't in
  play; npm's suggested "fix" is a *downgrade* from 7.18 to 7.11. Unresolved,
  deliberately.
- **Test isolation is a recurring hazard.** Two separate rounds of test bugs
  came from assuming an empty database. Any new test that asserts global row
  counts, or imports the sample PGN under a fixed player name, will break
  against a realistic dev database. Use `tests/sample_data.py` (per-session
  unique file hash) and scope assertions to ids the test created.

---

## 6. Environment constraints

- Windows 11, PowerShell; backend venv at `backend/.venv`
- PostgreSQL runs in Docker, host port **5433** (not 5432 — a native Windows
  Postgres occupies 5432)
- Backend dev server on **8001** (not 8000, same reason); Vite on 5173
- `.\dev.ps1` from the repo root starts both
- **TLS interception on this machine** breaks certificate validation for
  `git`, `curl`, and `python` against github.com from some shells (npm works).
  Anything that needs to fetch from GitHub may have to be run in the user's
  own shell.

---

## 7. Existing roadmap (from `plan3.md`, not yet committed to)

`plan3.md` sketches, in order:

- **Sprint 4** — single-game Stockfish analysis: drive the engine, normalize
  evaluations to the player's perspective, handle centipawn and mate scores,
  persist one evaluation per ply, record engine version/depth, show an
  evaluation graph, and clearly distinguish not-analyzed / in-progress /
  complete / failed. Prove it on *one* game before doing whole uploads.
- **Sprint 5** — persistent engine jobs: queued/running/complete/failed states,
  progress, retry, cancellation, bounded concurrency, resumability, bulk
  analysis. This is where `moves` starts acting as an engine work queue.
- **Sprint 6** — move-quality classification: centipawn loss, best-move
  comparison, inaccuracy/mistake/blunder thresholds, phase assignment,
  game-level accuracy.
- **Sprint 7** — recurring weakness detection across many games.
- **Sprint 8** — explanations and training recommendations, optionally LLM-authored
  over engine-backed structured evidence.

Also see `plan3-build.md` for the staged-execution format that worked well
(one verifiable checkpoint at a time).

---

## 8. Questions a planner should weigh for Sprint 4

1. **Synchronous imports won't survive engine work.** A 79-game upload is
   ~6,972 positions; at even 100 ms/position that is ~11 minutes. Sprint 4's
   "one game at a time" scope avoids this, but the boundary between Sprint 4
   and Sprint 5 is really "when does async become mandatory?"
2. **Storage shape for evaluations.** One row per analyzed ply keyed to
   `moves`, versus a separate analysis-run table keyed to engine config. The
   invariant that aggregates stay derived argues for storing raw evaluations
   only and computing accuracy/centipawn-loss on read.
3. **Engine provenance.** Evaluations are only comparable within the same
   engine version + depth/node limit. That needs recording from day one, the
   same way `opening_dataset_version` was.
4. **Stockfish acquisition.** It is a binary dependency, not a pip package.
   Vendoring, download-at-setup, and "assume it's on PATH" are all different
   trade-offs, and the TLS constraint above matters here.
5. **Is auth needed before anything else?** Everything is currently
   single-user with no access control. Engine analysis makes the data more
   valuable and the compute more expensive, which may change that calculus.
6. **Still no CI.** Three sprints of manual verification have worked but the
   surface is growing, and Sprint 4 adds a binary dependency and long-running
   work — both harder to verify by hand.
