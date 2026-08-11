# Sprint 6 acceptance checklist

Move quality, upload-level weakness summaries, and grounded training
recommendations, all derived on read from evaluations Sprint 5 already stored.
Run this to confirm the sprint still holds after any change to the derivation,
the aggregation, the recommendation rules, or the evidence contract.

Nothing in this sprint re-runs Stockfish, touches the worker, adds a migration,
or calls a model.

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

Expected: **618 passed** with an engine configured, **612 passed / 6 skipped**
without one, **138** frontend tests, `alembic check` clean, lint and build
exit 0.

Check the exit status of each command **directly**, never through a pipe —
`npm run lint | tail` reports `tail`'s status, which has already produced one
false "clean" report on this project.

The engine-free run matters as much as the other: the ordinary suite must never
depend on a Stockfish binary being installed. In PowerShell, `$env:STOCKFISH_PATH=""`
**deletes** the variable rather than emptying it; use a shell where an empty
string stays an empty string.

## Manual

Start everything with `.\dev.ps1` (backend, worker, frontend), open a saved
analysis with at least one fully analysed game, and work down the list.

| # | Check | Expected |
|---|---|---|
| 1 | Open a game that has never been analysed | No move-quality panel, no error — an ungraded game is a normal state |
| 2 | Analyse it and let it finish | Move quality appears without a reload |
| 3 | Read the summary card | Average, median, and both denominators shown; nothing reads as a percentage of the wrong total |
| 4 | Step through the move list | Inaccuracies, mistakes and blunders carry `?!` / `?` / `??`; good moves carry nothing |
| 5 | Find a move you know was strong | Not flagged. Matching the engine exactly says "Matched the engine" |
| 6 | Open a game you **lost by checkmate** | The mating move is *not* a missed mate; it reads as a mate you allowed |
| 7 | Open a game you **won by checkmate** | Your mating move is not flagged as an error of any kind |
| 8 | Set the filter to Mistakes, then Critical | The list shortens; the board does not move on its own |
| 9 | Press Next issue repeatedly | Walks your problem moves in order and stops at the last one, disabled |
| 10 | Press Previous issue from the first | Disabled, not wrapping to the end |
| 11 | Open a game where you played Black | Scores read from *your* side: an advantage for you is positive |
| 12 | Read the Training insights coverage line | "Based on N of M games" where M is the whole upload |
| 13 | Analyse one more game in that upload | The insights refresh themselves once the batch settles |
| 14 | Read a recommendation | It names its sample and links to real positions from *this* upload |
| 15 | Click a linked position | Opens that game at that move, board and move list both on it |
| 16 | Reload on that URL | Same game, same move — the position is in the address bar |
| 17 | Read "How these are worked out" | States the fixed thresholds and that nothing here understands chess |
| 18 | Look at an upload with only a couple of analysed games | Fewer recommendations, or none, rather than confident advice |
| 19 | Stop the backend, then reload the dashboard | An error message, not a blank panel or a spinner that never ends |
| 20 | Check every table's headings | Each heading sits above the column it names |

Checks 6 and 7 are the ones to run first if you only run two. They are the
sprint's central correctness risk, and both fail loudly and identically when the
mate-parity rule is wrong.

## Invariants this sprint added

Carrying on the numbering from Sprint 5's eighteen:

19. Move quality is **derived on read**. No derived-data table, no migration,
    and no second copy of a number that can drift from the evaluation it came
    from.
20. A mate is never converted into a centipawn number, at any layer — not in
    the derivation, an average, a sort key, a recommendation, or the evidence
    payload.
21. `mate_in_white = 0` is attributed by **ply parity**, never by a sign. The
    side that moved into the position delivered the mate.
22. Centipawn loss is clamped at zero: improving your position is never
    recorded as a negative loss.
23. Only the saved player's moves are graded. The opponent's are not derived,
    not counted, and not returned.
24. A game whose saved name matched neither player is `not_gradable`, and is
    never silently graded as White.
25. Every aggregate's denominator is **completed, gradable games only**, and
    every figure is displayed beside the sample it was computed over.
26. Thresholds, sample floors, and every other constant are server-owned. None
    is a request parameter, for the same reason a depth or an engine path is
    not.
27. A recommendation fires only when its sample floor *and* its meaningfulness
    gate are both met, and it cites specific positions from the upload it was
    asked about.
28. Recommendation wording claims no chess motif — no forks, no pawn structure,
    no "you struggle with endgames". Enforced by a denylist test.
29. Ordering is total. Two equally severe moves cannot swap places between two
    requests for the same data.
30. Every read endpoint added this sprint is read-only: no job enqueued, no row
    inserted, no run modified, no engine started.
31. The evidence payload contains no filesystem path, no engine binary
    location, no database URL, and no environment value.
32. Facts and generated prose are structurally separated. There is nowhere
    inside an evidence item to put a sentence.

## Tests that must be able to fail

Sprint 5's three protected mechanisms are unchanged and still required to fail
when broken: `FOR UPDATE SKIP LOCKED`, background lease renewal, bounded
concurrency. See `docs/sprint-5-acceptance.md`. **They must not be rewritten to
make a Sprint 6 test easier**, and CP10 re-confirmed all three by breaking them
again.

Sprint 6 adds seven. Each was verified by deliberately breaking the code and
watching the named test go red:

| Mechanism | Break it by | Test must fail |
|---|---|---|
| Player perspective | flipping the sign in `normalize_score` | Black-perspective centipawn-loss tests |
| Centipawn-loss clamp | dropping the `max(0, ...)` | `test_improving_the_evaluation_is_clamped_to_zero_not_negative` |
| Threshold boundaries | making `>=` into `>` at 200 | `test_threshold_boundaries[200-blunder]`, with 199 still green |
| **Mate-zero ply parity** | assigning a fixed side instead | the White-mates tests **and** the Black-mates tests |
| Aggregate denominator | folding unanalysed games in | `test_the_denominator_is_complete_games_only` |
| Recommendation floors | removing the sample floor or the meaningfulness gate | `test_one_game_is_never_enough`, `test_a_small_relative_gap_is_not_reported` |
| Mate kept off the number line | flattening mate to ±10000 | `test_no_transition_involving_mate_produces_a_centipawn_number` |

The mate-parity row is the one to check both ways. A single fixed sign is right
by accident for one colour, so `mater_is_white = True` and `= False` must each
be tried; if only one direction fails, half the mechanism is untested.

## Verified against real data, not only fixtures

Every backend checkpoint was run against the developer's own library as well as
its fixtures. That caught two design errors fixtures did not:

- The opening rule originally named the weakest bucket that met its sample
  floors, which on real data produced *"spend your next session on the
  Accelerated Dragon"* from **2 games at ACPL 24 against a library average of
  20** — four centipawns of noise presented as a finding. The meaningfulness
  gate (≥5cp **and** ≥20% relative) exists because of that.
- The evidence contract's "no free text" test was first written as a heuristic
  (at most five spaces, no trailing full stop). It passed on fixtures. The real
  library contains `English Opening: Symmetrical Variation, Anti-Benoni
  Variation, Spielmann Defense` — 80 characters, eight words — so the heuristic
  would have rejected legitimate stored data. It was replaced with a complete
  field list naming the source of every field.

Current library state: **3 uploads, 79 games, 79 analysed, 7,051 position
evaluations.** All2023 (56 games) is the only upload with enough data to
produce a second recommendation.

## Still deferred, deliberately

Unchanged by this sprint and still not defects:

- **Re-analysis at another depth.** The data model supports it; no UI offers
  it. Deferred a third time.
- **Deployment and containerization.** Local development only. `STOCKFISH_PATH`
  is a Windows `.exe`, and containerizing would fork the engine identity that
  analysis reuse depends on.
- **Generative-AI explanations.** CP9 fixed the payload's *shape* so a later
  sprint can add them without the model recomputing anything. Nothing calls a
  model, and `EvidenceEnvelope.explanation` is always null.
