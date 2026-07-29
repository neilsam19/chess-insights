# Chess Insights — Sprint 5 Plan (Revised)

**Sprint theme:** Durable, asynchronous, bounded engine analysis
**Supersedes:** the checkpoint plan in `CHESS-INSIGHTS-SPRINT-5-PLAN.md` §10.
**Retains unchanged:** that document's §2 (execution-model decision), §7 (retry
policy), §11 (invariants), and §12 (risks). Those are correct and are not
restated here — read them alongside this file.

## Why this revision exists

The original plan was written without visibility into the current codebase. Its
architecture is sound; its checkpoints assume five things about the repository
that are not true. This file re-cuts the work into sessions that match what
actually exists, and turns those five assumptions into explicit decisions to be
made in Checkpoint 0 rather than discovered mid-sprint.

---

## 1. Findings against the current code

### F1 — The test fixture cannot exercise `FOR UPDATE SKIP LOCKED`

`backend/tests/conftest.py` binds every session in a test to **one connection
inside one outer transaction**, using
`join_transaction_mode="create_savepoint"`, and rolls the whole thing back at
teardown. Application `commit()` calls only release a savepoint.

`FOR UPDATE SKIP LOCKED` skips rows locked by *other* transactions. Two
"competing claimers" built on that fixture share a single transaction, so both
would claim the same job and the test would pass while proving nothing —
the worst kind of green test.

Real claim-contention tests need **two independent connections**, which means
**committed rows**, which means the rollback fixture does not apply and those
tests need their own cleanup.

This is the highest-risk item in the sprint. Test isolation has already caused
three separate rounds of failures across Sprints 2–4 (empty-database
assumptions, the duplicate-PGN collision, services constructing their own
sessions). This would be the fourth, and unlike the others it fails silently.

**Consequence:** the claim-protocol tests get their own checkpoint (CP2), and
the isolation strategy is decided before any of them are written.

### F2 — "Add the worker to Docker Compose" means containerizing the backend

There is no `Dockerfile` anywhere in the repository. `compose.yaml` defines
exactly one service, `db`. The backend has always run from the venv via
`dev.ps1`.

Worse, it cannot work as described: `STOCKFISH_PATH` points at a Windows
`.exe`, and a Linux container cannot execute it. Containerizing the worker
would require also solving Stockfish-in-container, which is a Linux binary, a
second engine build, and a divergence between what CI, the container, and the
developer machine each run.

**Decision (proposed):** do not containerize in Sprint 5. Run the worker as a
second local process alongside uvicorn, started by `dev.ps1`. Compose keeps
providing Postgres only. Containerization belongs with deployment, after
Sprint 6.

### F3 — Config identity requires the binary hash, but only the launcher computes it

The design requires `config_hash` to include the Stockfish SHA-256, and
requires the enqueue endpoint to return `503` when the engine is unusable.

`build_stockfish_engine()` in `backend/app/services/engine.py` currently does
three things at once: validate the configured path, hash the executable
(`_hash_binary`, which reads the whole file), and spawn the UCI subprocess.

The API must be able to answer "is the engine usable, and what is its config
identity?" **without spawning Stockfish on every enqueue**, and certainly
without re-hashing a ~50 MB binary per request.

**Consequence:** `engine.py` needs a cheap, cached `resolve_engine_config()`
that validates the path and returns `(binary_sha256, options, depth)`,
separate from the launcher. Scheduled into CP1, not discovered in CP6.

### F4 — Two competing authorities for idempotency

`analyze_game()` in `backend/app/services/game_analysis_service.py` already
implements the reuse and conflict rules itself: it raises
`AnalysisAlreadyRunningError` when the latest run is `running`, and returns
`AnalysisResult(created=False)` when the latest run is `complete`.

Once jobs own queuing, those same rules exist in two places with different
data. The plan's §8 note that "a hidden synchronous path may remain
internally" leaves this undecided.

**Decision (proposed):** strip `analyze_game()` to a **pure executor** — given
a game and a config, produce evaluations or fail. All idempotency (reuse,
conflict, skip) moves to the enqueue and claim layer. One authority.

### F5 — `analysis_runs` has no `cancelled` state

`backend/app/models/analysis_run.py` defines only `STATUS_RUNNING`,
`STATUS_COMPLETE`, `STATUS_FAILED`. The design requires cancelled attempts to
exist and to hold zero evaluations. Add `STATUS_CANCELLED`, and keep the
existing convention: plain strings, not a database enum, matching
`user_color` / `opening_source`.

---

## 2. Checkpoint sizing changes

| Original | Problem | Revision |
|---|---|---|
| CP0 (docs) | No tests; mostly re-transcribes §5/§7/§8 | Kept, but repurposed to resolve F1–F5 |
| CP1 (schema + repos) | Two concerns, and the repo tests are where F1 bites | Split into CP1 schema / CP2 repositories |
| CP3 (heartbeat + stale + cancel + retry) | Four separable features in one session | Split into CP4 lease/recovery / CP5 cancel+retry |
| CP4 (API + UI) | Sprint 4 shipped API and UI as separate commits | Split into CP6 API / CP7 UI |
| CP5 (bulk API + UI) | Same | Split into CP8 API / CP9 UI |
| CP6 (concurrency) | Satisfiable by the single-slot worker already | Folded into CP10 |

Eleven sessions instead of eight. Each is one committable concept with its own
tests — the shape that worked for Sprints 3 and 4.

---

## 3. Checkpoints

Each checkpoint ends with: tests passing, `alembic check` clean where relevant,
and one commit. Do not start the next checkpoint until the current one passes.

Baseline to preserve throughout: **180 backend tests** with an engine
configured, **175 passed + 5 skipped** without one, **31 frontend tests**,
`alembic check` clean, CI green on all three jobs.

---

### CP0 — Decisions and architecture record

No code. This checkpoint exists to close F1–F5 so no later checkpoint stalls
on an open question.

**Build**

- ADR recording the separate-worker / PostgreSQL-queue decision. Summarize
  §2 of the original plan; do not re-argue it.
- Resolve and record each of F1–F5 as a decision with its reasoning.
- Write the exact job and batch state machines, including which transitions
  are enforced by the database and which by service logic.
- Enumerate retryable vs. non-retryable error codes as a concrete list of
  string constants, not prose categories.
- Write the API response schemas before implementing them.
- Restate the non-goals: no Redis, no position-level resume, no WebSockets,
  no containerization this sprint.

**Acceptance**

- No status transition in the state machine is ambiguous or unreachable.
- Every terminal state has a documented meaning and a documented UI treatment.
- The test-isolation strategy for claim contention (F1) is written down
  before CP2 begins.
- The document explains how this could later migrate to Redis without
  changing the frontend contract.

**Commit:** `docs: add sprint 5 architecture decision record`

---

### CP1 — Schema and engine-config identity

**Build**

- `analysis_batches` and `analysis_jobs` tables per §4 of the original plan.
- Link `analysis_runs` to jobs: `analysis_job_id` (nullable,
  `ON DELETE SET NULL`) and `attempt_number`. Nullable because Sprint 4 runs
  predate jobs and must survive the migration.
- Add `STATUS_CANCELLED` to `analysis_run.py` (F5).
- Constraints and indexes:
  - `CHECK completed_positions BETWEEN 0 AND total_positions`
  - index `(status, available_at, created_at)` for claiming
  - index `(batch_id, status)` for progress
  - index `(game_id, created_at DESC)`
  - **partial unique index** on `(game_id, config_hash)`
    `WHERE status IN ('queued','running')`
- Split `resolve_engine_config()` out of `build_stockfish_engine()` (F3):
  validates the configured path, returns binary SHA-256 + canonical options,
  never spawns a subprocess. Cache the hash by `(path, mtime, size)`.
- Implement `compute_config_hash()` over: `game_hash` (already on
  `games.game_hash`), binary SHA-256, requested depth, canonically ordered
  engine options, and an `ANALYSIS_BEHAVIOR_VERSION` constant.

**Tests**

- `alembic upgrade head` from the **current populated Sprint 4 database** —
  3 uploads / 79 games / 8 runs / 430 evaluations must survive intact.
- `alembic upgrade head` from an empty database.
- `alembic check` clean.
- Constraint tests: negative progress rejected; progress above total rejected;
  two active jobs for the same `(game_id, config_hash)` rejected by the
  database, not just by service code.
- Cascade tests: deleting an upload removes batches, jobs, runs, evaluations.
- `compute_config_hash()` is stable across process restarts and changes when
  depth, options, or binary change.
- `resolve_engine_config()` raises `EngineUnavailableError` for a missing or
  non-file path, and never launches a process (assert via a spawn spy).

**Acceptance**

- Existing Sprint 4 data migrates with no loss and no orphaned runs.
- The uniqueness invariant is enforced by Postgres.
- Config identity can be computed without starting Stockfish.

**Commit:** `feat: add analysis job and batch schema with config identity`

---

### CP2 — Repository layer and the contention test fixture

The riskiest checkpoint. It is alone deliberately.

**Build**

- A `committed_db` fixture that does **not** use the savepoint-rollback
  pattern: real connections, real commits, explicit teardown scoped to ids
  the test created. Document loudly why it exists and that it is only for
  concurrency tests.
- Repository functions with explicit transaction boundaries:
  `enqueue_batch`, `claim_next_job`, `heartbeat_job`, `request_cancel`,
  `complete_job`, `fail_job`, `requeue_job`, `find_stale_jobs`.
- `claim_next_job` uses `FOR UPDATE SKIP LOCKED`, sets `status`,
  `attempt_count`, `claimed_by`, `started_at`, `heartbeat_at`,
  `lease_expires_at`, and creates the linked run — all in one short
  transaction.
- All lease and expiry comparisons use **database time** (`now()`), never
  Python's clock.

**Tests**

- **Two independent connections cannot claim the same job.** Must use
  `committed_db`. Assert the second claimer gets a *different* job or none.
- A claim commits and releases before any engine work would begin — assert no
  open transaction is held after `claim_next_job` returns.
- Claiming respects `available_at` (a future value is not claimed).
- Claiming prefers the oldest eligible job.
- Every state transition function rejects transitions not in the state
  machine.
- Teardown leaves no rows behind: assert row counts before and after.

**Acceptance**

- The contention test genuinely fails if `SKIP LOCKED` is removed. **Verify
  this by deleting it and watching the test go red** before committing.
- The ordinary suite still uses the rollback fixture and still leaves the dev
  database clean.

**Commit:** `feat: add analysis job repository with safe concurrent claiming`

---

### CP3 — Single-slot worker

**Build**

- `python -m app.worker` entry point with `--once` for deterministic tests.
- Claim → release transaction → run engine → persist, using the three-short-
  transaction shape already proven in `game_analysis_service.py`.
- Refactor `analyze_game()` into a pure executor (F4): it takes a game and a
  config, returns evaluations or raises. Reuse/conflict logic is deleted from
  it and now lives only in the enqueue and claim layer.
- Before the final commit, verify the job is still owned by this worker and
  not cancelled. Discard results otherwise.
- Add worker startup to `dev.ps1` as a third window. **No Dockerfile, no
  compose changes** (F2).

**Tests**

- A successful job produces exactly `N+1` evaluations and marks run and job
  complete.
- A failed execution stores **zero** evaluations and clears lease fields.
- A worker whose ownership changed mid-run discards its results.
- `--once` exits cleanly with an empty queue.
- Real-Stockfish worker test added to the existing `engine` CI job.

**Acceptance**

- The API and the worker start and stop independently.
- Restarting uvicorn does not disturb a running worker job.
- One game completes with no HTTP request open for its duration.

**Commit:** `feat: add analysis worker process`

---

### CP4 — Heartbeats, leases, and stale recovery

**Build**

- Heartbeat loop independent of individual Stockfish position calls, so a slow
  position cannot expire its own lease.
- Configurable `ANALYSIS_HEARTBEAT_SECONDS` (default 5) and
  `ANALYSIS_LEASE_SECONDS` (default 60).
- Stale sweep on worker startup and periodically thereafter: find `running`
  jobs whose lease expired, mark the abandoned run `failed` with an explicit
  `worker_lease_expired` code, requeue if attempts remain, otherwise fail.
- Throttle progress writes by time or position count; heartbeat stays
  independent and reliable.

**Tests**

- An expired running job is requeued when attempts remain.
- An expired job on its final attempt becomes `failed`.
- The abandoned `analysis_run` becomes `failed`, not left `running`.
- A long single position does not expire its own lease.
- Progress is monotonic within an attempt.

**Acceptance**

- **Killing the worker mid-game no longer leaves an unrecoverable permanent
  `running` record.** This is the specific defect flagged as Sprint 5's top
  priority in `PROJECT-STATUS.md`.

**Commit:** `feat: add worker heartbeats and stale job recovery`

---

### CP5 — Cancellation and retry

**Build**

- `cancel_requested_at` as a control flag; the worker observes it between
  positions and transitions the job to `cancelled`.
- Queued cancellation prevents the job from ever being claimed.
- Retry policy per §7 of the original plan, as the concrete error-code list
  written in CP0.
- Manual retry creating a **new attempt and a new run**, never mutating
  history.
- Structured `error_code` alongside the existing sanitized `error_message`.
  Sanitization behavior from Sprint 4 must be preserved — no paths, no
  tracebacks.

**Tests**

- Cancelling a queued job prevents the claim.
- Cancelling a running job stops at the next position boundary and stores
  zero evaluations.
- Manual retry creates a new run and leaves prior runs untouched.
- Non-retryable errors are not automatically retried.
- `max_attempts` is respected.
- Error messages still leak no filesystem paths.

**Acceptance**

- Every terminal state is reachable and recoverable per the CP0 state machine.
- No cancellation path leaves partial evaluations.

**Commit:** `feat: add analysis cancellation and retry policy`

---

### CP6 — Asynchronous single-game API

**Build**

- `POST /api/games/{id}/analysis` becomes enqueue-and-return:
  `202` queued, `200` reused, `409` already active, `404` not found,
  `422` unanalyzable, `503` engine unusable at enqueue.
- Extend `GET /api/games/{id}/analysis` with `queued` and `running` variants
  carrying job id, optional batch id, `completed_positions`,
  `total_positions`, percent, latest error, and whether retry is allowed.
  Keep the existing discriminated-union shape.
- `POST /api/analysis/jobs/{id}/cancel` and `/retry`.
- `GET /api/analysis/jobs/{id}`.

**Tests**

- Full status matrix: 202 / 200 / 409 / 404 / 422 / 503.
- A matching completed analysis is reused, not re-run.
- A matching active job is not duplicated.
- No request holds the engine — assert the endpoint returns before any
  evaluation exists.
- Clients cannot set job state, progress, engine identity, or results.

**Acceptance**

- The endpoint returns in well under a second regardless of game length.
- The response contract is a superset of Sprint 4's, so the graph still
  renders from a completed analysis unchanged.

**Commit:** `feat: convert game analysis endpoint to durable job creation`

---

### CP7 — Single-game frontend

**Build**

- `GameAnalysisPanel` polls while queued or running (1–2 s), stops on terminal
  states, and clears timers on unmount.
- Queued, running-with-progress, cancelled, and failed-with-retry states.
- Cancel button while queued or running; retry only when eligible.
- Board and move navigation stay usable during analysis.
- Reopening or reloading reconstructs state from the server — the panel is
  already keyed by `gameId`, so remount-per-game behavior stays as is.
- Back off after repeated network failures; a polling failure is **not** an
  analysis failure.

**Tests**

- Each state renders correctly.
- Timers are cleaned up on unmount (fake timers; note this is a new pattern
  for this suite — existing tests only use `vi.clearAllMocks()`).
- Polling stops on terminal states.
- A transient fetch failure does not render the analysis as failed.
- `npm run lint` clean — verify with `${PIPESTATUS[0]}`, not a piped tail.

**Acceptance**

- Clicking Analyze Game returns immediately.
- Reloading mid-analysis recovers progress.
- A completed analysis renders the existing evaluation graph, still
  board-synchronized.

**Commit:** `feat: poll durable analysis jobs from the game analysis panel`

---

### CP8 — Bulk upload analysis API

**Build**

- `POST /api/uploads/{upload_id}/analysis`: one batch and one job per eligible
  game, created in a single transaction.
- Report queued / skipped-as-complete / already-active / ineligible counts,
  plus a config summary.
- One service function as the **only** authority for batch-state derivation.
- `GET /api/analysis/batches/{id}` and
  `POST /api/analysis/batches/{id}/cancel`.

**Tests**

- Batch creation is all-or-nothing.
- Completed matching games are skipped, not re-run.
- Batch state derives correctly across every mixed combination of job states.
- Batch cancellation affects queued and running jobs correctly.
- Deleting an upload cascades through batch, job, run, and evaluation records.
- Repeating an identical bulk request queues nothing new.

**Acceptance**

- A 79-game upload can be queued in one action, in one transaction.
- Re-requesting does not silently redo completed work.

**Commit:** `feat: add bulk upload analysis with batch tracking`

---

### CP9 — Bulk dashboard UI

**Build**

- "Analyze Upload" action on the saved dashboard.
- Games complete / total, plus queued / running / failed / cancelled counts.
- Which games were skipped because a matching analysis already existed.
- Batch cancel, links to failed games, per-game retry.
- Same polling discipline as CP7.

**Tests**

- Batch progress rendering across mixed states.
- Cancel and retry controls appear only when applicable.
- Polling stops and timers clear.

**Acceptance**

- Queue an upload, navigate away, return, and see accurate live progress.

**Commit:** `feat: add bulk analysis controls to the saved dashboard`

---

### CP10 — Bounded concurrency, hardening, and acceptance

**Build**

- `ANALYSIS_WORKER_CONCURRENCY`, default `1`. Values above 1 use a fixed
  executor with independent session and job ownership per slot. Never derived
  from submitted game count.
- Startup logging of concurrency, Stockfish threads, and hash allocation, with
  the documented CPU expectation `concurrency × STOCKFISH_THREADS`.
- Graceful shutdown: stop claiming, finish or cancel active work under a
  documented policy, exit.
- Structured logs carrying job id, batch id, game id, attempt, worker id,
  transition.
- Update `README.md`, environment variable docs, `dev.ps1`, and the
  architecture section. Add a manual acceptance checklist.

**Tests**

- Simultaneous engine instances never exceed configuration.
- Jobs are not double-claimed across slots.
- One slot failing does not terminate other slots.

**Full verification**

```powershell
docker compose up -d

cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm test
npm run lint
npm run build
```

**Manual acceptance**

1. Queue one game; response is immediate.
2. Restart the API; the worker continues.
3. Kill the worker mid-game.
4. Restart it; observe stale recovery and retry.
5. Cancel a running game; confirm zero partial evaluations.
6. Queue a whole upload; observe bounded concurrency.
7. Repeat the bulk request; confirm completed games are skipped.
8. Reload the browser mid-job; progress recovers.
9. Complete a job; the evaluation graph is still board-synchronized.

**Acceptance**

- All Sprint 1–4 invariants still hold, plus the twelve in §11 of the original
  plan.
- CI green from a fresh clone against an empty database.
- No request stays open for the duration of analysis.
- No stale running job is permanently unrecoverable.
- No workload can launch more Stockfish processes than configured.

**Commit:** `feat: add bounded worker concurrency and sprint 5 hardening`

---

## 4. Standing constraints

Carried forward from Sprints 1–4 and non-negotiable:

- Never accept a client-supplied engine path, UCI command, engine option,
  FEN, evaluation, job state, or progress value.
- Never assemble a shell string from request data.
- Sanitize every error message: no filesystem paths, no tracebacks, no
  environment values.
- Never hold a database transaction while Stockfish is running.
- Scope every test assertion to ids the test created — never to global counts.
- Verify lint and test exit status directly, never through a pipe.
- The opening dataset stays vendored; nothing is fetched at runtime.

## 5. Stopping point

Sprint 5 is done when durable asynchronous single-game and whole-upload
analysis work reliably under one configurable worker, with crash recovery,
progress, cancellation, retry, idempotency, and bounded concurrency.

Do not extend into queue dashboards, Redis, deployment orchestration, or
position-level checkpointing. Go straight to Sprint 6, where the stored
evaluations finally become visible user value: centipawn loss, move-quality
labels, phase breakdowns, and game accuracy.
