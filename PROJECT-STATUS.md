# Chess Insights — Project Status

**Purpose of this file:** a self-contained snapshot for planning the next
sprint. It assumes no prior context. Hand it to a planner along with the
question "what should Sprint 7 be?"

**Last updated:** 2026-08-11
**Current state:** Sprints 1–6 complete, accepted, and committed.
**Repo:** `main` clean.

---

## 1. What the product does today

A personal chess analytics tool. A single user uploads their own PGN files,
gets statistics and a board viewer, saves analyses permanently, has Stockfish
evaluate saved games as durable background work, and is told which of their own
moves cost the most and what to work on next.

```
upload .pgn  →  preview (stateless)  →  pick which player name is yours
             →  save  →  PostgreSQL  →  reopen any time from "Analysis History"
             →  select a game  →  moves fetched on demand
             →  Analyze Game (or Analyze All Games)
             →  job queued, worker runs Stockfish, page polls progress
             →  evaluation graph, synced to the board
             →  move quality per move, weakness summary per upload,
                training recommendations linked to real positions
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

Added by Sprint 6, all derived on read from evaluations already stored:

- **Per move:** centipawn loss, an inaccuracy / mistake / blunder label,
  `?!` / `?` / `??` annotations in the move list, whether the move matched the
  engine's first choice, and mate errors reported as their own category
- **Per game:** average and median centipawn loss, engine-agreement rate,
  mistake counts, and a filterable "moves to review" list with
  previous/next-issue navigation
- **Per upload:** the same metrics aggregated, split by colour and by opening,
  a ranked list of the costliest positions, and up to four **training
  recommendations** — each stating its sample, citing specific positions, and
  deep-linking to the exact move
- An explainer stating the fixed thresholds and that nothing here understands
  chess

---

## 2. Verified state (reproducible)

Run from a clean checkout with Docker running:

```powershell
docker compose up -d
cd backend && .\.venv\Scripts\python.exe -m pytest -q      # 618 passed
.\.venv\Scripts\python.exe -m alembic check                # clean
cd ..\frontend && npm test && npm run lint && npm run build
```

| Check | Result |
|---|---|
| Backend tests, engine configured | 618 passed |
| Backend tests, `STOCKFISH_PATH=""` | 612 passed, 6 skipped |
| Frontend tests | 138 passed |
| Lint / build | exit 0 / exit 0 |
| `alembic check` | no new upgrade operations |
| Schema built from an empty database | 4 migrations, no drift |

**Sprint 6 added no migration.** The migration count is unchanged from Sprint 5
on purpose: move quality is computed on read, so there is no derived-data table
to keep in step with the evaluations it came from.

Live development data: **3 uploads, 79 games, 6,972 moves, 10 batches, 202 jobs,
82 analysis runs, 7,051 position evaluations, 79 of 79 games fully analysed.**

Move quality over that library: **3,490 player moves, 3,461 of them graded**,
ACPL 20 on both multi-game uploads, engine agreement 51–56%. The 56-game upload
is the only one with enough data to produce a second recommendation beyond
"review these positions" — which is the sample discipline working, not a gap.

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
GET    /api/uploads/{id}/move-quality  aggregate + recommendations (read-only)
DELETE /api/uploads/{id}
POST   /api/uploads/{id}/analysis      bulk enqueue → 202/200/404/503
GET    /api/games/{id}                 full detail incl. moves
GET    /api/games/{id}/analysis        discriminated union (see below)
GET    /api/games/{id}/move-quality    per-move grades (read-only)
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

Added by Sprint 6 (full list, with rationale, in
`docs/sprint-6-acceptance.md`):

19. Move quality is derived on read. No derived-data table, no migration.
20. A mate is never converted into a centipawn number, at any layer.
21. `mate_in_white = 0` is attributed by **ply parity**, never by a sign.
22. Centipawn loss is clamped at zero.
23. Only the saved player's moves are graded.
24. A game matching neither player is `not_gradable`, never graded as White.
25. Every aggregate's denominator is completed, gradable games only, and every
    figure is shown beside its sample.
26. Thresholds and sample floors are server-owned; none is a request parameter.
27. A recommendation fires only when its sample floor **and** its
    meaningfulness gate are met, and cites positions from the upload asked for.
28. Recommendation wording claims no chess motif (denylist test).
29. Ordering is total — equal severities cannot swap between requests.
30. Every Sprint 6 endpoint is read-only.
31. The evidence payload holds no path, secret, or environment value.
32. Facts and generated prose are structurally separated.

---

## 5. Tests that must be able to fail

Some mechanisms are invisible when working and catastrophic when silently
broken. Each was verified by deliberately breaking the code and confirming the
test goes red. **If any of these ever passes after such a change, the test has
stopped testing anything.**

Sprint 5's three, re-confirmed in Sprint 6 CP10 and **not to be rewritten to
make a newer test easier**:

| Mechanism | Break it by | Test must |
|---|---|---|
| `FOR UPDATE SKIP LOCKED` | removing `skip_locked=True` | fail with a statement timeout, **not hang** |
| Background lease renewal | setting `heartbeat_seconds=0` | fail: a healthy job gets swept |
| Bounded concurrency | spawning more slots than configured | fail: peak engine count exceeds the limit |

The first is why competing sessions set `statement_timeout` — without it,
removing `SKIP LOCKED` makes the test hang rather than fail, and a suite that
hangs on regression is barely better than one that passes on it.

Sprint 6 adds seven more, listed in `docs/sprint-6-acceptance.md`. The one that
needs breaking **in both directions** is mate-zero ply parity: a single fixed
sign is right by accident for one colour, so `True` and `False` must each be
tried.

CP10 also probed ten mechanisms the plan did *not* name, looking for a break
nothing catches. Two were found and closed: both percentage helpers could be
swapped to Python's `round()` with the entire suite green, and the coverage
precedence could be reordered without any test noticing. **Confirming that
planned tests fail is weak evidence — they were written for those mechanisms.
The probes are where the real gaps were.**

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

## 7. What Sprint 7 inherits

The open design question Sprint 6 inherited — store derived move quality or
compute it on read — **was answered: compute on read.** Aggregating a 56-game
upload takes five bounded queries and in-memory arithmetic, and the query count
is asserted by test rather than assumed. There is still no derived-data table,
and no invalidation story is needed because there is nothing to invalidate.

Two affordances left in place for a future sprint:

- **`app/schemas/evidence.py` and `app/services/evidence.py`** define a stable,
  self-contained record of one position — game context, the move, what the
  engine said, the assessment, and full provenance — assembled only from stored
  rows and Sprint 6 derivations. It is **not exposed over HTTP** and nothing
  consumes it. It exists so a later sprint can hand a position to a model
  without that model recomputing anything or reaching for the database.
- **`EvidenceEnvelope.explanation`** is the single slot where generated prose
  would sit, beside the evidence rather than inside it. It is always null.
  There is deliberately nowhere inside an evidence item to put a sentence, so a
  model's output can never later be mistaken for something measured.

`EVIDENCE_CONTRACT_VERSION` (payload shape) and `MOVE_QUALITY_VERSION` (the
rules that produced the values) are separate on purpose: a consumer needs one
to parse and the other to know what the numbers mean.

The library is now **fully analysed — 79 of 79 games**, so a Sprint 7 feature
can assume real data rather than a thin sample. It should still degrade
gracefully: coverage handling is load-bearing and well tested, and throwing it
away would be a regression.

---

## 8. Known gaps and deliberate non-goals

Not defects — scoped out on purpose:

- **No re-analysis at a different depth.** The data model supports it (a
  different depth is a different `config_hash`, so it would be a new job rather
  than a mutation), but no UI offers it. Small, self-contained, **deferred
  three times now** — Sprints 4, 5, and 6. Either do it or stop listing it.
- **No accuracy percentage.** Centipawn loss, labels, and counts are derived;
  a single headline "accuracy" figure is not. Deliberate: Lichess and
  Chess.com use different formulas, and inventing a third that resembles
  neither would be worse than showing none.
- **Recommendations do not name chess motifs.** They can say which positions
  cost the most and where the losses cluster, never *why* in chess terms. That
  is enforced by a denylist test, not merely unimplemented — the rules are
  arithmetic and have no basis for a motif claim.
- **No position-level explanations.** The evidence contract is defined but not
  exposed or consumed.
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
  Sprints 5 and 6 each ran to 11 checkpoints.
- **Verifying against the developer's real library, not only fixtures.** This
  is the practice that has paid off most. In Sprint 6 it caught a
  recommendation firing on four centipawns of noise, and a test heuristic that
  would have rejected a legitimate 80-character opening name. Both passed every
  fixture.
- **Probing for mechanisms no test defends**, rather than only confirming that
  planned tests fail. The planned breaks all behaved; the two real gaps came
  from ten extra probes.
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

## 11. Questions for a Sprint 7 planner

1. Is **deployment** now the most valuable thing left? It has been deferred
   twice while the feature set grew. The project is a strong local portfolio
   piece that has never run anywhere but a laptop, and the honest blocker is
   specific: `STOCKFISH_PATH` is a Windows `.exe`, and containerizing the
   worker would fork the engine identity that analysis reuse depends on. That
   is a real design problem, not a packaging chore.
2. **Generative explanations** are the obvious next feature and the payload is
   ready for them. But the whole sprint was built so a model's output can never
   be mistaken for a measurement — what is the acceptance criterion for a
   generated sentence, and how does a reader tell it from a derived one?
3. Should move-quality thresholds stay **fixed**? They are currently the same
   in every position. A 100-centipawn drop in a sharp middlegame and in a dead
   drawn endgame are not the same event, but varying them makes every number
   incomparable across games and much harder to defend.
4. **Opening family rollups** would make the per-opening table far more useful
   on a 56-game library — Najdorf lines currently split across many buckets, so
   most fall under the sample floor and are excluded from recommendations. Is
   that a Sprint 7 feature or a smaller fix to the existing table?
5. Re-analysis at another depth has now been deferred three times. Commit to it
   or delete it from the roadmap.
6. Is anything needed for **more than one user**? There is still no
   authentication, and every design decision so far has assumed a single
   trusted person. That assumption is load-bearing in more places than the
   missing login screen.
