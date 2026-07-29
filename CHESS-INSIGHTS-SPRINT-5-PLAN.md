# Chess Insights — Sprint 5 Plan

**Sprint theme:** Durable, asynchronous, bounded engine analysis  
**Starting point:** Sprints 1–4 accepted; CI green; synchronous per-game Stockfish analysis works end to end.  
**Primary goal:** A request to analyze one game or an entire upload becomes durable work that survives API restarts, exposes progress, supports cancellation/retry, and never launches unbounded Stockfish processes.

---

## 1. Is the project already a resume-level v1 MVP?

Yes. Sprint 4 already completes a credible v1 vertical slice:

- A real user workflow exists from PGN upload through persistent history and engine analysis.
- The backend has normalized PostgreSQL persistence, migrations, constraints, and cascade behavior.
- Opening classification is based on played positions rather than trusting PGN labels.
- Stockfish analysis is persisted with engine provenance and typed mate/centipawn values.
- Large arrays are loaded on demand instead of being included in list responses.
- The repository is reproducible from a fresh clone and fresh database.
- CI separately verifies backend, frontend, migrations, and real engine integration.
- The project has meaningful automated coverage rather than only manual demos.

This is substantially stronger than a typical portfolio CRUD project. Sprint 5 should be treated as a reliability and systems-design upgrade, not as work required to make the project legitimate.

For the eventual polished v1, the highest-value remaining sequence is:

1. Sprint 5: durable engine jobs.
2. Sprint 6: move-quality and accuracy insights, which create the clearest user-facing analytical payoff.
3. Deployment, seeded/demo data, screenshots, architecture documentation, and a short recorded walkthrough.

Authentication, Redis, distributed workers, and LLM features are not required for the project to be resume-worthy.

---

## 2. Explicit execution-model decision

### Decision

Use a **separate analysis worker process backed by PostgreSQL job records**.

- FastAPI handles HTTP requests and writes durable jobs.
- A separate `analysis-worker` process claims jobs from PostgreSQL.
- PostgreSQL remains the only infrastructure service.
- The worker uses a fixed, configurable concurrency limit.
- The React application polls job or batch status endpoints.

Add the worker as a second application service in Docker Compose, but do not add Redis, Celery, RQ, or arq in Sprint 5.

### Why not FastAPI in-process background tasks?

In-process tasks are inappropriate for the problem Sprint 5 is intended to solve:

- Work is tied to the API process lifecycle and can disappear on restart.
- Multiple API server processes would each have independent, difficult-to-coordinate concurrency.
- Long CPU-intensive Stockfish work competes with request handling.
- Deployment reloads and crashes make stale-run recovery harder rather than easier.
- There is no clean operational boundary between serving requests and executing jobs.

FastAPI background tasks would make the endpoint return quickly, but they would not provide durable asynchronous execution.

### Why not Redis plus Celery/RQ/arq now?

A Redis-backed queue is valid when the system needs high throughput, many worker hosts, advanced routing, scheduled tasks, or mature operational tooling. This project currently needs none of those.

Adding Redis now would introduce:

- Another always-on service, configuration surface, health check, and persistence concern.
- Dual sources of truth between queue state and PostgreSQL analysis records.
- More difficult local setup and CI.
- Framework-specific retry and acknowledgement semantics before the product needs them.
- A larger architectural commitment with little visible benefit at current scale.

PostgreSQL already provides durability, transactions, row locks, indexing, and `FOR UPDATE SKIP LOCKED`, which are sufficient for a bounded local/single-host worker queue.

### Why a PostgreSQL-backed separate worker fits this v1

- A committed job exists before the API responds.
- API restarts do not lose queued work.
- Worker restarts can recover expired leases.
- `FOR UPDATE SKIP LOCKED` permits safe claims if a second worker is added later.
- Concurrency is explicit and independent of API process count.
- Job status and product state live in one transactional database.
- The design can later be migrated to Redis/Celery without changing the analysis domain model or frontend contract.

### Revisit Redis only when one of these becomes true

- Analysis is deployed across multiple machines or autoscaled worker replicas.
- PostgreSQL polling or lock contention becomes measurable.
- Queue routing, priorities, scheduling, or rate limiting become product requirements.
- Job volume is high enough that the database queue materially affects normal application queries.
- Operational dashboards and framework-managed retries are worth the added dependency.

---

## 3. Sprint 5 scope

### Must deliver

- Durable queued engine jobs.
- A separate worker process.
- Single-game asynchronous analysis.
- Whole-upload bulk analysis.
- Bounded Stockfish concurrency.
- Per-game and aggregate batch progress.
- Cancellation.
- Explicit retry behavior.
- Recovery of stale `running` work after a worker crash.
- Idempotent reuse/skipping of matching completed analyses.
- A UI that can leave/reopen a page without losing the job.
- CI coverage for worker behavior and one real Stockfish path.

### Explicit non-goals

- Redis/Celery/RQ/arq.
- WebSockets or server-sent events; polling is sufficient for v1.
- Position-level persistence and resume inside a partially analyzed game.
- Multiple priority queues.
- Multi-user fairness or quotas.
- Distributed worker autoscaling.
- Scheduled or recurring analysis.
- Sprint 6 move-quality labels or game accuracy.

### Definition of “resumability” for this sprint

Resumability is **game-level**, not position-level:

- Completed games in a bulk request are never repeated unnecessarily.
- A queued game remains queued across process restarts.
- A running game whose worker dies is requeued and restarted from ply 0.
- Existing completed evaluations remain reusable when their analysis configuration matches.

Do not persist partial position evaluations during a run. This preserves the existing invariant that failed/cancelled runs contain no evaluations and keeps Sprint 6 inputs simple and trustworthy.

---

## 4. Proposed data model

Keep `analysis_runs` as the record of an actual engine attempt and its immutable provenance/results. Do not overload it into the entire queue abstraction.

### `analysis_batches`

One user request. A single-game request may also create a one-job batch so the same progress and cancellation model works everywhere.

Suggested fields:

```text
id UUID PK
upload_id UUID NULL FK uploads ON DELETE CASCADE
status queued|running|complete|partial_failed|failed|cancelled
config_hash TEXT NOT NULL
requested_depth INTEGER NOT NULL
engine_options JSONB NOT NULL
created_at TIMESTAMPTZ NOT NULL
started_at TIMESTAMPTZ NULL
completed_at TIMESTAMPTZ NULL
cancel_requested_at TIMESTAMPTZ NULL
```

Batch counts and percentages should normally be derived from child jobs rather than maintained as mutable counters.

### `analysis_jobs`

One independently claimable game analysis unit.

Suggested fields:

```text
id UUID PK
batch_id UUID NOT NULL FK analysis_batches ON DELETE CASCADE
game_id UUID NOT NULL FK games ON DELETE CASCADE
status queued|running|complete|failed|cancelled
config_hash TEXT NOT NULL
attempt_count INTEGER NOT NULL DEFAULT 0
max_attempts INTEGER NOT NULL DEFAULT 2
available_at TIMESTAMPTZ NOT NULL
claimed_by TEXT NULL
lease_expires_at TIMESTAMPTZ NULL
heartbeat_at TIMESTAMPTZ NULL
started_at TIMESTAMPTZ NULL
completed_at TIMESTAMPTZ NULL
cancel_requested_at TIMESTAMPTZ NULL
total_positions INTEGER NOT NULL
completed_positions INTEGER NOT NULL DEFAULT 0
latest_run_id UUID NULL
error_code TEXT NULL
error_message TEXT NULL
```

Recommended constraints/indexes:

- `CHECK completed_positions BETWEEN 0 AND total_positions`.
- Index `(status, available_at, created_at)` for claiming.
- Index `(batch_id, status)` for progress queries.
- Index `(game_id, created_at DESC)`.
- A partial unique index preventing two active jobs for the same `(game_id, config_hash)` while status is `queued` or `running`.

### `analysis_runs`

Retain current fields and add enough linkage to preserve attempt history:

```text
analysis_job_id UUID NULL FK analysis_jobs ON DELETE SET NULL
attempt_number INTEGER NULL
```

Each retry creates a new `analysis_run`. A failed or cancelled attempt contains no `position_evaluations`.

### Analysis configuration identity

Create a canonical server-side configuration payload and hash it. It should include at least:

- Game identity, preferably `game_hash`.
- Stockfish binary SHA-256.
- Requested depth.
- Canonically ordered engine options.
- Any analysis behavior version that would alter output interpretation.

Do not trust a client-provided hash or engine identity.

---

## 5. State machines

### Job state transitions

```text
queued -> running -> complete
queued -> cancelled
running -> cancelled
running -> failed
running -> queued       # expired lease and retry still allowed
failed -> queued        # explicit retry
cancelled -> queued     # explicit retry, if supported
```

`cancel_requested_at` is a control flag, not necessarily a standalone terminal state. A running worker observes it and then transitions the job to `cancelled`.

### Batch state derivation

Derive batch state from its jobs:

- `queued`: all jobs queued.
- `running`: at least one queued/running and none terminally determines completion.
- `complete`: all jobs complete or intentionally skipped because a matching completed run already exists.
- `partial_failed`: at least one complete and at least one failed.
- `failed`: all executable jobs failed.
- `cancelled`: no running/queued jobs remain and cancellation prevented completion.

Implement one service function as the only authority for batch-state derivation.

---

## 6. Worker behavior

### Claim protocol

In one short transaction:

1. Select the oldest eligible queued job using `FOR UPDATE SKIP LOCKED`.
2. Set `status = running`.
3. Increment `attempt_count`.
4. Set `claimed_by`, `started_at`, `heartbeat_at`, and `lease_expires_at`.
5. Create the linked `analysis_run` with `status = running`.
6. Commit.

Release the transaction before starting Stockfish.

### Execution

- Load game positions through the repository/service layer.
- Start one Stockfish subprocess for the game.
- Accumulate evaluations in memory as Sprint 4 does.
- Update job progress and heartbeat periodically in short independent transactions.
- Check `cancel_requested_at` between positions.
- Close Stockfish in `finally`.
- Before committing results, verify the job is still owned by this worker and has not been cancelled.

### Successful completion

In one short transaction:

- Insert all position evaluations.
- Mark the analysis run complete.
- Mark the job complete with full progress.
- Clear lease ownership fields.
- Recompute/derive the parent batch state.

### Failure

- Store no evaluations.
- Mark the run failed with a sanitized error.
- Classify the error as retryable or non-retryable.
- Requeue only when policy allows; otherwise mark the job failed.
- Always clear worker ownership/lease fields.

### Heartbeat and stale recovery

- Run a heartbeat loop independent of individual Stockfish position calls.
- Suggested heartbeat interval: configurable, e.g. 5 seconds.
- Suggested lease duration: configurable and comfortably larger, e.g. 30–60 seconds.
- On worker startup and periodically thereafter, find `running` jobs whose lease expired.
- Mark the abandoned `analysis_run` failed with an explicit worker-lease-expired error.
- Requeue the job if attempts remain; otherwise fail it.

The worker must use database time for lease comparisons to avoid local clock discrepancies.

### Bounded concurrency

Use an explicit `ANALYSIS_WORKER_CONCURRENCY` setting.

- Default to `1` for safe local development.
- Permit a small fixed value later.
- Never derive concurrency from the number of submitted games.
- Document that approximate Stockfish CPU demand is `worker_concurrency × STOCKFISH_THREADS`, with memory additionally affected by hash allocation.

A worker may initially execute one claimed job at a time. That already satisfies bounded concurrency. A fixed executor can be introduced only after the single-slot implementation is accepted.

---

## 7. Retry policy

Define behavior instead of using a generic “retry everything” rule.

### Automatically retry

Only failures that are plausibly transient:

- Worker process died and lease expired.
- Stockfish subprocess terminated unexpectedly.
- Temporary database connection failure after execution but before result persistence, where ownership checks make replay safe.

### Do not automatically retry

- Invalid or zero-move game.
- Missing/unusable Stockfish binary.
- Invalid engine configuration.
- Deterministic parsing or stored-data invariant violation.
- User cancellation.

### Limits

- Default `max_attempts = 2`.
- Use `available_at` for a short retry delay.
- Expose a manual retry endpoint for failed/cancelled jobs.
- Manual retry must still enforce active-job uniqueness and configuration idempotency.

---

## 8. API contract

### Preserve the route, change the execution semantics

Change the existing endpoint from synchronous execution to durable job creation:

```text
POST /api/games/{game_id}/analysis
```

Recommended behavior:

- `202 Accepted`: a new job was queued.
- `200 OK`: a matching completed analysis already exists and is reused.
- `409 Conflict`: a matching job is already queued/running.
- `404`: game not found.
- `422`: game cannot be analyzed.
- `503`: engine configuration is unusable at enqueue time.

Do not keep two public implementations of the same operation. A hidden synchronous test/service path may remain internally, but the product endpoint should become asynchronous.

### Bulk endpoint

```text
POST /api/uploads/{upload_id}/analysis
```

The response should report:

- Batch id.
- Number queued.
- Number skipped because matching analysis is already complete.
- Number already active.
- Number ineligible.
- Configuration summary.

### Read endpoints

```text
GET /api/analysis/jobs/{job_id}
GET /api/analysis/batches/{batch_id}
GET /api/games/{game_id}/analysis
```

Extend the existing game-analysis view to expose:

- `queued`, `running`, `complete`, `failed`, or `cancelled`.
- Job id and optional batch id.
- `completed_positions`, `total_positions`, and percent.
- Latest error and whether retry is allowed.
- Completed analysis data when available.

### Commands

```text
POST /api/analysis/jobs/{job_id}/cancel
POST /api/analysis/jobs/{job_id}/retry
POST /api/analysis/batches/{batch_id}/cancel
```

Use commands rather than permitting arbitrary client-written status updates.

---

## 9. Frontend behavior

### Per-game flow

- “Analyze Game” returns immediately after enqueue.
- Show queued state before the worker claims it.
- Poll at a modest interval, e.g. every 1–2 seconds while active.
- Show position progress when running.
- Keep the board and move navigation usable during analysis.
- Permit navigation away; reopening the game reconstructs state from the server.
- Show cancel while queued/running.
- Show retry only for eligible failed/cancelled jobs.
- On completion, stop polling and render the evaluation graph.

### Whole-upload flow

Add an “Analyze Upload” action to the saved upload dashboard.

Show:

- Games complete / total.
- Current queued/running/failed/cancelled counts.
- Overall position progress if useful.
- Which games were skipped because matching analyses already existed.
- A batch cancel action.
- Links to failed games and retry controls.

### Polling cleanup

- Stop polling for terminal states.
- Stop timers on component unmount.
- Back off slightly after repeated network failures.
- Do not treat a temporary polling failure as an analysis failure.

---

## 10. Checkpointed implementation plan

Each checkpoint ends with tests, migration checks when relevant, and an acceptance-log entry. Do not begin the next checkpoint until the current one passes.

### Checkpoint 0 — Architecture record and contracts

**Build**

- Add an ADR documenting the separate-worker/PostgreSQL-queue decision.
- Write exact job and batch state machines.
- Define retryable error categories.
- Define API response schemas before implementation.
- Record explicit non-goals, especially no Redis and no position-level resume.

**Acceptance**

- No ambiguous status transition remains.
- Every terminal state has a documented meaning.
- The synchronous endpoint migration behavior is decided.
- The architecture explains how it could later migrate to Redis without changing frontend semantics.

### Checkpoint 1 — Schema and repository layer

**Build**

- Add `analysis_batches` and `analysis_jobs`.
- Link `analysis_runs` to jobs/attempts.
- Add constraints, indexes, and active-job uniqueness.
- Add focused repository functions for enqueue, claim, heartbeat, cancel request, complete, fail, retry, and stale-job lookup.
- Keep transaction boundaries explicit.

**Tests**

- Alembic upgrade from current Sprint 4 schema.
- Alembic upgrade from an empty database.
- `alembic check` clean.
- Constraint and cascade tests.
- Repository transition tests scoped to ids created by the test.

**Acceptance**

- Existing Sprint 4 data migrates without loss.
- Invalid transitions are rejected by service logic and important invariants by the database.
- Two active matching jobs cannot exist.

### Checkpoint 2 — Single-slot worker and safe claiming

**Build**

- Add a worker entry point, e.g. `python -m app.worker`.
- Claim with `FOR UPDATE SKIP LOCKED`.
- Execute one queued game using the existing engine service.
- Persist success/failure using short transactions.
- Add a `--once` mode for deterministic tests and CI.
- Add the worker service to Docker Compose.

**Tests**

- Two claimers cannot claim the same job.
- A claimed job holds no long database transaction while Stockfish runs.
- Successful job creates exactly `N+1` evaluations.
- Failed execution stores zero evaluations.
- Real Stockfish worker integration test under the existing engine CI job.

**Acceptance**

- API and worker can be started independently.
- Restarting the API does not affect a queued/running worker job.
- One game completes without an HTTP request remaining open.

### Checkpoint 3 — Heartbeats, stale recovery, cancellation, retry

**Build**

- Add periodic heartbeat/lease renewal.
- Add startup and periodic stale-job sweep.
- Implement cancellation for queued and running jobs.
- Implement retry policy and manual retry.
- Record structured error codes plus safe messages.

**Tests**

- Expired running job is requeued when attempts remain.
- Expired final attempt becomes failed.
- Abandoned `analysis_run` becomes failed.
- Queued cancellation prevents claim.
- Running cancellation stops after the current position boundary and stores no evaluations.
- Manual retry creates a new attempt without mutating old run history.

**Acceptance**

- Killing the worker mid-game no longer leaves an unrecoverable permanent `running` record.
- Reopening the application shows the durable state accurately.

### Checkpoint 4 — Asynchronous single-game API and UI

**Build**

- Convert `POST /api/games/{id}/analysis` to enqueue and return immediately.
- Extend `GET /api/games/{id}/analysis` with queued/running progress and job metadata.
- Add job cancel/retry endpoints.
- Update the game-analysis frontend to poll, cancel, retry, and recover after navigation/reload.

**Tests**

- API response matrix: 202/200/404/409/422/503.
- Matching completed analysis is reused.
- Matching active job is not duplicated.
- Frontend queued, running, progress, cancelled, failed, retry, and complete states.
- Poll timers are cleaned up.

**Acceptance**

- Clicking Analyze Game returns quickly.
- The user can reload or leave and return without losing progress.
- Completion still renders the existing evaluation graph correctly.

### Checkpoint 5 — Bulk upload analysis

**Build**

- Add the upload analysis endpoint.
- Create one batch and one job per eligible game in a single transaction.
- Skip matching completed work.
- Reuse matching active work where practical, or report it explicitly.
- Add batch status and cancel endpoints.
- Add the saved-dashboard bulk-analysis UI.

**Tests**

- Batch creation is all-or-nothing.
- Completed matching games are skipped.
- No unbounded Stockfish processes are created.
- Batch progress derives correctly across mixed job states.
- Batch cancellation affects queued/running jobs correctly.
- Deleting an upload cascades through batch/job/run/evaluation records as intended.

**Acceptance**

- A multi-game upload can be queued with one action.
- Work progresses under the configured concurrency limit.
- Repeating the request does not silently redo completed matching analyses.

### Checkpoint 6 — Configurable bounded concurrency

**Build**

- Add `ANALYSIS_WORKER_CONCURRENCY`.
- Keep the default at one.
- If concurrency greater than one is implemented, use a fixed executor and independent job/session ownership per slot.
- Add clear startup logging of concurrency, Stockfish threads, and hash allocation.

**Tests**

- Maximum simultaneous engine instances never exceeds configuration.
- Jobs are not double claimed under concurrent slots.
- One slot failure does not terminate unrelated active slots.

**Acceptance**

- Submitting 50 games creates 50 durable jobs but only the configured number run at once.

### Checkpoint 7 — Hardening, documentation, and final acceptance

**Build**

- Add worker health/readiness information.
- Add graceful worker shutdown: stop claiming, request active work to finish or cancel under a documented policy, then exit.
- Add operational logs with job id, batch id, game id, attempt, worker id, and transition.
- Update README, architecture diagram, local startup instructions, and environment variables.
- Update `dev.ps1` or provide a parallel worker-start command.
- Add a manual acceptance script/checklist.

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

Also manually prove:

1. Queue one game and receive an immediate response.
2. Restart the API while the worker continues.
3. Kill the worker mid-game.
4. Restart it and observe stale recovery/retry.
5. Cancel a running game and confirm no partial evaluations exist.
6. Queue an entire upload and observe bounded concurrency.
7. Repeat the same bulk request and confirm completed matching games are skipped.
8. Reload the browser during a job and recover progress.
9. Complete a job and verify the evaluation graph is still board-synchronized.

**Acceptance**

- All existing Sprint 1–4 invariants still hold.
- CI is green from a fresh clone and empty database.
- No request stays open for the duration of Stockfish analysis.
- No stale running job is permanently unrecoverable.
- No workload can launch more than the configured number of Stockfish processes.

---

## 11. Invariants Sprint 5 adds

1. A committed job exists before an enqueue response is returned.
2. Only the worker executes Stockfish analysis.
3. A job is owned by at most one unexpired worker lease.
4. Engine execution never holds a long database transaction.
5. Failed or cancelled attempts store zero position evaluations.
6. A completed run is immutable.
7. Progress is monotonic within an attempt.
8. Client requests cannot set job state, engine identity, progress, or results directly.
9. Concurrency is bounded by server configuration.
10. Matching active jobs are not duplicated.
11. Matching completed runs are reused or explicitly skipped unless a future force-reanalysis feature is deliberately added.
12. Bulk resumability is at game granularity; interrupted individual games restart from ply 0.

---

## 12. Risks and controls

### PostgreSQL queue polling load

**Risk:** overly frequent polling creates unnecessary database traffic.  
**Control:** claim one or several jobs per poll, use the indexed status/availability query, and sleep/back off when the queue is empty.

### Worker dies after Stockfish finishes but before commit

**Risk:** the expensive game is recomputed.  
**Control:** accept at-least-once execution with exactly-once committed results enforced by ownership checks and uniqueness. Recompute is preferable to partial/duplicate committed output.

### Stale worker later attempts to commit

**Risk:** a recovered/retried job receives results from the old worker.  
**Control:** require matching `claimed_by` and valid lease/attempt ownership in the final update transaction.

### Database deletion during execution

**Risk:** upload/game is deleted while a worker is analyzing it.  
**Control:** final commit must tolerate the missing/cancelled job and discard in-memory results safely.

### Excessive progress writes

**Risk:** one update per position generates avoidable write volume.  
**Control:** throttle progress updates by time or number of positions while heartbeat remains reliable.

### Too much Sprint 5 scope

**Risk:** queue infrastructure consumes the product roadmap.  
**Control:** retain polling, game-level resume, one queue, one priority, and PostgreSQL only. Do not introduce distributed-system features without a demonstrated need.

---

## 13. Recommended stopping point

Sprint 5 is complete when durable asynchronous single-game and whole-upload analysis work reliably under one configurable worker, including crash recovery, progress, cancellation, retry, idempotency, and bounded concurrency.

Do not extend the sprint into queue dashboards, Redis migration, deployment orchestration, or position-level checkpointing. Move directly to Sprint 6 so the stored evaluations become visible user value: centipawn loss, move-quality labels, phase breakdowns, and game accuracy.
