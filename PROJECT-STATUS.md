# Chess Insights — Project Status

**Purpose of this file:** a self-contained snapshot for planning the next
sprint. It assumes no prior context. Hand it to a planner along with the
question "what should Sprint 5 be?"

**Last updated:** 2026-07-28
**Current state:** Sprints 1–4 complete, accepted, committed, and pushed.
**Repo:** `main` clean, level with `origin/main`. **CI is green.**

---

## 1. What the product does today

A personal chess analytics tool. A single user uploads their own PGN files,
gets statistics and a board viewer, saves analyses permanently, and can have
Stockfish evaluate a saved game.

```
upload .pgn  →  preview (stateless)  →  pick which player name is yours
             →  save  →  PostgreSQL  →  reopen any time from "Analysis History"
             →  select a game  →  moves fetched on demand
             →  Analyze Game  →  Stockfish evaluates every position
             →  evaluation graph, synced to the board
```

What the user sees:

- Totals, W/D/L, win %, score % `(W + 0.5D)/G`, White vs Black split
- Per-opening performance under **canonical** opening names, identified from
  the positions actually played rather than from PGN headers
- A real board with move navigation, click-to-jump, flip, colour-aware orientation
- A history list of saved analyses, reopenable and deletable
- For a saved game: an engine evaluation graph with click-to-jump, current-ply
  sync, and explicit not-analyzed / analyzing / complete / failed states

---

## 2. Verified state (reproducible)

```powershell
docker compose up -d

cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 180 passed (with STOCKFISH_PATH set)
.\.venv\Scripts\python.exe -m alembic check      # No new upgrade operations detected

cd ..\frontend
npm test        # 31 passed
npm run build   # clean
npm run lint    # clean
```

Without an engine configured the backend suite reports **175 passed, 5
skipped** — the engine-dependent tests are marked and skipped, never failed.

Also verified: the full suite passes against a **freshly migrated empty
database** as well as the populated dev one; `alembic upgrade head` builds the
schema from empty in 3 migrations; analysis survives a complete
`docker compose down` / `up`; and one real game has been analysed end to end
with Stockfish 17.

**Live data in the dev database:** 3 uploads, 79 games, 6,972 moves, 52
distinct canonical openings, 8 complete analysis runs, 430 position
evaluations.

---

## 3. CI (new in Sprint 4, and now green)

`.github/workflows/ci.yml`, three jobs on push and PR:

- **backend** — Postgres service, `alembic upgrade head` against an *empty*
  database, `pytest`, `alembic check`. `STOCKFISH_PATH` is deliberately empty,
  proving the suite needs no binary.
- **frontend** — `npm ci`, test, lint, build.
- **engine** — installs Stockfish and runs only `-m stockfish_integration`.

**Why green CI matters here:** it is the first environment that is a genuine
fresh clone. Its first run failed with `No 'script_location' key found` because
a blanket `*.ini` rule in `.gitignore` had been silently excluding
`backend/alembic.ini` since Sprint 2 — migrations only ever worked because the
file existed locally on the developer machine. Nobody cloning the repo could
have run them. CI green now means the repo is genuinely self-contained.

---

## 4. Architecture

```
backend/  Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, python-chess, pytest
  app/api/routes/     health, uploads, games (incl. analysis), engine
  app/api/            dependencies.py - injectable session/engine factories
  app/core/           settings, engine + request-scoped session
  app/models/         Upload, Game, Move, AnalysisRun, PositionEvaluation
  app/repositories/   focused query functions (not a generic framework)
  app/schemas/        Pydantic request/response models
  app/services/       pgn_parser, statistics, import_service, library_stats,
                      epd, opening_dataset, opening_classifier,
                      engine, game_analysis_service, analysis_view
  app/scripts/        reclassify_openings (backfill CLI)
  app/data/openings/  vendored Lichess dataset + generated index + MANIFEST
  scripts/            build_opening_index.py (dataset build tooling)

frontend/ React 19, TypeScript, Vite, react-router-dom, react-chessboard, Vitest
  src/api/            uploads, library, analysis
  src/components/     FileUpload, GameList, ChessGameViewer, MoveList,
                      DashboardSummary, StatCard, OpeningLabel,
                      UploadHistoryList, GameAnalysisPanel, EvaluationGraph
  src/pages/          HomePage, SavedDashboardPage
  src/utils/          openings, evaluation, libraryAdapters, players
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

analysis_runs  id(uuid pk), game_id → games ON DELETE CASCADE,
               status(running|complete|failed),
               engine_name, engine_version, engine_binary_sha256,
               requested_depth, engine_options(jsonb),
               created_at, started_at, completed_at, error_message
               INDEX (game_id, created_at), INDEX (game_id, status)

position_evaluations  id(bigint pk),
               analysis_run_id → analysis_runs ON DELETE CASCADE,
               ply, score_cp_white, mate_in_white, best_move_uci,
               depth_reached, nodes, time_ms
               UNIQUE (analysis_run_id, ply)
               CHECK exactly one of score_cp_white / mate_in_white
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
DELETE /api/uploads/{id}               cascades to games + moves + analysis
GET    /api/games/{id}/analysis        not_analyzed | running | complete | failed
POST   /api/games/{id}/analysis        analyze one saved game (201 / 200 / 404 / 409 / 422 / 503)
GET    /api/engine/status              is Stockfish usable?
```

---

## 5. Invariants future work must not break

1. **Aggregate statistics are always derived, never stored.** No
   win/score/percentage columns exist anywhere.
2. **Preview writes zero database rows,** and preview games are never
   analysable — they have no persistent id.
3. **Upload import is one all-or-nothing transaction.**
4. **Engine analysis holds no database transaction while Stockfish runs.**
   Three short transactions: create run → (analyze) → save + complete/fail.
5. **Classification and analysis happen server-side only.** Client-supplied
   openings, engine identity, depth, FENs, and evaluations are all ignored.
6. **One EPD normalization function** feeds both dataset generation and
   runtime classification.
7. **The opening dataset is vendored;** no runtime network access, ever.
8. **Move and evaluation arrays are never in list responses.**
9. **Duplicate detection** is on `(file_sha256, normalized_player_name)`.
10. **Evaluations are stored White-canonical.** Positive centipawns and
    positive mate favour White. Perspective is a display concern.
11. **Centipawn and mate stay distinct types** end to end. Mate is never
    flattened into a fake centipawn number.
12. **A failed run stores no evaluations.** Results accumulate in memory and
    are written only after the whole game succeeds.

### Engine analysis specifics

Positions `0..N` are analysed (`N+1` evaluations for `N` half-moves) using the
FENs stored at import, so ply 0 is the starting position and Sprint 6 can
compute centipawn loss across every move. Stockfish is configured via
`STOCKFISH_PATH` / `_DEPTH` / `_THREADS` / `_HASH_MB` and never discovered on
`PATH`. One process per game, closed in `finally`.

The UI displays **standard engine convention (+ favours White)** using
`white_score`. The API also returns a player-relative `score` on every
evaluation, which is unused by the UI today and exists for Sprint 6.

`user_color` may be `"unknown"` when the saved player name matched neither
side; analysis then reports White perspective rather than refusing.

---

## 6. Known gaps and deliberate non-goals

**Not built, by design:**

- No move-quality classification — evaluations are stored, but centipawn loss,
  accuracy, and blunder/mistake/inaccuracy labels are not derived yet
- No bulk or whole-upload analysis; one game at a time
- No background jobs, queues, progress tracking, cancellation, retry policy,
  bounded concurrency, or resumability
- No authentication — single user, all data visible to anyone with access
- No deployment; local development only
- No cross-upload combined statistics
- No family-level opening rollups
- No LLM features

**Real limitations worth knowing:**

- **Analysis is synchronous.** `POST /api/games/{id}/analysis` stays open for
  the whole run. Fine for one game; an 80-move game at depth 12 takes on the
  order of a minute.
- **Stale `running` runs are unrecoverable.** If the backend is killed
  mid-analysis, that run stays `running` forever and the game cannot be
  re-analysed through the API. This is the single most important thing for
  Sprint 5 to fix.
- **The opening header fallback is unreachable in practice.** All 20 legal
  first moves are named openings, so `opening_source = "unknown"` occurs only
  for zero-move games. Zero-move games are also rejected for analysis.
- **Frontend coverage is uneven.** Vitest covers opening display, lazy move
  loading, and the analysis panel. Most Sprint 1/2 components (upload form,
  name picker, stat cards, move list) still have no tests.
- **3 pre-existing high npm advisories:** `brace-expansion` via eslint, and a
  `react-router` RSC-mode CSRF issue. The app uses client-side `BrowserRouter`
  with no server actions; npm's suggested "fix" is a *downgrade* from 7.18 to
  7.11. Unresolved, deliberately.
- **Test isolation is a recurring hazard.** Three separate rounds of test bugs
  came from assuming an empty database or a real session. New tests must scope
  assertions to ids they created, use `tests/sample_data.py` for a
  per-session-unique file hash, and inject the session factory rather than
  letting services build their own.

---

## 7. Environment constraints

- Windows 11, PowerShell; backend venv at `backend/.venv`
- PostgreSQL in Docker, host port **5433** (5432 is taken by a native install)
- Backend dev server on **8001**; Vite on **5173**; `.\dev.ps1` starts both
- Stockfish is a **binary dependency**, installed manually, referenced by
  `STOCKFISH_PATH`. Currently a `sse41-popcnt` build — an `avx2`/`bmi2` build
  would be noticeably faster
- **TLS interception on this machine** breaks certificate validation for `git`,
  `curl`, and `python` against github.com in some shells (npm works). Anything
  fetching from GitHub may need to run in the user's own shell
- If a database-backed command appears to hang, check Docker Desktop is
  running — psycopg has no default connect timeout and blocks silently

---

## 8. Existing roadmap (from `plan3.md`, not yet committed to)

- **Sprint 5** — persistent engine jobs: queued/running/complete/failed job
  records, progress, retry, cancellation, bounded concurrency, resumability,
  bulk analysis of a whole upload.
- **Sprint 6** — move-quality classification: centipawn loss, best-move
  comparison, inaccuracy/mistake/blunder thresholds, phase assignment,
  game-level accuracy.
- **Sprint 7** — recurring weakness detection across many games.
- **Sprint 8** — explanations and training recommendations.

`plan3-build.md` and `CHESS-INSIGHTS-SPRINT-4-PLAN.md` show the staged
checkpoint format that has worked well: one verifiable checkpoint at a time,
with an acceptance log per checkpoint.

---

## 9. Questions a planner should weigh for Sprint 5

1. **Stale `running` recovery is the top priority.** It is already reachable
   today and has no workaround through the API. Any job design must decide
   how a run is declared dead (heartbeat, timeout, startup sweep).
2. **What executes the work?** In-process background task, a separate worker
   process, or a queue (Celery/RQ/arq + Redis). This is the largest
   architectural decision of the sprint and adds the first
   always-running dependency beyond Postgres.
3. **Concurrency and resource limits.** Each Stockfish process takes threads
   and a hash allocation. Analysing 56 games needs a bounded pool, not one
   process per game.
4. **Idempotency and cost.** A whole-upload analysis of `All2023.pgn` is
   ~5,000 positions. Re-running must not silently redo completed work — the
   `(engine, depth, options)` provenance already recorded is the natural key.
5. **Progress reporting.** The UI currently shows an indeterminate spinner.
   Real progress needs per-game or per-position counters and a polling or
   streaming endpoint.
6. **Does the synchronous endpoint stay?** Sprint 4's
   `POST /api/games/{id}/analysis` blocks. Sprint 5 should decide whether it
   becomes a job-creating endpoint (breaking the current frontend contract) or
   is kept alongside a new bulk endpoint.
