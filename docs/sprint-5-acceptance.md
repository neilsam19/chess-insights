# Sprint 5 acceptance checklist

Durable, asynchronous, bounded engine analysis. Run this to confirm the sprint
still holds after any change to the worker, the queue, or the analysis API.

## Automated

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

Expected: **376 passed** with an engine configured, **370 passed / 6 skipped**
without one (`STOCKFISH_PATH=""`), **54** frontend tests, `alembic check` clean,
lint and build exit 0.

The engine-free run matters as much as the other: the ordinary suite must never
depend on a Stockfish binary being installed.

## Manual

Start everything with `.\dev.ps1` (backend, worker, frontend).

| # | Check | Expected |
|---|---|---|
| 1 | Press **Analyze Game** on an unanalysed game | Returns instantly to "Queued", never blocks on the engine |
| 2 | Restart the API while a game is being analysed | Analysis continues; the worker is unaffected |
| 3 | Hard-kill the worker mid-game | Job stays `running` with an expiring lease |
| 4 | Restart the worker | Logs `recovered 1 abandoned job(s)`, requeues, re-runs from ply 0 |
| 5 | Cancel a running game | Says "Cancelling" at once, stops within a position or two, stores **zero** evaluations |
| 6 | Press **Analyze All Games** on an upload | One batch, aggregate progress, **one** engine process regardless of game count |
| 7 | Press it again | Reports games skipped / already queued; queues nothing new |
| 8 | Reload the browser mid-analysis | Progress recovers from the server |
| 9 | Let a game finish | Evaluation graph renders and stays synchronised with the board |
| 10 | `Ctrl+C` the worker mid-game | Finishes the in-flight game, then exits |

## Invariants this sprint added

1. A committed job exists before an enqueue response is returned.
2. Only the worker executes Stockfish analysis.
3. A job is owned by at most one unexpired worker lease.
4. Engine execution never holds a long database transaction.
5. Failed or cancelled attempts store zero position evaluations.
6. A completed run is immutable.
7. Progress is monotonic within an attempt.
8. Clients cannot set job state, engine identity, progress, or results.
9. Concurrency is bounded by server configuration.
10. Matching active jobs are not duplicated.
11. Matching completed runs are reused or explicitly skipped.
12. Bulk resumability is at game granularity; interrupted games restart at ply 0.

## Tests that must be able to fail

Three mechanisms are invisible when working and catastrophic when silently
broken, so each has a test that was checked by deliberately breaking the code:

| Mechanism | Break it by | Test should |
|---|---|---|
| `FOR UPDATE SKIP LOCKED` | removing `skip_locked=True` | fail with a statement timeout, not hang |
| Background lease renewal | setting `heartbeat_seconds=0` | fail: the job is swept while healthy |
| Bounded concurrency | spawning more slots than configured | fail: peak engine count exceeds the limit |

If any of these ever passes after such a change, the test has stopped testing
anything.
