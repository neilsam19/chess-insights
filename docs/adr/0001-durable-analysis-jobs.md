# ADR 0001 — Durable engine analysis jobs

**Status:** Accepted
**Date:** 2026-07-29
**Sprint:** 5
**Supersedes:** the synchronous analysis path introduced in Sprint 4

This is the Checkpoint 0 deliverable of
`CHESS-INSIGHTS-SPRINT-5-PLAN-REVISED.md`. No checkpoint after this one may
leave a state transition, error classification, or response shape undecided.

---

## 1. Context

Sprint 4 analyses a game synchronously inside the HTTP request. The request
stays open for the entire Stockfish run, and if the backend dies mid-analysis
the `analysis_runs` row is left in `running` forever. That game can then never
be re-analysed through the API, because `analyze_game()` refuses to start when
the latest run is `running`. There is no cancellation, no progress, no bulk
analysis, and nothing bounding how many engine processes could be started.

## 2. Decision

Analysis becomes **durable work executed by a separate worker process, queued
in PostgreSQL**.

- FastAPI validates and writes a committed job, then returns.
- A separate `analysis-worker` process claims jobs using
  `FOR UPDATE SKIP LOCKED`.
- PostgreSQL stays the only infrastructure service.
- Concurrency is a fixed server-side configuration value.
- The React app polls for status.

The full argument for this over in-process background tasks and over
Redis/Celery/RQ/arq is in `CHESS-INSIGHTS-SPRINT-5-PLAN.md` §2 and is not
repeated here. The short version: in-process tasks are tied to the API
lifecycle and so cannot be durable, and Redis would add an always-on service
and a second source of truth to solve throughput problems this project does
not have. PostgreSQL already provides durability, transactions, row locks, and
`SKIP LOCKED`.

The triggers for revisiting Redis are listed in that document and remain
unchanged.

---

## 3. Decisions closing the Sprint 5 findings

### D1 — Claim-contention tests get their own non-transactional fixture

**Finding:** `backend/tests/conftest.py` binds every session to one connection
inside one outer transaction (`join_transaction_mode="create_savepoint"`) and
rolls back at teardown. `FOR UPDATE SKIP LOCKED` only skips rows locked by
*other* transactions, so two claimers built on that fixture share a
transaction, both succeed, and the test passes while proving nothing.

**Decision:** add a second fixture, `committed_db`, used **only** by tests that
must exercise real concurrency.

- Real independent connections, real commits, no outer transaction.
- Teardown deletes by the ids the test created, in dependency order:
  `position_evaluations` → `analysis_runs` → `analysis_jobs` →
  `analysis_batches` → `moves` → `games` → `uploads`.
- Teardown runs in a `finally` so a failing assertion still cleans up.
- Every such test asserts total row counts before and after, so a leak fails
  the test rather than silently polluting the dev database.
- The fixture's docstring states plainly that it is not the default and why.

**Guard:** the CP2 acceptance criterion is that removing `SKIP LOCKED` from the
claim query makes the contention test **fail**. A concurrency test that cannot
fail is worse than no test, and this is verified by hand before committing.

Everything else keeps using the existing rollback fixture. This is a
deliberate exception, not a migration.

*Rationale: test isolation has caused three separate rounds of failures across
Sprints 2–4. Each was loud. This one would be silent.*

### D2 — The worker is a local process, not a container

**Finding:** the repository has no `Dockerfile`; `compose.yaml` defines only
`db`. The backend has always run from the venv via `dev.ps1`.

**Decision:** run the worker as a third local process, started by `dev.ps1`
alongside uvicorn and vite. Compose continues to provide PostgreSQL only. No
Dockerfile in Sprint 5.

**Why this is also the more maintainable choice.** `STOCKFISH_PATH` points at a
Windows executable that a Linux container cannot run, so a containerized worker
would need its own Linux Stockfish build. The binary SHA-256 is an input to
`config_hash` (see D3), so the container and the developer machine would
compute **different config identities for the same logical analysis**. Reuse
and idempotency would then misfire between them. Containerizing now does not
merely add a Dockerfile; it forks engine identity.

**Cost of deferring: none.** The worker is `python -m app.worker` either way.
Containerizing later is a Dockerfile plus a compose entry with zero
application-code change, and it belongs with deployment after Sprint 6, when
everything is Linux consistently.

### D3 — Engine config identity is resolved without starting Stockfish

**Finding:** `build_stockfish_engine()` validates the path, hashes the
executable, and spawns the UCI subprocess in one step. The enqueue endpoint
must return `503` for an unusable engine and must compute `config_hash`, but
must not spawn Stockfish or re-read a ~50 MB binary on every request.

**Decision:** split `backend/app/services/engine.py`:

- `resolve_engine_config() -> EngineConfig` — validates `STOCKFISH_PATH`,
  returns `(binary_sha256, options, depth)`, never spawns a process. Raises
  `EngineUnavailableError` exactly as today.
- `build_stockfish_engine()` — keeps launching, and now consumes the resolved
  config rather than re-deriving it.

The hash is cached on `(path, mtime, size)`, so it is computed once per process
per binary and recomputed automatically if the executable is replaced.

**Canonical config hash, in two layers.** Serialize to JSON with sorted keys
and no whitespace, then SHA-256 the UTF-8 bytes.

*Engine config hash* — identity of a configuration, independent of any game.
Stored on `analysis_batches.engine_config_hash`:

```json
{
  "behavior_version": 1,
  "depth": 12,
  "engine_binary_sha256": "<hex>",
  "options": {"Hash": 64, "Threads": 1}
}
```

*Job config hash* — that configuration applied to one game. Stored on
`analysis_jobs.config_hash`:

```json
{
  "engine_config_hash": "<hex>",
  "game_hash": "<games.game_hash>"
}
```

**Amended during CP1.** This was originally specified as a single hash with
`game_hash` folded in. That does not work: a batch spans many games and so
needs an identity of its own, which a game-specific hash cannot express.
Splitting it also means the second layer is structured JSON rather than string
concatenation, so `("ab", "c")` and `("a", "bc")` cannot collide.

The executable *path* is deliberately not an input — moving the same binary
must not invalidate existing analyses. The digest identifies the build.

`ANALYSIS_BEHAVIOR_VERSION` starts at `1` and is bumped whenever a change would
alter how a stored evaluation should be interpreted. `games.game_hash` already
exists and is used rather than the game id, so identical positions imported
twice share an identity.

A client-supplied hash, depth, engine path, or engine option is never accepted.

### D4 — Idempotency has exactly one authority

**Finding:** `game_analysis_service.analyze_game()` already implements reuse and
conflict rules — it raises `AnalysisAlreadyRunningError` when the latest run is
`running` and returns `created=False` when the latest run is `complete`. Once
jobs exist, the same rules would live in two places over different data.

**Decision:** `analyze_game()` becomes a **pure executor**. Given a game and a
resolved config it produces evaluations or raises. It no longer inspects prior
runs, no longer decides reuse, and no longer raises
`AnalysisAlreadyRunningError`.

All idempotency moves to two places and only these two:

1. **Enqueue** — decides reuse (`200`), conflict (`409`), or queue (`202`).
2. **The partial unique index** on `(game_id, config_hash)` where status is
   `queued` or `running` — the database is the final arbiter, so a race between
   two enqueues cannot create two active jobs.

The three-short-transaction shape documented at the top of
`game_analysis_service.py` is preserved. Only the decision-making is removed.

### D4a — No `latest_run_id` pointer on jobs (amended during CP1)

The original data model put a `latest_run_id` column on `analysis_jobs`. It is
not implemented, for two reasons:

- It creates a circular foreign key (`analysis_runs.analysis_job_id` →
  `analysis_jobs`, and back), which complicates migration ordering for no
  functional gain.
- It is a denormalization that can drift from the run history it summarizes,
  and D4 has just finished removing a *different* second source of truth.

A job's latest attempt is derived from `analysis_runs` ordered by
`(analysis_job_id, attempt_number)`, which
`ix_analysis_runs_job_id_attempt` makes cheap.

### D5 — Runs gain a `cancelled` state

**Finding:** `analysis_run.py` defines only `running`, `complete`, `failed`.

**Decision:** add `STATUS_CANCELLED = "cancelled"`. Plain string, not a database
enum, matching the existing convention used by `user_color`, `user_result`, and
`opening_source`.

A cancelled run holds **zero** position evaluations, exactly like a failed one.
`not_analyzed` remains a derived API state and is still never stored.

---

## 4. Job state machine

States: `queued`, `running`, `complete`, `failed`, `cancelled`.

```
                    ┌─────────────────────────────┐
                    │                             │
  (enqueue) ──▶ queued ──claim──▶ running ──▶ complete
                    │                │
                    │                ├──▶ failed ──┐
                    │                │             │
                    ├──▶ cancelled ◀─┤             │
                    │        │       │             │
                    │        │       └──requeue────┘
                    │        │            (back to queued)
                    └────────┴──manual retry──▶ queued
```

### Permitted transitions

| From | To | Trigger | Enforced by |
|---|---|---|---|
| — | `queued` | enqueue | service + unique index |
| `queued` | `running` | worker claim | `FOR UPDATE SKIP LOCKED` |
| `queued` | `cancelled` | cancel command | service, under `FOR UPDATE` |
| `running` | `complete` | successful persist | service + ownership check |
| `running` | `failed` | non-retryable error, or attempts exhausted | service |
| `running` | `queued` | retryable error with attempts remaining, or expired lease with attempts remaining | service |
| `running` | `cancelled` | worker observes `cancel_requested_at` | service |
| `failed` | `queued` | manual retry | service + unique index |
| `cancelled` | `queued` | manual retry | service + unique index |

### Explicitly not permitted

- `queued → failed`. A job fails only by being attempted. A queued job whose
  game or upload is deleted is removed by cascade, not failed.
- `queued → complete`. Reuse of an existing completed analysis is decided at
  enqueue and produces no job at all.
- `complete → anything`. A completed run is immutable. Re-analysis of an
  already-complete game is deliberately not offered in Sprint 5; adding it
  later means a new job under a *different* `config_hash`, or an explicit
  force-reanalyse feature, never mutation.
- `cancelled → running` and `failed → running` directly. Retry always passes
  through `queued`, so a retried job is claimed by the normal protocol and
  cannot skip the uniqueness check.

### The run is created after the claim, not inside it (amended during CP2)

The original claim protocol had the claim transaction also create the linked
`analysis_run`. That is not implementable: `analysis_runs.engine_name` is
`NOT NULL`, and the name only exists after `engine.identify()`, which requires
a live Stockfish process. Creating the run inside the claim would mean starting
the engine while holding the claim transaction open — precisely what invariant
4 forbids.

So the order is:

1. **Claim transaction** — take the job, set the lease. Commit.
2. Start the engine (no transaction held).
3. **Run transaction** — create the `analysis_run` from the real engine
   identity. Commit.
4. Analyse (no transaction held).

A job that is `running` with no run yet means the engine is still starting.
A job whose engine never starts produces no run at all, which matches the
Sprint 4 rule that a misconfigured server is a `503`, not a failed analysis.
The stale sweep handles both cases: it fails the job, and the run too if one
exists.

### Manual retry is stricter than the transition table

`ALLOWED_TRANSITIONS` permits `running → queued`, but that edge belongs to the
stale sweep and automatic requeue, which act on jobs whose lease they own.
`retry_job` additionally requires the job to be `failed` or `cancelled`.

A user asking to retry a job that is running right now is asking for the wrong
verb — they want cancel. Allowing it would silently abandon a live worker's
run while that worker kept analysing.

### `cancel_requested_at` is a flag, not a state

Cancellation takes `FOR UPDATE` on the job row and then:

- **queued** → set `status = cancelled` and `cancel_requested_at` immediately.
  Taking the row lock is what prevents a concurrent claim from racing it.
- **running** → set `cancel_requested_at` only. The worker observes it at the
  next position boundary and performs the transition itself.
- **terminal** → no-op, reported as already finished.

### Terminal states and their meanings

| State | Meaning | Evaluations | Retryable | UI treatment |
|---|---|---|---|---|
| `complete` | The whole game was analysed and persisted | exactly `N+1` | no | Evaluation graph, as Sprint 4 renders today |
| `failed` | An attempt failed and no attempts remain, or the error is non-retryable | **zero** | yes, manually | Error message + Retry button |
| `cancelled` | A user stopped it | **zero** | yes, manually | "Cancelled" + Analyze Game button |

`complete` is the only state that is truly final. `failed` and `cancelled` are
re-openable by explicit user action, which is why manual retry passes back
through `queued`.

---

## 5. Batch state derivation

Batch state is **always derived from child jobs**, never stored as a mutable
counter. One service function is the only authority.

While any job is non-terminal:

1. any `running`, or a mix of `queued` and terminal jobs → **`running`**
2. every job `queued` → **`queued`**

Once every job is terminal, first match wins:

3. any `failed` **and** any `complete` → **`partial_failed`**
4. any `failed` → **`failed`**
5. any `cancelled` → **`cancelled`**
6. otherwise → **`complete`**

This is total: rules 3–6 cover every combination of terminal jobs, and rule 6
is reached only when all jobs are `complete`.

**A batch with zero jobs is `complete` immediately**, with `completed_at` set at
creation. This is reachable and normal: it is what a bulk request returns when
every game already has a matching completed analysis. Games skipped that way
are reported in the creation response as counts; they never become jobs, which
is why rule 6 does not need a "skipped" case.

---

## 6. Error codes

Stored as `analysis_jobs.error_code`, alongside the sanitized
`error_message` whose behaviour is unchanged from Sprint 4 — no filesystem
paths, no tracebacks, no environment values, truncated to 500 characters.

### Retryable — retried automatically while attempts remain

| Code | Cause |
|---|---|
| `worker_lease_expired` | The worker died; the stale sweep reclaimed the job |
| `engine_crashed` | The Stockfish subprocess terminated unexpectedly mid-run |
| `database_unavailable` | A transient database failure during persistence |

These are plausibly transient and safe to replay, because a failed attempt
stores zero evaluations and the ownership check prevents a stale worker from
committing late.

### Non-retryable — recorded as `failed` on the first occurrence

| Code | Cause |
|---|---|
| `invalid_game_data` | Zero moves, mismatched move count, or non-contiguous plies |
| `invalid_position` | A stored FEN the engine refused |
| `engine_unavailable` | The configured binary is missing or unusable |
| `engine_misconfigured` | The engine rejected the configured options |
| `ambiguous_score` | The engine returned neither centipawns nor mate |
| `evaluation_count_mismatch` | Produced a number of evaluations other than `N+1` |
| `cancelled_by_user` | Not a failure; recorded on the cancelled run for provenance |

Retrying any of these would deterministically reproduce the same result, so
automatic retry would only waste engine time.

### Implementation note for CP5

`EngineFailureError` currently covers three of these at once —
`engine_crashed`, `invalid_position`, and `ambiguous_score` are all raised as
the same class from `engine.py`. CP5 must give it a `code` attribute or split
it into subclasses. Classifying on message text is not acceptable.

`EngineUnavailableError` already maps cleanly to `engine_unavailable`, and its
existing meaning is preserved: encountered at enqueue it is a `503` with **no
job created**, because a misconfigured server is not a failed analysis.

### Limits

- `max_attempts` defaults to `2`.
- Retry delay is applied via `available_at`.
- Manual retry is available for `failed` and `cancelled` jobs, and still
  enforces active-job uniqueness and config idempotency.

---

## 7. API contract

Decided here, implemented in CP6 and CP8.

### Analysis state — the discriminated union

`GET /api/games/{game_id}/analysis` keeps its Pydantic
`Field(discriminator="status")` union and its TypeScript tagged-union mirror.
Two variants are added and two are extended. `not_analyzed` and `complete` are
unchanged, so a completed analysis still renders exactly as it does today.

```python
class QueuedAnalysisOut(BaseModel):
    status: Literal["queued"] = "queued"
    game_id: uuid.UUID
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    queued_at: datetime
    total_positions: int
    completed_positions: int = 0
    percent: int = 0
    attempt: int
    max_attempts: int
    can_cancel: bool = True
    can_retry: bool = False


class RunningAnalysisOut(BaseModel):          # extended
    status: Literal["running"] = "running"
    game_id: uuid.UUID
    run_id: uuid.UUID
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    started_at: datetime
    total_positions: int
    completed_positions: int
    percent: int
    attempt: int
    max_attempts: int
    can_cancel: bool = True
    can_retry: bool = False


class FailedAnalysisOut(BaseModel):           # extended
    status: Literal["failed"] = "failed"
    game_id: uuid.UUID
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    can_cancel: bool = False
    can_retry: bool


class CancelledAnalysisOut(BaseModel):        # new
    status: Literal["cancelled"] = "cancelled"
    game_id: uuid.UUID
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    cancelled_at: datetime
    can_cancel: bool = False
    can_retry: bool = True
```

`run_id` is **nullable on queued, failed, and cancelled**, because the run is
created at claim time. A job cancelled while still queued never had a run, and
that is correct rather than an omission.

`percent` is computed server-side from `completed_positions / total_positions`
so every client shows the same number.

### Status matrix — `POST /api/games/{game_id}/analysis`

| Code | Condition | Body |
|---|---|---|
| `202` | A new job was queued | `QueuedAnalysisOut` |
| `200` | A matching completed analysis exists and is reused | `CompleteAnalysisOut` |
| `409` | A matching job is already `queued` or `running` | current state, with `job_id` |
| `404` | Game not found | detail |
| `422` | Game cannot be analysed (zero moves, invalid stored data) | detail |
| `503` | Engine unusable at enqueue; **no job created** | detail |

This replaces Sprint 4's `201`/`200`. The change is deliberate and is the
sprint's one breaking contract change: `202` now means "accepted, not done",
which is the honest description of what happens.

### Bulk

```
POST /api/uploads/{upload_id}/analysis   -> BatchCreatedOut  (202, or 200 if nothing to do)
GET  /api/analysis/batches/{batch_id}    -> BatchStatusOut
POST /api/analysis/batches/{batch_id}/cancel
GET  /api/analysis/jobs/{job_id}         -> JobStatusOut
POST /api/analysis/jobs/{job_id}/cancel
POST /api/analysis/jobs/{job_id}/retry
```

`BatchCreatedOut` reports `batch_id`, `queued`, `skipped_complete`,
`already_active`, `ineligible`, and a config summary (`requested_depth`,
`engine_name`, `engine_binary_sha256`). `BatchStatusOut` reports the derived
status, per-status job counts, aggregate position progress, timestamps, and
`failed_game_ids` so the UI can link to them.

### Commands, not state writes

State changes happen only through the cancel and retry command endpoints. There
is no endpoint that accepts a status, progress value, engine identity, or
evaluation from a client. This is invariant 8 and it is absolute.

---

## 8. How this migrates to Redis later without touching the frontend

The frontend only ever sees `status`, `job_id`, `batch_id`, progress counts,
and the command endpoints. It has no knowledge of how a job is stored or
claimed. A future migration would replace the *claim mechanism*
(`FOR UPDATE SKIP LOCKED` against `analysis_jobs`) with a broker, while:

- `analysis_batches`, `analysis_jobs`, `analysis_runs`, and
  `position_evaluations` remain the system of record;
- the state machines in §4 and §5 remain unchanged;
- the API contract in §7 remains unchanged;
- the frontend requires no change at all.

The queue is an implementation detail behind the repository layer. That is the
main reason for keeping job state in PostgreSQL rather than letting a broker
own it: the domain model does not have to move if the transport does.

---

## 9. Non-goals for Sprint 5

- Redis, Celery, RQ, or arq.
- WebSockets or server-sent events. Polling is sufficient.
- Containerizing the backend or worker (D2).
- Position-level persistence or resume inside a partially analysed game.
  Resumability is **game-level**: a running game whose worker dies restarts
  from ply 0. This preserves the invariant that failed and cancelled runs hold
  zero evaluations, which Sprint 6 depends on.
- Force re-analysis of an already-complete game.
- Multiple priority queues, multi-user fairness, quotas, autoscaling, or
  scheduled analysis.
- Sprint 6 move-quality labels, centipawn loss, or accuracy.

---

## 10. Consequences

**Gained:** analysis survives an API restart; a killed worker no longer leaves a
permanently unrecoverable `running` record; a whole upload can be queued in one
action; users can cancel and retry; no workload can start more Stockfish
processes than configured.

**Cost:** a second process to run and reason about, a polling frontend, and
two test-isolation strategies instead of one (D1).

**Accepted trade-off:** execution is at-least-once with exactly-once *committed*
results. If a worker dies after Stockfish finishes but before the commit, the
game is recomputed. Recomputation is strictly preferable to partial or
duplicated committed output, and the ownership check in the final transaction
is what guarantees the latter cannot happen.
