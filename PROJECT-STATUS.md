# Chess Insights — Project Status

**Purpose of this file:** a self-contained snapshot for planning the next
sprint. It assumes no prior context. Hand it to a planner along with the
question "what should Sprint 6 be?"

**Last updated:** 2026-08-01
**Current state:** Sprints 1–5 complete, accepted, committed, and pushed.
**Repo:** `main` clean, level with `origin/main`.

---

## 1. What the product does today

A personal chess analytics tool. A single user uploads their own PGN files,
gets statistics and a board viewer, saves analyses permanently, and has
Stockfish evaluate saved games as durable background work.

```
upload .pgn  →  preview (stateless)  →  pick which player name is yours
             →  save  →  PostgreSQL  →  reopen any time from "Analysis History"
             →  select a game  →  moves fetched on demand
             →  Analyze Game (or Analyze All Games)
             →  job queued, worker runs Stockfish, page polls progress
             →  evaluation graph, synced to the board
```

What the user sees:

- Totals, W/D/L, win %, score % `(W + 0.5D)/G`, White vs Black split
- Per-opening performance under **canonical** opening names, identified from
  the positions actually played rather than from PGN headers
- A real board with move navigation, click-to-jump, flip, colour-aware
  orientation
- A history list of saved analyses, reopenable and deletable
- Per game: queued / running-with-progress / complete / failed / cancelled,
  with cancel and retry, surviving reload and navigation
- Per upload: **Analyze All Games**, aggregate progress across every game,
  batch cancel, and a report of what was skipped as already analysed

---

## 2. Verified state (reproducible)

Run from a clean checkout with Docker running:

```powershell
docker compose up -d
cd backend && .\.venv\Scripts\python.exe -m pytest -q      # 376 passed
.\.venv\Scripts\python.exe -m alembic check                # clean
cd ..\frontend && npm test && npm run lint && npm run build
```

| Check | Result |
|---|---|
| Backend tests, engine configured | 376 passed |
| Backend tests, `STOCKFISH_PATH=""` | 370 passed, 6 skipped |
| Frontend tests | 54 passed |
| Lint / build | exit 0 / exit 0 |
| `alembic check` | no new upgrade operations |
| Schema built from an empty database | 4 migrations, no drift |

Live development data: **3 uploads, 79 games, 6,972 moves, 9 batches, 163 jobs,
43 analysis runs, 3,236 position evaluations, 40 of 79 games fully analysed.**

The engine-free run matters as much as the other: **the ordinary suite must
never require a Stockfish binary.** Engine tests use a fake in-memory engine;
6 real-binary tests are marked `stockfish_integration` and skip without one.

---

## 3. Architecture

Three processes, one database. **PostgreSQL is the only infrastructure
service** — no Redis, no broker, no message queue.

```
  browser ──HTTP──▶ FastAPI ──writes job──▶ PostgreSQL ◀──claims job── worker
                       │                        ▲                        │
                       └────reads state─────────┘                        ▼
                                                                     Stockfish
```

- **FastAPI** validates a request, commits a durable job, and returns. It never
  starts an engine.
- **`python -m app.worker`** claims jobs with `FOR UPDATE SKIP LOCKED`, holds a
  time-limited lease, runs Stockfish, and writes results back.
- **React** polls for progress.

That split is what makes analysis durable: restarting the API does not disturb
a running analysis, and killing the worker leaves a lease that expires so the
job is recovered and retried rather than stuck forever.

**Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x typed declarative, Alembic,
PostgreSQL 17, `python-chess`, pytest · React 19, TypeScript, Vite,
`react-router-dom`, `react-chessboard`, Vitest + RTL.

**Local ports:** Postgres **5433**, backend **8001**, Vite **5173** — all moved
off defaults to avoid conflicts on the developer's machine.

### Data model

```
uploads ──< games ──< moves
                  ├──< analysis_runs ──< position_evaluations
                  └──< analysis_jobs >── analysis_batches >── uploads
```

- `uploads` — one saved import. Unique on `(file_sha256, normalized_player_name)`,
  so the same file can be imported once per player name.
- `games` — one game. Carries canonical `eco` / `opening_name` /
  `opening_source` / `opening_matched_ply` / `opening_dataset_version`, plus
  `game_hash` (content identity, used for analysis identity).
- `moves` — `ply`, `san`, `uci`, `fen_before`, `fen_after`. Unique on
  `(game_id, ply)`.
- `analysis_batches` — one user request (single game or whole upload). Holds
  `engine_config_hash`, depth, options. Its `status` is a **derived cache**;
  reads always recompute from the jobs.
- `analysis_jobs` — one claimable unit of work. Status, `config_hash`,
  attempt counters, `available_at`, lease fields (`claimed_by`,
  `lease_expires_at`, `heartbeat_at`), progress, `cancel_requested_at`,
  `error_code` / `error_message`.
- `analysis_runs` — one actual engine attempt, with immutable provenance
  (engine name, version, binary SHA-256, depth, options) and
  `analysis_job_id` / `attempt_number`. **Nullable**, because Sprint 4 runs
  predate jobs.
- `position_evaluations` — raw engine output per ply: `score_cp_white`,
  `mate_in_white`, `best_move_uci`, `depth_reached`, `nodes`, `time_ms`.

Key database-enforced rules:

- Partial unique index on `(game_id, config_hash) WHERE status IN ('queued','running')`
  — at most one active job per game+configuration, enforced by Postgres so
  concurrent enqueues cannot both win.
- `CHECK (score_cp_white IS NULL) <> (mate_in_white IS NULL)` — a position is
  either a centipawn assessment or a mate, never both or neither.
- `CHECK completed_positions BETWEEN 0 AND total_positions`.
- Cascades: deleting an upload removes games, moves, batches, jobs, runs, and
  evaluations. Deleting a job sets `analysis_runs.analysis_job_id` to NULL
  rather than cascading — a run records work that really happened.

### Analysis identity

Reuse and duplicate-rejection depend on a two-layer hash:

```
engine config hash = sha256(behaviour version + depth + options + binary sha256)
job config hash    = sha256(engine config hash + games.game_hash)
```

The **executable path is deliberately not an input** — moving the same binary
must not invalidate existing analyses. Sprint 4 runs have no stored hash, so
identity is recomputed from what each run recorded; without that, Sprint 5
would have silently invalidated 430 existing evaluations.

### API surface

```
GET    /api/health
POST   /api/uploads/preview            stateless; writes nothing
POST   /api/uploads                    permanent import, one transaction
GET    /api/uploads                    paginated
GET    /api/uploads/{id}
GET    /api/uploads/{id}/stats
GET    /api/uploads/{id}/games         paginated summaries, no move arrays
DELETE /api/uploads/{id}
POST   /api/uploads/{id}/analysis      bulk enqueue → 202/200/404/503
GET    /api/games/{id}                 full detail incl. moves
GET    /api/games/{id}/analysis        discriminated union (see below)
POST   /api/games/{id}/analysis        enqueue → 202/200/409/404/422/503
GET    /api/analysis/jobs/{id}
POST   /api/analysis/jobs/{id}/cancel
POST   /api/analysis/jobs/{id}/retry
GET    /api/analysis/batches/{id}
POST   /api/analysis/batches/{id}/cancel
GET    /api/engine/status
```

`GET /api/games/{id}/analysis` returns a discriminated union on `status`:
`not_analyzed` | `queued` | `running` | `complete` | `failed` | `cancelled`.
Every variant carries `can_cancel` / `can_retry`, so the client never
re-derives which actions are legal. Mirrored exactly as a TypeScript tagged
union.

---

## 4. Invariants that must not regress

From Sprints 1–4:

1. Preview writes nothing to the database, ever.
2. Import is one all-or-nothing transaction.
3. List endpoints never carry move or evaluation arrays.
4. Openings come from played positions; a PGN header can never override a
   position match. Precedence: dataset match → header fallback → unknown.
5. The opening dataset is vendored; nothing is fetched at runtime.
6. Centipawn and mate scores stay distinct types on the wire — mate is never
   flattened into a large fake centipawn number.
7. Evaluations are stored canonically (positive favours White); perspective is
   a presentation concern.
8. Engine identity, depth, options, and positions come from the server only —
   never from a request.

Added by Sprint 5:

9. A committed job exists before an enqueue response is returned.
10. Only the worker executes Stockfish. The API never starts an engine.
11. A job is owned by at most one unexpired worker lease; every worker mutation
    matches on `claimed_by` inside the UPDATE.
12. Engine execution never holds an open database transaction.
13. Failed and cancelled attempts store **zero** position evaluations.
14. A completed run is immutable.
15. Progress is monotonic within an attempt, and clamped to the total.
16. Concurrency is bounded by configuration, never by submitted workload.
17. Matching active jobs are not duplicated; matching completed runs are reused.
18. Resumability is game-level — an interrupted game restarts from ply 0.

---

## 5. Three tests that must be able to fail

Three mechanisms are invisible when working and catastrophic when silently
broken. Each was verified by deliberately breaking the code and confirming the
test goes red. **If any of these ever passes after such a change, the test has
stopped testing anything.**

| Mechanism | Break it by | Test must |
|---|---|---|
| `FOR UPDATE SKIP LOCKED` | removing `skip_locked=True` | fail with a statement timeout, **not hang** |
| Background lease renewal | setting `heartbeat_seconds=0` | fail: a healthy job gets swept |
| Bounded concurrency | spawning more slots than configured | fail: peak engine count exceeds the limit |

The first is why competing sessions set `statement_timeout` — without it,
removing `SKIP LOCKED` makes the test hang rather than fail, and a suite that
hangs on regression is barely better than one that passes on it.

---

## 6. Test isolation — read before writing tests

This has caused four separate rounds of failures across the project. The
remedies are all in place; the trap is real.

- **Never assert global counts.** Scope every assertion to ids the test
  created. The dev database holds real analyses from ordinary use — a global
  count reads those as the test's rows. (Bit us in Sprint 2 and again in
  Sprint 5 CP8.)
- **Two fixtures exist, deliberately.** `db_session` (default) binds everything
  to one connection inside one transaction and rolls back. `committed_db`
  (concurrency only) uses real connections and real commits with explicit
  cleanup. The default fixture **cannot** test `FOR UPDATE SKIP LOCKED`,
  because two claimers in one transaction never contend — such a test would
  pass while proving the opposite of what it claims.
- **Never `db.rollback()` after a read-only query** when the session may be
  caller-supplied: it rolls back to the savepoint and discards the test's
  uncommitted setup. Closing the session already discards the transaction.
- **A Session must not be touched from two threads.** `heartbeat_seconds=0`
  disables the renewal thread for tests on the shared session; the threaded
  path is covered by tests with independent connections.
- **Sample PGN bytes carry a per-session UUID comment** (`tests/sample_data.py`)
  so test imports cannot collide with a manually saved `sample-games.pgn`.

---

## 7. What Sprint 6 inherits

**3,236 position evaluations across 40 games are already stored**, and every
one carries `best_move_uci` alongside the score. That is exactly the input
move-quality analysis needs, and it needs no re-analysis to get started.

Two deliberate affordances left in place for this:

- `PositionEvaluationOut.score` — the **player-relative** score — is already on
  the wire and in the TypeScript types, but the UI renders `white_score`
  instead (standard engine convention). `score` is not dead code; it is what
  centipawn loss should be computed from.
- `best_move_uci` is stored per position specifically so the played move can be
  compared against the engine's preference without re-running anything.

Deriving centipawn loss requires no schema change: it is a function of
consecutive evaluations plus `moves.uci`. Whether derived values should be
**stored** (a new table) or **computed on read** is an open design question —
storing them would be the project's first derived-data table, and every
existing aggregate is computed on read.

---

## 8. Known gaps and deliberate non-goals

Not defects — scoped out on purpose:

- **No move-quality classification.** Evaluations are stored; centipawn loss,
  accuracy, and blunder/mistake labels are not derived from them yet. This is
  the obvious Sprint 6 candidate and the clearest user-facing payoff left.
- **No re-analysis at a different depth.** The data model supports it (a
  different depth is a different `config_hash`, so it would be a new job rather
  than a mutation), but no UI offers it. Small, self-contained, deferred twice.
- **No opening family rollups.** Openings group by full canonical name, so
  `Sicilian Defense: Najdorf Variation` and `…, English Attack` count
  separately.
- **No authentication.** All saved uploads are visible to anyone using the app.
- **No deployment setup.** Local development only; nothing is containerized
  except PostgreSQL. There is no `Dockerfile`. This was a deliberate Sprint 5
  decision: the worker runs as a local process because `STOCKFISH_PATH` is a
  Windows `.exe` that a Linux container cannot execute, and containerizing
  would fork the engine identity that analysis reuse depends on.
- **PGN imports are still synchronous** within the request; only engine
  analysis runs in the background.
- **No generative-AI features.**
- **No CI at present for the worker's concurrency tests beyond the standard
  suite** — they run in the normal backend job and take ~18s.

---

## 9. Environment constraints (developer machine)

- Postgres on host port **5433**, backend on **8001** — defaults were occupied.
- Stockfish is at a Windows path in `backend/.env`, gitignored. **Only the
  worker reads it**; without it, jobs stay queued rather than failing.
- Corporate TLS interception blocks some HTTPS from tooling. The opening
  dataset was cloned manually by the developer for this reason. Certificate
  verification has **not** been disabled anywhere, and should not be.
- `.gitignore` had a blanket `*.ini` that excluded `backend/alembic.ini` and
  broke migrations from a fresh clone. Fixed in Sprint 4; the rule is now
  scoped to `sample-data/*.ini`. Worth remembering before adding broad ignores.

---

## 10. Working practices that have paid off

- **Small checkpoints, one committable concept each, verified before moving on.**
  Sprint 5 ran to 11 checkpoints.
- **A plan file and an ADR before code.** Decisions that came out of
  implementation were amended back into
  `docs/adr/0001-durable-analysis-jobs.md` rather than lost.
- **Deliberately breaking a mechanism to confirm its test fails.** This caught
  that a missing `SKIP LOCKED` would hang rather than fail.
- **Verifying lint and test exit status directly**, never through a pipe —
  `npm run lint | tail` returns `tail`'s exit status, which once produced a
  false "clean" report.
- **Sanitizing every stored error message.** No paths, tracebacks, or
  environment values, verified by test.

---

## 11. Questions for a Sprint 6 planner

1. Should derived move-quality values be **stored** in a new table or
   **computed on read**? Every existing aggregate is computed on read; storing
   would be the first exception, and would need an invalidation story when
   evaluations change.
2. Accuracy is not a standard metric — Lichess and Chess.com use different
   formulas. Which definition, and is it worth matching a familiar one?
3. Should move-quality thresholds (inaccuracy / mistake / blunder) be fixed, or
   vary with position sharpness and material?
4. The 39 unanalysed games are analysable in one click but take real time. Does
   Sprint 6 assume a fully analysed library, or must every feature degrade
   gracefully with partial coverage?
5. Is **deployment** more valuable than more analysis features at this point?
   The project is a strong local portfolio piece but has never run anywhere but
   a laptop, and containerization was explicitly deferred.
6. Re-analysis at a different depth is cheap to add and currently missing.
   Sprint 6 feature, or still not worth it?
