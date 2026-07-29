# Chess Insights — Sprint 4 Implementation Plan

**Purpose:** Provide Claude Code with a staged, verifiable implementation plan for Sprint 4.

**Source of truth:** `PROJECT-STATUS.md`, last updated 2026-07-27.

**Current repository state:** Sprints 1, 2, and 3 are complete, accepted, committed, and pushed. The repository is clean, and all current backend/frontend checks pass.

---

## 1. Sprint 4 objective

Sprint 4 should prove **end-to-end Stockfish analysis for one saved game**.

The completed flow should be:

```text
open saved upload
→ select one saved game
→ request Stockfish analysis
→ persist one evaluation per position
→ record engine provenance
→ distinguish not analyzed / running / complete / failed
→ display an evaluation graph
```

Sprint 4 must remain intentionally narrow.

It should **not** add:

- Whole-upload analysis
- Queues or workers
- Resumability
- Cancellation
- Bounded concurrency
- Move-quality labels
- Accuracy calculations
- LLM explanations
- Authentication
- Deployment

Those belong to later sprints.

---

## 2. Existing invariants that must not break

The implementation must preserve all current load-bearing invariants.

1. Aggregate statistics are derived, never stored.
2. Preview writes zero database rows.
3. Upload import remains one all-or-nothing transaction.
4. Opening classification remains server-side only.
5. Opening EPD normalization remains shared between dataset generation and runtime classification.
6. The opening dataset remains vendored and requires no runtime network access.
7. Move arrays remain excluded from list responses.
8. Duplicate uploads remain keyed by `(file_sha256, normalized_player_name)`.
9. A preview game must never become analyzable before it is saved.
10. Existing upload, game, move, and opening behavior must remain unchanged.

Engine analysis introduces a different transaction boundary from upload import:

- Upload import must remain one atomic transaction.
- Engine analysis must not hold a database transaction open while Stockfish is thinking.

---

## 3. Decisions to make before implementation

Use the following design unless the existing code reveals a strong reason to change it.

### 3.1 Analyze one saved game at a time

- Analysis is triggered only from a persisted game.
- No analysis is performed during preview.
- No analysis is performed automatically during upload.
- No bulk-analysis endpoint is added.
- Sprint 4 may wait synchronously for one game to finish.
- Analysis state must still be persisted explicitly.

The boundary between Sprint 4 and Sprint 5 is:

```text
Sprint 4: prove one-game engine analysis
Sprint 5: make engine work persistent, queued, resumable, and scalable
```

### 3.2 Use a separate analysis-run model

Do not add engine evaluation fields directly to `moves`.

A game may later be analyzed multiple times with different:

- Stockfish versions
- Binary builds
- Depths
- Node limits
- Thread counts
- Hash sizes
- Engine options

Use:

```text
Game
  → AnalysisRun
      → PositionEvaluation
```

The run stores provenance and lifecycle state.

The evaluation rows store raw position-level output.

### 3.3 Store scores from White's perspective

Persist a canonical representation:

- Positive centipawns: White is better.
- Negative centipawns: Black is better.
- Positive mate value: White has mate in N.
- Negative mate value: Black has mate in N.

Convert to the selected user's perspective in the API/service response.

Do not permanently store frontend-specific or player-specific graph values.

### 3.4 Analyze positions from ply 0 through ply N

For a game with `N` half-moves, save `N + 1` evaluation points.

```text
ply 0 → starting position
ply 1 → position after White's first move
ply 2 → position after Black's first move
...
ply N → final game position
```

This is preferable to storing only one score per move because Sprint 6 will need the evaluation immediately before and immediately after every move.

Without the starting position, the first move cannot receive a centipawn-loss calculation.

### 3.5 Use explicit Stockfish configuration

Prefer a configured path:

```text
STOCKFISH_PATH
```

Do not:

- Vendor a Windows Stockfish binary during Sprint 4.
- Depend only on Stockfish being globally available on `PATH`.
- Make the ordinary automated test suite require the real binary.

The machine has TLS interception issues, so downloading Stockfish automatically from GitHub during runtime or tests is not reliable.

Document manual setup.

### 3.6 Use a fixed analysis depth

Use a fixed configurable depth initially, for example:

```text
STOCKFISH_DEPTH=12
```

Also configure:

```text
STOCKFISH_THREADS
STOCKFISH_HASH_MB
```

Do not expose arbitrary engine configuration in the frontend during Sprint 4.

Persist the requested engine settings for provenance.

---

## 4. Targeted pre-implementation recheck

Do not re-audit all of Sprints 1–3.

Inspect only the code paths relevant to the new feature.

### 4.1 Backend recheck

Confirm the following before writing migrations or services.

#### API execution model

- Are route functions `def` or `async def`?
- If routes are asynchronous, ensure blocking Stockfish calls are not run directly on the event loop.
- Determine the project's existing pattern for moving blocking work to a worker thread, if any.

#### Session lifecycle

- Where does the request-scoped SQLAlchemy session commit?
- How are rollbacks handled?
- Are repositories expected to flush but never commit?
- How should a long-running non-database operation release the transaction?

#### Move representation

Confirm:

- `Move.ply` starts at 1.
- Moves are contiguous.
- The first move's `fen_before` is the initial position.
- Every move has a valid `fen_after`.
- `GET /api/games/{id}` returns moves ordered by ply.
- `Game.move_count` agrees with the number of stored moves.

#### Player perspective

Confirm:

- `Game.user_color` is populated for all persisted games.
- Its exact representation is known and stable.
- Saved games can always determine whether to invert White-perspective scores.

#### Existing architecture

Inspect:

- Current service/repository boundaries.
- Schema naming conventions.
- Enum handling conventions.
- Migration naming and timestamp conventions.
- Cascade behavior in current models.
- Existing API error response style.
- `tests/sample_data.py` and its unique-file-hash strategy.

New tests must not assume the database is empty.

### 4.2 Frontend recheck

Confirm:

- Where selected-game state lives.
- Whether saved-game moves are fetched only after selection.
- Whether `SavedDashboardPage` owns selected-game fetching.
- How `ChessGameViewer` receives the current ply.
- Whether the board can already jump to a specified ply.
- How loading and error states are rendered.
- Whether preview and saved views reuse the same board/viewer components.
- Whether a saved game has a clear persistent game ID available in the UI.

The Analyze button must appear only for persisted games.

---

## 5. Proposed persistence model

## 5.1 `analysis_runs`

Suggested fields:

```text
id                     UUID primary key
game_id                UUID FK → games ON DELETE CASCADE
status                 running | complete | failed
engine_name            text
engine_version         text
engine_binary_sha256   text nullable
requested_depth        integer
engine_options         jsonb
created_at             timestamp
started_at             timestamp
completed_at           timestamp nullable
error_message          text nullable
```

Suggested indexes:

```text
INDEX (game_id, created_at)
INDEX (game_id, status)
```

Possible additional fields, if easy to retrieve reliably:

```text
requested_nodes        bigint nullable
requested_time_ms      integer nullable
```

Do not add configuration fields that Sprint 4 does not actually use.

### Status meaning

```text
running
```

A run record exists and analysis has started but has not been marked complete or failed.

```text
complete
```

All expected position evaluations were persisted successfully.

```text
failed
```

Analysis did not complete. A sanitized error message is available.

`not_analyzed` should normally be a derived API state representing the absence of an analysis run.

### Engine identity

Store as much real provenance as can be obtained without excessive complexity:

- UCI engine name
- Parsed/reported version if available
- Configured analysis depth
- Engine options
- Optional SHA-256 hash of the executable

The executable hash is valuable because reported UCI names are not always sufficient to identify an exact build.

## 5.2 `position_evaluations`

Suggested fields:

```text
id                  bigint primary key
analysis_run_id     UUID FK → analysis_runs ON DELETE CASCADE
ply                 integer
score_cp_white      integer nullable
mate_in_white       integer nullable
best_move_uci       text nullable
depth_reached       integer nullable
nodes               bigint nullable
time_ms             integer nullable
```

Constraints:

```text
UNIQUE (analysis_run_id, ply)
INDEX (analysis_run_id, ply)
```

Add a check constraint if it fits the existing migration style:

```text
exactly one of score_cp_white or mate_in_white is non-null
```

If Stockfish or `python-chess` can occasionally produce neither value, decide explicitly whether to:

- Reject the result as an analysis failure, or
- Allow both fields to be null and represent an unavailable score.

Prefer rejecting malformed analysis output unless there is a known valid engine state that lacks both score types.

### Why store `best_move_uci` now?

`best_move_uci` is raw engine evidence, not a move-quality classification.

Sprint 6 will need it for:

- Best-move comparison
- Move-quality labels
- Possible best-line explanations

Capturing it now avoids re-running every position later solely to retrieve the engine's preferred move.

### Do not store yet

Do not persist:

- Centipawn loss
- Accuracy
- Blunder/inaccuracy/mistake labels
- Phase labels
- Graph-clipped values
- Mate converted to a fake centipawn number
- Game-level engine aggregates
- Natural-language explanations

These should be derived in later sprints.

---

## 6. Checkpoint 1 — Stockfish configuration and adapter

Create backend settings for:

```text
STOCKFISH_PATH
STOCKFISH_DEPTH
STOCKFISH_THREADS
STOCKFISH_HASH_MB
```

Use the project's existing settings mechanism.

Create a narrow engine abstraction so most tests do not need the Stockfish executable.

Example interface:

```python
from typing import Protocol


class ChessEngine(Protocol):
    def identify(self) -> "EngineIdentity":
        ...

    def evaluate(self, fen: str, depth: int) -> "RawEvaluation":
        ...

    def close(self) -> None:
        ...
```

Potential value types:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineIdentity:
    name: str
    version: str | None
    binary_sha256: str | None
    options: dict[str, object]


@dataclass(frozen=True)
class RawEvaluation:
    score_cp_white: int | None
    mate_in_white: int | None
    best_move_uci: str | None
    depth_reached: int | None
    nodes: int | None
    time_ms: int | None
```

Implement a Stockfish adapter using `python-chess` UCI engine support.

### Adapter requirements

- Start one Stockfish process per game analysis.
- Do not start one process per position.
- Configure Threads and Hash explicitly.
- Analyze each FEN at the configured depth.
- Normalize every score to White's perspective.
- Preserve centipawn and mate scores as different types.
- Retrieve the engine's best move when available.
- Shut down the engine in `finally`.
- Convert startup failures into a clear domain/application error.
- Convert unexpected engine termination into a clear analysis failure.
- Keep all `python-chess` score-object interpretation inside the adapter.

### Score-normalization tests

Use a fake engine or direct adapter-unit helpers to prove:

- White advantage produces positive CP.
- Black advantage produces negative CP.
- White mate produces positive mate.
- Black mate produces negative mate.
- CP and mate are never populated simultaneously.
- Perspective conversion for Black is correct.
- Engine cleanup occurs after success.
- Engine cleanup occurs after failure.
- Failure midway through a game is propagated.

### Checkpoint 1 acceptance

The code can analyze a deterministic in-memory sequence of FENs using a fake engine without:

- Accessing PostgreSQL
- Starting the real Stockfish executable
- Calling the API
- Touching the frontend

---

## 7. Checkpoint 2 — Migration, models, and repositories

Add:

```text
analysis_runs
position_evaluations
```

Follow existing SQLAlchemy and Alembic conventions.

### Migration requirements

Prove all of the following:

1. `alembic upgrade head` succeeds against a fresh empty database.
2. The migration succeeds against the populated development database.
3. Existing uploads, games, moves, and openings remain unchanged.
4. Deleting a game deletes its analysis runs.
5. Deleting an analysis run deletes its position evaluations.
6. Deleting an upload cascades through:
   - games
   - moves
   - analysis runs
   - position evaluations
7. Duplicate `(analysis_run_id, ply)` values are rejected.
8. Valid CP rows can be stored.
9. Valid mate rows can be stored.
10. Invalid score-shape rows are rejected if a check constraint is used.
11. `alembic check` reports no ungenerated model changes.

### Repository functions

Prefer focused query functions rather than a generic repository framework.

Likely repository operations:

```text
create_analysis_run
get_analysis_run
get_latest_analysis_run_for_game
list_position_evaluations_for_run
save_position_evaluations
mark_analysis_run_complete
mark_analysis_run_failed
```

Keep commit ownership at the service or request boundary according to existing architecture.

### Checkpoint 2 acceptance

Repository tests can:

1. Create a saved game.
2. Create a running analysis run.
3. Save exactly `move_count + 1` ordered evaluations.
4. Mark the run complete.
5. Reload the run and all evaluations.
6. Verify ordering.
7. Verify cascade deletion.

Tests must scope assertions to IDs created by the test and must not assume a globally empty database.

---

## 8. Checkpoint 3 — Single-game analysis coordinator

Create a dedicated service responsible for one complete analysis attempt.

Possible name:

```text
game_analysis_service.py
```

### Required sequence

1. Load the game.
2. Load moves ordered by ply.
3. Validate game and move consistency.
4. Build the ordered position list for plies `0..N`.
5. Create an `analysis_run` with `status="running"`.
6. Commit the short transaction that creates the run.
7. Release the database transaction.
8. Start Stockfish.
9. Analyze every position.
10. Close Stockfish.
11. Save all evaluation rows and mark the run complete in one short transaction.
12. If anything fails, mark the run failed in a separate short transaction.

### Position construction

Use stored FENs rather than reparsing `raw_pgn` unless an inconsistency is detected.

For a non-empty game:

```text
ply 0 → moves[0].fen_before
ply 1 → moves[0].fen_after
ply 2 → moves[1].fen_after
...
ply N → moves[N - 1].fen_after
```

For a zero-move game:

- Determine whether a legal initial-position evaluation should be supported.
- If supported, analyze the standard starting position as ply 0.
- If unsupported, return a deliberate validation error.
- Document and test the chosen behavior.

Supporting ply 0 for a zero-move game is reasonable and consistent.

### Consistency checks

Before running Stockfish, verify:

- Moves are ordered.
- Ply values are contiguous.
- The first ply is 1.
- Number of moves matches `game.move_count`.
- Every required FEN is present.
- Every FEN parses successfully.
- Adjacent stored FENs are logically consistent where practical.
- Expected evaluation count is `move_count + 1`.

Do not silently analyze partial or malformed move sequences.

### Persistence semantics

A completed run must satisfy:

```text
position_evaluation_count == game.move_count + 1
```

No partial evaluation set may be marked complete.

A failed run may have:

- No evaluation rows, preferably, or
- Partial rows only if intentionally retained for debugging.

For Sprint 4, prefer accumulating evaluations in memory and persisting them only after the full game succeeds. This keeps failed runs simple and prevents partial output from being mistaken for usable analysis.

### Retry behavior

A retry should create a new analysis run rather than overwriting an old failed run.

The latest run can determine current UI state.

Do not delete failed runs automatically unless there is a clear existing retention convention.

### Duplicate running requests

Sprint 4 should prevent two simultaneous analyses of the same game where practical.

A minimal rule:

- If a latest run is `running`, return a conflict.
- If a latest run is `complete`, decide whether POST returns the existing result or creates a new run.
- Prefer returning the existing complete run unless explicit reanalysis is supported.
- If a latest run is `failed`, allow a new run.

True concurrency control and stale-running recovery belong to Sprint 5, but obvious duplicate requests should not launch multiple engines.

### Checkpoint 3 tests

Use a deterministic fake engine.

Test:

- `running → complete`
- `running → failed`
- Exactly `N + 1` evaluations
- Correct ply ordering
- Correct CP persistence
- Correct mate persistence
- Correct best-move persistence
- Failure on malformed FEN
- Failure on noncontiguous plies
- Failure on move-count mismatch
- No partial evaluation rows marked complete
- Retry after failure creates a new run
- Duplicate request while running is rejected
- Engine closes after success
- Engine closes after failure

### Checkpoint 3 acceptance

One service call can analyze one saved game and produce a complete persisted run using a fake engine.

---

## 9. Checkpoint 4 — API

Suggested endpoints:

```text
GET  /api/games/{game_id}/analysis
POST /api/games/{game_id}/analysis
GET  /api/engine/status
```

Follow existing route naming and response conventions if they differ.

## 9.1 `GET /api/games/{game_id}/analysis`

Return one of four clear states:

```text
not_analyzed
running
complete
failed
```

### Not analyzed

Example shape:

```json
{
  "status": "not_analyzed",
  "game_id": "..."
}
```

### Running

Example shape:

```json
{
  "status": "running",
  "game_id": "...",
  "run_id": "...",
  "started_at": "..."
}
```

### Failed

Example shape:

```json
{
  "status": "failed",
  "game_id": "...",
  "run_id": "...",
  "started_at": "...",
  "completed_at": "...",
  "error_message": "Stockfish exited unexpectedly."
}
```

Do not expose raw stack traces, local secrets, or full filesystem details.

### Complete

Example shape:

```json
{
  "status": "complete",
  "game_id": "...",
  "run_id": "...",
  "engine": {
    "name": "Stockfish",
    "version": "17",
    "binary_sha256": "...",
    "requested_depth": 12,
    "options": {
      "Threads": 1,
      "Hash": 64
    }
  },
  "started_at": "...",
  "completed_at": "...",
  "perspective": "white",
  "evaluations": [
    {
      "ply": 0,
      "score": {
        "type": "cp",
        "value": 20
      },
      "white_score": {
        "type": "cp",
        "value": 20
      },
      "best_move_uci": "e2e4",
      "depth_reached": 12,
      "nodes": 12345,
      "time_ms": 41
    }
  ]
}
```

The exact shape may be simplified, but preserve type safety between centipawn and mate scores.

Recommended score schema:

```json
{
  "type": "cp",
  "value": 74
}
```

or:

```json
{
  "type": "mate",
  "value": 3
}
```

Do not serialize mate as a fake value such as `100000`.

### Player perspective

The API should clearly document whether `score` is:

- White perspective, or
- Saved player's perspective.

Recommended:

- Persist White perspective.
- Return a player-perspective `score`.
- Optionally also return canonical `white_score` for debugging/future clients.

When the saved player is Black:

```text
player CP = -white CP
player mate = -white mate
```

## 9.2 `POST /api/games/{game_id}/analysis`

Requirements:

- Accept only the game ID from the URL.
- Do not accept client-supplied FENs.
- Do not accept client-supplied moves.
- Do not accept client-supplied evaluations.
- Do not accept client-supplied engine identity.
- Do not accept arbitrary depth during Sprint 4.
- Use server configuration.

Expected outcomes:

```text
200 or 201 → analysis completed
404        → game not found
409        → analysis already running or already complete, depending on policy
503        → Stockfish unavailable or misconfigured
500        → unexpected analysis failure, with run persisted as failed
```

The exact successful status code should follow the current API's conventions.

### Synchronous behavior

For Sprint 4, the POST may remain open until the one-game analysis completes.

The frontend should show an analyzing state while awaiting the response.

This is acceptable only for the one-game proof.

Sprint 5 will replace this with persistent background-job behavior.

## 9.3 `GET /api/engine/status`

This endpoint is optional but useful.

It can report whether Stockfish configuration appears usable.

Example:

```json
{
  "available": true,
  "engine_name": "Stockfish",
  "engine_version": "17",
  "configured_depth": 12
}
```

Do not start a long analysis.

A lightweight startup/identity check is sufficient.

If this endpoint adds unnecessary scope, document engine setup through the analysis endpoint's `503` error instead.

### API regression requirements

- Existing upload list responses remain unchanged.
- Existing game list responses remain unchanged.
- Move arrays remain excluded from list endpoints.
- Evaluation arrays appear only in the single-game analysis endpoint.
- Preview routes remain stateless.
- Client-supplied opening data remains ignored.
- Existing game detail behavior remains unchanged unless deliberately extended.

### Checkpoint 4 acceptance

A real API request can:

1. Select one saved game.
2. Start analysis.
3. Persist a run.
4. Return complete CP/mate-aware evaluation data.
5. Return the same persisted data through GET after restart.

---

## 10. Checkpoint 5 — Frontend analysis states and graph

Add an analysis section to the saved-game viewer.

Do not add it to stateless preview games.

## 10.1 UI states

### Not analyzed

Show:

- `Analyze Game` button
- Optional short note explaining that Stockfish analysis may take some time

### Analyzing

Show:

- Disabled button
- Indeterminate loading indicator
- Clear text such as `Analyzing game...`

Do not display fake percentage progress during Sprint 4.

### Complete

Show:

- Evaluation graph
- Engine name/version
- Analysis depth
- Completion timestamp if useful
- Current-position evaluation
- CP or mate label

### Failed

Show:

- Readable error
- `Retry Analysis` button
- No stack trace
- No misleading partial graph

## 10.2 Evaluation graph behavior

The graph should:

- Use the saved player's perspective.
- Use ply or move progression on the x-axis.
- Use pawn advantage on the y-axis for centipawn values.
- Include a visible zero line.
- Stay synchronized with the current board ply.
- Render mate scores distinctly.
- Avoid treating mate as ordinary centipawn magnitude.
- Preserve real values in data even if the visual y-axis is clipped.
- Be usable on normal desktop widths.
- Handle short and long games.

For CP display:

```text
100 centipawns = 1.00 pawn
```

Suggested labels:

```text
+0.74
-1.32
M3
-M2
```

The sign should remain from the saved player's perspective.

### Mate rendering

For graph coordinates only, map mate values to a fixed display band beyond normal centipawn values.

Example visual-only policy:

```text
mate for player     → top graph band
mate against player → bottom graph band
```

Tooltips and labels must still display:

```text
Mate in 3
Mated in 2
```

Do not persist the graph-mapped value.

## 10.3 Graph implementation choice

A small custom SVG graph is sufficient.

Advantages:

- Avoids adding a charting dependency.
- Makes click-to-ply alignment straightforward.
- Keeps bundle size and dependency surface small.

A library is acceptable only if the existing frontend already uses one or the implementation would otherwise become disproportionately complex.

## 10.4 Board synchronization

Required:

- Current graph point or marker reflects the board's current ply.

Optional after core acceptance:

- Clicking a graph point jumps the board to that ply.
- Hovering shows move number, SAN, and evaluation.

Do not let optional interaction delay the core Sprint 4 acceptance.

## 10.5 Frontend API layer

Create a focused API wrapper for game analysis.

Likely functions:

```text
getGameAnalysis(gameId)
analyzeGame(gameId)
```

Use discriminated TypeScript unions for statuses and score types.

Example:

```ts
type EvaluationScore =
  | { type: "cp"; value: number }
  | { type: "mate"; value: number };

type GameAnalysis =
  | { status: "not_analyzed"; gameId: string }
  | { status: "running"; gameId: string; runId: string; startedAt: string }
  | {
      status: "failed";
      gameId: string;
      runId: string;
      errorMessage: string;
    }
  | {
      status: "complete";
      gameId: string;
      runId: string;
      perspective: "white" | "black";
      evaluations: PositionEvaluation[];
      engine: EngineMetadata;
    };
```

## 10.6 Frontend tests

Add Vitest coverage for:

- Analyze button appears for a persisted saved game.
- Analyze button does not appear for a preview game.
- Button is disabled while analysis is running.
- Complete response renders the graph.
- CP values render correctly.
- Mate values render differently from CP.
- Failed analysis displays retry.
- Clicking retry issues a new request.
- Existing lazy move loading still works.
- Board/graph current-ply marker stays aligned.
- Black-player scores are not inverted again by the frontend if the backend already returns player perspective.

### Checkpoint 5 acceptance

A user can analyze one saved game from the UI and see a persisted, perspective-correct evaluation graph with explicit status handling.

---

## 11. Checkpoint 6 — Real Stockfish integration and CI

## 11.1 Opt-in real-engine integration test

Add an integration marker such as:

```text
stockfish_integration
```

The test should:

- Skip when `STOCKFISH_PATH` is unset.
- Use a very short legal game.
- Use a low test depth.
- Start the real Stockfish process.
- Persist a complete run.
- Verify valid CP or mate output.
- Verify exactly `move_count + 1` evaluations.
- Verify engine identity is recorded.
- Avoid asserting exact centipawn numbers.
- Avoid asserting a specific best move unless the test pins an exact binary and configuration.

Exact evaluations may vary across Stockfish versions, platforms, and builds.

## 11.2 CI recommendation

Sprint 4 is a good point to add CI because the project now includes:

- PostgreSQL migrations
- Backend tests
- Frontend tests
- A binary dependency
- Longer-running analysis code

Recommended CI jobs:

### Backend

- Start PostgreSQL service.
- Install Python dependencies.
- Run `alembic upgrade head` against an empty database.
- Run backend tests.
- Run `alembic check`.

### Frontend

- Install npm dependencies.
- Run tests.
- Run lint.
- Run build.

### Optional engine integration

- Install Stockfish in a Linux CI job.
- Set `STOCKFISH_PATH`.
- Run only the marked engine integration test.

The ordinary backend suite must still pass without Stockfish installed by using fake engine implementations.

## 11.3 Manual Windows verification

Document exact local commands using the current environment:

```powershell
docker compose up -d

cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m alembic check

cd ..\frontend
npm test
npm run build
npm run lint
```

Also document:

- Where the Stockfish executable is installed.
- How `STOCKFISH_PATH` is set.
- How to verify the engine-status endpoint or one-game analysis.
- That PostgreSQL is on port 5433.
- That the backend is on port 8001.
- That Vite is on port 5173.

---

## 12. Error-handling requirements

Handle the following cases explicitly.

### Configuration errors

- `STOCKFISH_PATH` missing
- File does not exist
- File is not executable
- Wrong binary
- Engine fails UCI handshake

Return a clear server-side configuration error.

### Runtime engine errors

- Process exits unexpectedly
- Timeout
- Invalid engine response
- Position analysis fails
- Engine cannot be closed cleanly

Persist the run as failed.

### Data errors

- Game not found
- No moves and zero-move support not implemented
- Missing FEN
- Invalid FEN
- Noncontiguous plies
- Move-count mismatch
- Missing user color

Do not launch Stockfish when stored game data fails validation.

### API/client behavior

- Duplicate click while request is running should not start another analysis.
- Refresh after completion should load persisted analysis.
- Refresh after failure should show failed state.
- A stale `running` run after backend termination may remain unresolved in Sprint 4.

Stale-run recovery belongs to Sprint 5. Document this limitation rather than building a partial job system now.

---

## 13. Security and resource boundaries

Even though this remains a local single-user application:

- Never accept client-supplied engine paths.
- Never accept arbitrary UCI commands.
- Never accept arbitrary engine options from the client.
- Never execute shell command strings assembled from request data.
- Use the configured executable path directly through `python-chess`.
- Keep Threads and Hash bounded.
- Use a sensible per-position or per-game timeout if supported cleanly.
- Sanitize errors before returning them to the frontend.
- Do not expose environment variables.
- Do not expose full local filesystem paths unnecessarily.

Authentication is not required for Sprint 4 because the application is still local and single-user.

Authentication should become a prerequisite before:

- Deployment
- Shared access
- Remote engine compute
- Multi-user history
- Publicly reachable APIs

---

## 14. Test matrix

## 14.1 Unit tests

### Engine normalization

- Positive White CP
- Negative White CP
- White mate
- Black mate
- Best move UCI
- Engine metadata
- Cleanup on success
- Cleanup on failure

### Perspective conversion

- White user receives canonical sign
- Black user receives inverted CP
- Black user receives inverted mate

### Position construction

- Zero moves
- One move
- Multiple moves
- Noncontiguous plies
- Missing FEN
- Invalid FEN
- Move-count mismatch

## 14.2 Repository tests

- Create run
- Load latest run
- Save ordered evaluations
- CP row
- Mate row
- Duplicate ply rejection
- Complete transition
- Failed transition
- Delete game cascade
- Delete upload cascade

## 14.3 Service tests

- Full successful fake-engine run
- Engine startup failure
- Mid-game failure
- No partial complete run
- Retry after failure
- Reject duplicate running request
- Completed result survives new DB session

## 14.4 API tests

- Game not found
- Not analyzed response
- Successful POST
- Complete GET
- Running conflict
- Failure response
- Stockfish unavailable response
- Client cannot submit FENs/evaluations/options
- List endpoints remain lightweight

## 14.5 Frontend tests

- Analyze button visibility
- Loading state
- Complete graph
- CP label
- Mate label
- Failure and retry
- Saved-only behavior
- Current-ply synchronization
- Existing lazy move loading regression

## 14.6 Integration tests

- Real Stockfish optional test
- Fresh migration
- Populated migration
- Restart persistence
- Full manual browser flow

---

## 15. Definition of done

Sprint 4 is complete only when all of the following are true.

1. A user can open one persisted game and request analysis.
2. Preview remains stateless and offers no analysis action.
3. The backend analyzes positions `0..N`.
4. A completed run stores exactly `move_count + 1` evaluation rows.
5. Centipawn and mate scores are represented separately.
6. Canonical persisted scores use White's perspective.
7. API/UI scores are correctly normalized to the saved player's perspective.
8. Engine name, version, configured depth, options, and useful provenance are persisted.
9. Analysis state clearly distinguishes:
   - not analyzed
   - running
   - complete
   - failed
10. Failed analysis does not appear complete.
11. A retry creates a new analysis attempt.
12. The UI renders an evaluation graph for a completed run.
13. Mate scores are shown as mate, not fake centipawn values.
14. The graph stays aligned with the board's current ply.
15. Analysis survives backend and database restart.
16. Deleting an upload removes all related analysis data.
17. Upload import remains one atomic transaction.
18. Preview continues to write zero rows.
19. Existing list endpoints remain free of move and evaluation arrays.
20. Existing opening classification behavior remains unchanged.
21. Existing backend tests pass.
22. Existing frontend tests, lint, and build pass.
23. New backend and frontend tests pass.
24. Alembic builds an empty database from scratch.
25. Alembic upgrades the populated development database safely.
26. `alembic check` reports no pending model changes.
27. At least one real saved game is demonstrated end to end using Stockfish.

---

## 16. Recommended implementation order

Execute Sprint 4 in this order:

```text
1. Targeted code inspection
2. Engine configuration and adapter
3. Engine adapter unit tests
4. Analysis models and migration
5. Repository functions and repository tests
6. Position-building and analysis coordinator
7. Fake-engine service tests
8. Analysis API
9. API tests
10. Frontend API types and wrappers
11. Analysis status UI
12. Evaluation graph
13. Frontend tests
14. Real Stockfish integration test
15. Fresh/populated migration verification
16. Full regression suite
17. Manual end-to-end acceptance
18. Commit and push
```

Do not begin frontend graph work before the backend's persisted response contract is stable.

---

## 17. Suggested staged acceptance log

After each checkpoint, record evidence in the same style used for earlier sprint acceptance.

### Checkpoint 1

```text
Engine adapter:
- Fake-engine tests:
- CP normalization:
- Mate normalization:
- Cleanup verified:
```

### Checkpoint 2

```text
Migration:
- Fresh DB:
- Populated DB:
- Cascade:
- Alembic check:
```

### Checkpoint 3

```text
Analysis service:
- Successful fake run:
- Failure transition:
- Expected N+1 evaluations:
- Retry:
```

### Checkpoint 4

```text
API:
- Not analyzed:
- Complete:
- Failed:
- Stockfish unavailable:
- Existing list responses unchanged:
```

### Checkpoint 5

```text
Frontend:
- Analyze action:
- Loading:
- Graph:
- CP:
- Mate:
- Retry:
- Board synchronization:
```

### Checkpoint 6

```text
Acceptance:
- Real Stockfish game:
- Restart persistence:
- Backend tests:
- Frontend tests:
- Build:
- Lint:
- Migration from empty:
```

---

## 18. Guidance for Claude Code

Before modifying files:

1. Read `PROJECT-STATUS.md`.
2. Inspect the exact backend/frontend files identified in the targeted recheck.
3. Report any existing architectural detail that conflicts with this plan.
4. Preserve established naming and transaction conventions.
5. Implement one checkpoint at a time.
6. Run focused tests after each checkpoint.
7. Do not silently expand Sprint 4 into Sprint 5.
8. Do not weaken current invariants to simplify engine work.
9. Do not make the ordinary test suite depend on a local Stockfish installation.
10. At completion, produce an itemized acceptance report with file references, test commands, and results.

If a design choice must change, explain:

```text
- What existing code made the planned design unsuitable
- What alternative was chosen
- Which invariant remains protected
- Whether the change affects Sprint 5
```

The goal is not merely to display an engine graph.

The goal is to establish a durable engine-analysis foundation that Sprint 5 can move into a persistent job system without replacing the core storage format, score semantics, provenance model, or frontend response contract.
